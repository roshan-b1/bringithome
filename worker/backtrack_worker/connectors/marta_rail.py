"""MARTA rail positions from the rail realtime arrivals API.

The API returns predicted arrivals (one row per train per upcoming station),
not vehicle positions. Two URLs exist and they carry different fields
(checked Oct 8 2026, rows align one to one):

  keyed  /itsmarta/railrealtimearrivals/traindata?apiKey=...      checks the key
         adds TRIP_ID, TRACK_CIRCUIT, TRACK_LOCATION, IS_END_OF_SERVICE ...
         has NO latitude/longitude
  open   /itsmarta/railrealtimearrivals/developerservices/traindata
         adds LATITUDE, LONGITUDE, DELAY; currently accepts any key

We poll both and merge per train: position from `open`, run id and track
circuit from `keyed`. If one URL fails we still record what the other gives
(and log it). One row per realtime TRAIN_ID per poll, located at its next
station (the row with the smallest WAITING_SECONDS).

`ts` is when we observed the train (the poll time). MARTA's EVENT_TIME often
stays frozen for minutes while the prediction moves on, so it is stored
separately as `event_time` instead of being the key: every poll keeps its own
next-station and ETA. EVENT_TIME is Atlanta local with no zone. The API has
no CORS, so it is only ever called from the server.
"""

from __future__ import annotations

import hashlib
import json
import urllib.parse
from datetime import datetime

from ..redact import describe
from ..rows import ParseResult, PositionRow, clamp_int4, clean_text, plausible
from ..timeutil import parse_marta_rail_time
from .base import FetchError, FetchResult, HttpGet, http_get
from .rail_stations import station_id_for

BASE = "https://developerservices.itsmarta.com:18096/itsmarta/railrealtimearrivals"
KEYED_URL = BASE + "/traindata"
OPEN_URL = BASE + "/developerservices/traindata"

DIRECTIONS = {"N", "S", "E", "W"}


def _int(value) -> int | None:
    try:
        return clamp_int4(int(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _float(value) -> float | None:
    try:
        f = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # drop NaN


class MartaRailConnector:
    name = "marta-rail"
    mode = "rail"
    archive_ext = "json"

    def __init__(self, api_key: str | None, poll_s: float = 20, timeout_s: float = 15,
                 http: HttpGet = http_get, keyed_url: str = KEYED_URL, open_url: str = OPEN_URL):
        self.api_key = api_key
        self.poll_s = poll_s
        self.timeout_s = timeout_s
        self._http = http
        self.keyed_url = keyed_url
        self.open_url = open_url
        self._last_digest: str | None = None

    def _get_list(self, url: str) -> list:
        # The key rides in the query string, so it is URL-encoded (a stray space
        # or '#' can't break the request) and the URL never goes into an error.
        full = f"{url}?{urllib.parse.urlencode({'apiKey': self.api_key})}" if self.api_key else url
        resp = self._http(full, {"Accept": "application/json"}, self.timeout_s)
        if resp.status != 200:
            raise FetchError(f"HTTP {resp.status}")
        try:
            data = json.loads(resp.body)
        except ValueError as e:
            raise FetchError("body is not JSON") from e
        if isinstance(data, dict):  # e.g. {"ErrorCode":"400","ErrorMessage":"Invalid APIKey"}
            raise FetchError(f"API error: {data.get('ErrorMessage') or data}")
        if not isinstance(data, list):
            raise FetchError("unexpected JSON shape")
        return data

    def fetch(self, polled_at: datetime) -> FetchResult:
        doc: dict = {"polled_at": polled_at.isoformat(), "keyed": None, "open": None}
        errors: dict[str, str] = {}
        sources = {"open": self.open_url}
        if self.api_key:
            sources["keyed"] = self.keyed_url
        else:
            errors["keyed"] = "no MARTA_RAIL_API_KEY set"
        for kind, url in sources.items():
            try:
                doc[kind] = self._get_list(url)
            except FetchError as e:
                errors[kind] = str(e)
            except Exception as e:  # noqa: BLE001  one URL failing must not discard the other
                errors[kind] = describe(e)
        if doc["keyed"] is None and doc["open"] is None:
            raise FetchError("; ".join(f"{k}: {v}" for k, v in errors.items()))
        if errors:
            doc["errors"] = errors

        # The API refreshes about every 21 s. Skip identical payloads so the
        # archive only holds real updates.
        digest = hashlib.sha256(
            json.dumps([doc["keyed"], doc["open"]], sort_keys=True).encode()
        ).hexdigest()
        if digest == self._last_digest:
            return FetchResult(changed=False, polled_at=polled_at, notes=errors)
        self._last_digest = digest
        payload = json.dumps(doc, separators=(",", ":")).encode()
        return FetchResult(changed=True, payload=payload, polled_at=polled_at, notes=errors)

    def parse(self, payload: bytes, polled_at: datetime) -> ParseResult:
        doc = json.loads(payload)
        # per train, per source: (waiting_seconds, row) for the soonest station
        best: dict[str, dict[str, tuple[int, dict]]] = {}
        position: dict[str, tuple[float, float]] = {}
        for kind in ("open", "keyed"):
            for r in doc.get(kind) or []:
                if not isinstance(r, dict):
                    continue
                if str(r.get("IS_REALTIME", "")).strip().lower() != "true":
                    continue  # schedule-only rows name no train
                tid = clean_text(r.get("TRAIN_ID"))
                if not tid:
                    continue
                wait = _int(r.get("WAITING_SECONDS"))
                wait_key = wait if wait is not None else 10**9
                slot = best.setdefault(tid, {})
                if kind not in slot or wait_key < slot[kind][0]:
                    slot[kind] = (wait_key, r)
                if kind == "open" and tid not in position:
                    lat, lon = _float(r.get("LATITUDE")), _float(r.get("LONGITUDE"))
                    if (lat is not None and lon is not None and not (lat == 0 and lon == 0)
                            and -90 <= lat <= 90 and -180 <= lon <= 180):
                        position[tid] = (lat, lon)

        rows: list[PositionRow] = []
        unknown_stations: set[str] = set()
        bad_times = 0
        newest_event: datetime | None = None
        for tid, slot in best.items():
            primary = (slot.get("open") or slot.get("keyed"))[1]
            keyed = slot.get("keyed", (None, {}))[1]
            wait, nxt = min(slot.values(), key=lambda x: x[0])

            event_time = None
            raw_time = primary.get("EVENT_TIME") or keyed.get("EVENT_TIME")
            try:
                event_time = parse_marta_rail_time(str(raw_time), near=polled_at)
            except (TypeError, ValueError):
                pass
            if not plausible(event_time, polled_at):
                event_time = None
                bad_times += 1
            elif newest_event is None or event_time > newest_event:
                newest_event = event_time

            station_name = clean_text(nxt.get("STATION"))
            station_id = station_id_for(station_name)
            if station_name and station_id is None:
                unknown_stations.add(station_name)
            direction = (clean_text(primary.get("DIRECTION")) or "").upper()
            pos = position.get(tid)

            rows.append(PositionRow(
                ts=polled_at,
                mode=self.mode,
                vehicle_id=tid,
                source=self.name,
                route_short=(clean_text(primary.get("LINE")) or "").upper() or None,
                lat=pos[0] if pos else None,
                lon=pos[1] if pos else None,
                direction=direction if direction in DIRECTIONS else None,
                trip_id=clean_text(keyed.get("TRIP_ID")),
                next_station=station_name,
                station_id=station_id,
                next_eta_s=wait if wait != 10**9 else None,
                destination=clean_text(primary.get("DESTINATION")),
                track_circuit=clean_text(keyed.get("TRACK_CIRCUIT")),
                event_time=event_time,
                polled_at=polled_at,
            ))
        warnings = [f"unknown station name: {n}" for n in sorted(unknown_stations)]
        if bad_times:
            warnings.append("train(s) with a missing or implausible EVENT_TIME (kept, event_time empty)")
        # freshness = MARTA's own newest EVENT_TIME, so a frozen feed shows as stale
        return ParseResult(rows=rows, feed_ts=newest_event, skipped=0, warnings=tuple(warnings))
