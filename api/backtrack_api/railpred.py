"""Rail predictions from the raw archive: where a train is going next, and when.

MARTA's rail API answers with predicted arrivals, one row per (train, upcoming station). The
recorder archives every poll exactly as it came (data/raw/marta-rail/<UTC date>/<HHMMSS>_<epochms>
.json.gz), so the newest archived poll at or before t IS the prediction table at t. Reading it
from disk serves live (t = now: the newest file) and as_of (t = the replayed moment) with one
code path, and nothing here can read a live-only source (ARCHITECTURE.md decision 8, 7.7).

Per train:
  - only IS_REALTIME == "true" rows (schedule-only rows name no train);
  - the keyed and open URLs carry the same predictions, so rows are merged per (train, station),
    keeping the smaller WAITING_SECONDS (an earlier arrival is the safer one to plan a meet on);
  - eta = that file's poll time + WAITING_SECONDS, never now + WAITING_SECONDS, so a poll that
    is 40 s old still gives the right clock time;
  - the list ends at DESTINATION (a short turn ends early), then drops anything before t;
  - DELAY is never read.

Names go through rail_station_map (the loaded schedule, via the place index), then the
connector's own normalizer. A name neither knows becomes place_id "rail_name:<slug>" (for
example "rail_name:new-town") with a label made from the raw name. That prefix is not a Place
kind, so index.place() returns None for it and the meet check, which needs mapped stations
(decision 9), leaves it out, while the rider still sees the stop.
"""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable

from backtrack_worker.archive import RawArchive
from backtrack_worker.connectors.rail_stations import RAIL_STATIONS, norm_api_name, station_id_for
from backtrack_worker.rows import clean_text
from backtrack_worker.static_gtfs import display_name

SOURCE = "marta-rail"
MAX_AGE_S = 300                  # decision 8: the newest poll at or before t, within 5 min
UNMAPPED = "rail_name:"          # place_id prefix for a station name nothing maps
FILE_CACHE_SIZE = 16             # /now and /trains/nearby poll every 10 s; files arrive every ~20 s


# ----------------------------------------------------------------------------- names

class StationNames:
    """Rail API spellings (STATION and DESTINATION) -> GTFS parent station and display name."""

    def __init__(self, parents: dict[str, str] | None = None, labels: dict[str, str] | None = None):
        self._parents: dict[str, str] = {}
        for name, parent in (parents or {}).items():
            self._parents.setdefault(name, parent)
            self._parents.setdefault(norm_api_name(name), parent)
        self._labels = dict(labels or {})

    @classmethod
    def from_index(cls, index) -> StationNames:
        """From the place index the API already holds (its rail_station_map and station labels)."""
        return cls(getattr(index, "station_map", {}), {p: s.label for p, s in getattr(index, "stations", {}).items()})

    @classmethod
    def from_rows(cls, rows: Iterable[dict]) -> StationNames:
        """From rail_station_map rows (api_name, parent_stop_id, display_name)."""
        rows = list(rows)
        return cls({r["api_name"]: r["parent_stop_id"] for r in rows},
                   {r["parent_stop_id"]: r["display_name"] for r in rows})

    def parent(self, name: str | None) -> str | None:
        if not name:
            return None
        found = self._parents.get(name) or self._parents.get(norm_api_name(name))
        return found or station_id_for(name)   # same normalization the table was built with

    def label(self, name: str | None) -> str | None:
        if not name:
            return None
        parent = self.parent(name)
        if parent is not None:
            if parent in self._labels:
                return self._labels[parent]
            if parent in RAIL_STATIONS:
                return display_name(RAIL_STATIONS[parent][0])
        return display_name(norm_api_name(name))

    def place_id(self, name: str) -> str:
        parent = self.parent(name)
        if parent is not None:
            return f"station:{parent}"
        slug = norm_api_name(name).removesuffix(" STATION").lower().replace(" ", "-")
        return f"{UNMAPPED}{slug}"


def _same_station(names: StationNames, a: str, b: str) -> bool:
    pa, pb = names.parent(a), names.parent(b)
    if pa is not None and pb is not None:
        return pa == pb
    return norm_api_name(a).removesuffix(" STATION") == norm_api_name(b).removesuffix(" STATION")


# ----------------------------------------------------------------------------- results

@dataclass(frozen=True, slots=True)
class RailStop:
    place_id: str                # "station:<parent_stop_id>", or "rail_name:<slug>" when unmapped
    label: str                   # display name, e.g. "Five Points"
    eta: datetime                # file poll time + WAITING_SECONDS (UTC)
    station: str                 # the API's raw STATION name


@dataclass(frozen=True, slots=True)
class TrainPrediction:
    train_id: str
    line: str | None             # RED | GOLD | BLUE | GREEN
    direction: str | None        # N | S | E | W
    destination: str | None      # raw DESTINATION, e.g. "Hamilton E. Holmes"
    destination_place_id: str | None
    destination_label: str | None
    stops: tuple[RailStop, ...]  # upcoming, soonest first, ending at DESTINATION

    @property
    def next_stop(self) -> RailStop | None:
        return self.stops[0] if self.stops else None


@dataclass(frozen=True, slots=True)
class RailSnapshot:
    polled_at: datetime          # the poll this snapshot is (file name time)
    path: Path
    trains: dict[str, TrainPrediction]


# ----------------------------------------------------------------------------- parsing (cached per file)

@dataclass(frozen=True, slots=True)
class _ParsedTrain:
    line: str | None
    direction: str | None
    destination: str | None
    waits: tuple[tuple[str, int], ...]   # (raw STATION, WAITING_SECONDS), soonest first, one per station


def _int(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse_rail_payload(payload: bytes) -> dict[str, _ParsedTrain]:
    """One archived poll -> per TRAIN_ID, its realtime predictions, merged across both URLs."""
    doc = json.loads(payload)
    attrs: dict[str, tuple[str | None, str | None, str | None]] = {}
    waits: dict[str, dict[str, tuple[str, int]]] = {}
    for kind in ("open", "keyed"):   # open first: its LINE/DIRECTION/DESTINATION win, as in the connector
        for r in doc.get(kind) or []:
            if not isinstance(r, dict) or str(r.get("IS_REALTIME", "")).strip().lower() != "true":
                continue
            tid, station, wait = clean_text(r.get("TRAIN_ID")), clean_text(r.get("STATION")), _int(r.get("WAITING_SECONDS"))
            if not tid or not station or wait is None:
                continue
            line = (clean_text(r.get("LINE")) or "").upper() or None
            direction = (clean_text(r.get("DIRECTION")) or "").upper() or None
            attrs.setdefault(tid, (line, direction if direction in ("N", "S", "E", "W") else None,
                                   clean_text(r.get("DESTINATION"))))
            per_station = waits.setdefault(tid, {})
            key = norm_api_name(station)
            if key not in per_station or wait < per_station[key][1]:
                per_station[key] = (station, wait)
    return {tid: _ParsedTrain(*attrs[tid], tuple(sorted(per_station.values(), key=lambda sw: (sw[1], sw[0]))))
            for tid, per_station in waits.items()}


class RailFileCache:
    """Parsed archive files by path, in a small LRU. Archive files are written once with an
    atomic rename and never change, so the path alone is the key."""

    def __init__(self, maxsize: int = FILE_CACHE_SIZE):
        self.maxsize = maxsize
        self._items: OrderedDict[Path, dict[str, _ParsedTrain]] = OrderedDict()
        self._lock = threading.Lock()
        self.parses = 0

    def get(self, path: Path, load: Callable[[Path], dict[str, _ParsedTrain]]) -> dict[str, _ParsedTrain]:
        with self._lock:
            if path in self._items:
                self._items.move_to_end(path)
                return self._items[path]
        parsed = load(path)   # outside the lock: a slow disk never blocks other readers
        with self._lock:
            self.parses += 1
            self._items[path] = parsed
            self._items.move_to_end(path)
            while len(self._items) > self.maxsize:
                self._items.popitem(last=False)
        return parsed


_FILES = RailFileCache()


def _load(path: Path) -> dict[str, _ParsedTrain]:
    return parse_rail_payload(RawArchive.read(path))


# ----------------------------------------------------------------------------- the read

def rail_predictions_at(data_dir: Path, t: datetime, *, max_age_s: int = MAX_AGE_S,
                        names: StationNames | None = None, cache: RailFileCache | None = None) -> RailSnapshot | None:
    """The newest archived rail poll with polled_at <= t and t - polled_at <= max_age_s, as
    per-train predictions with ETAs at or after t. None when no such file exists (then the
    caller shows no next stations, and the meet check says no_eta)."""
    if t.tzinfo is None:
        raise ValueError("t must be timezone-aware")
    names = names or StationNames()
    cache = cache or _FILES
    archive = RawArchive(Path(data_dir))
    since = t - timedelta(seconds=max_age_s)
    files = list(archive.iter_files(SOURCE, since=since, until=t + timedelta(microseconds=1)))
    for path in reversed(files):              # newest first; an unreadable file falls back to the one before
        try:
            parsed = cache.get(path, _load)
        except Exception:  # noqa: BLE001  the sync thread logs unreadable payloads
            continue
        polled_at = archive.polled_at_of(path)
        return RailSnapshot(polled_at, path, {tid: _prediction(tid, p, polled_at, t, names)
                                              for tid, p in parsed.items()})
    return None


def _prediction(tid: str, p: _ParsedTrain, polled_at: datetime, t: datetime, names: StationNames) -> TrainPrediction:
    stops: list[RailStop] = []
    for station, wait in p.waits:
        stops.append(RailStop(names.place_id(station), names.label(station) or station,
                              polled_at + timedelta(seconds=wait), station))
        if p.destination and _same_station(names, station, p.destination):
            break   # valid up to DESTINATION: anything after it is the next trip's guess
    return TrainPrediction(
        train_id=tid, line=p.line, direction=p.direction, destination=p.destination,
        destination_place_id=names.place_id(p.destination) if p.destination else None,
        destination_label=names.label(p.destination),
        stops=tuple(s for s in stops if s.eta >= t))
