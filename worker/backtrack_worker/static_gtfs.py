"""Static GTFS loader: MARTA's schedule zip -> the gtfs_* tables, rail helpers and place aliases.

Two halves:

- build_feed() is a pure transform with no database. It picks the services
  that run in a date window (calendar + calendar_dates), keeps only their
  trips, turns times into integer seconds (' 4:40:00' -> 16800, '26:11:00' ->
  94260), and derives what the matcher and resolver need:
    gtfs_stop_routes   which route + direction (and headsign) serves each stop
    rail_platforms     ordered platforms per line + direction, from the longest trip
    rail_station_map   every rail API spelling (STATION and DESTINATION) -> parent station
    place_aliases      hand-written names ("gsu", "the airport") + every station name
  stop_times (about a million rows) is streamed from the zip, never held in memory.

- write_feed() replaces every static table in ONE transaction (TRUNCATE + COPY),
  so the API never sees a half-loaded schedule.
"""

from __future__ import annotations

import csv
import re
import hashlib
import io
import math
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Iterator

from .connectors.rail_stations import (API_ALIASES, KNOWN_DESTINATIONS, RAIL_STATIONS, norm_api_name,
                                       station_id_for)
from .places import normalize_place
from .timeutil import gtfs_time_to_seconds

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ZIP = REPO_ROOT / "data" / "static" / "google_transit.zip"
ALIAS_SEEDS = REPO_ROOT / "db" / "seeds" / "place_aliases.csv"
MARTA_GTFS_URL = "https://itsmarta.com/google_transit_feed/google_transit.zip"

# direction_id 0 / 1 as letters, per line (verified on all 4 lines, PREP-REPORT section 3)
RAIL_DIRECTIONS = {"RED": ("N", "S"), "GOLD": ("N", "S"), "BLUE": ("E", "W"), "GREEN": ("E", "W")}


def _reader(zf: zipfile.ZipFile, name: str) -> Iterator[dict[str, str]]:
    """Rows of one GTFS file with keys and values stripped (MARTA pads times with spaces)."""
    try:
        f = zf.open(name)
    except KeyError:
        return
    with f:
        rows = csv.reader(io.TextIOWrapper(f, "utf-8-sig", newline=""))
        header = [h.strip() for h in next(rows, [])]
        for raw in rows:
            if not raw:
                continue
            yield {k: (raw[i].strip() if i < len(raw) else "") for i, k in enumerate(header)}


def _date(text: str) -> date:
    return date(int(text[:4]), int(text[4:6]), int(text[6:8]))


def active_on(d: date, calendar: dict[str, dict], exceptions: dict[tuple[str, date], int]) -> set[str]:
    """The service_ids running on date d (mirrors the SQL active_services function)."""
    weekday = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")[d.weekday()]
    out = {sid for sid, c in calendar.items()
           if c["start"] <= d <= c["end"] and c[weekday] and exceptions.get((sid, d)) != 2}
    out |= {sid for (sid, day), kind in exceptions.items() if day == d and kind == 1}
    return out


def _haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371008.8 * math.asin(math.sqrt(a))


def display_name(gtfs_name: str) -> str:
    """'LAKEWOOD-FT MCPHERSON STATION' -> 'Lakewood-Ft McPherson'."""
    name = gtfs_name.removesuffix(" STATION").title()
    for wrong, right in (("Sec ", "SEC "), ("Mcpherson", "McPherson"), ("H E ", "H.E. ")):
        name = name.replace(wrong, right)
    return name


@dataclass
class StaticFeed:
    source: str
    sha256: str
    window_start: date
    window_end: date
    service_ids: list[str]
    feed_start: date | None
    feed_end: date | None
    routes: list[dict]
    stops: dict[str, dict]
    calendar: list[dict]
    calendar_dates: list[dict]
    trips: dict[str, dict]
    shapes: dict[str, list[tuple[float, float]]]
    stop_routes: list[dict]
    rail_platforms: list[dict]
    rail_station_map: list[dict]
    place_aliases: list[dict]
    stop_time_count: int
    warnings: list[str] = field(default_factory=list)
    _zip_path: Path | None = None

    @property
    def counts(self) -> dict[str, int]:
        return {
            "routes": len(self.routes), "stops": len(self.stops), "trips": len(self.trips),
            "stop_times": self.stop_time_count, "shapes": len(self.shapes),
            "stop_routes": len(self.stop_routes), "rail_platforms": len(self.rail_platforms),
            "rail_station_map": len(self.rail_station_map), "place_aliases": len(self.place_aliases),
        }

    def stop_times(self) -> Iterator[tuple]:
        """(trip_id, stop_sequence, stop_id, arr_s, dep_s, timepoint) for kept trips, streamed."""
        with zipfile.ZipFile(self._zip_path) as zf:
            for r in _reader(zf, "stop_times.txt"):
                row = _stop_time_row(r, self.trips, self.stops)
                if row is not None:
                    yield row


def _stop_time_row(r: dict, trips: dict, stops: dict) -> tuple | None:
    if r.get("trip_id") not in trips or r.get("stop_id") not in stops:
        return None
    arr, dep = r.get("arrival_time") or r.get("departure_time"), r.get("departure_time") or r.get("arrival_time")
    if not arr:
        return None
    try:
        arr_s, dep_s, seq = gtfs_time_to_seconds(arr), gtfs_time_to_seconds(dep), int(r["stop_sequence"])
    except (ValueError, KeyError):
        return None
    tp = r.get("timepoint")
    return (r["trip_id"], seq, r["stop_id"], arr_s, dep_s, None if tp == "" or tp is None else tp == "1")


def build_feed(zip_path: Path = DEFAULT_ZIP, window_start: date | None = None, days: int = 14,
               alias_seeds: Path | None = ALIAS_SEEDS, extra_destinations: set[str] = frozenset(),
               extra_stations: set[str] = frozenset()) -> StaticFeed:
    """Read the zip and derive every static table. No database needed."""
    zip_path = Path(zip_path)
    window_start = window_start or date.today() - timedelta(days=1)
    window_end = window_start + timedelta(days=days - 1)
    warnings: list[str] = []
    sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()

    with zipfile.ZipFile(zip_path) as zf:
        # calendar -> which services run in the window
        calendar_rows = list(_reader(zf, "calendar.txt"))
        calendar = {r["service_id"]: {
            "start": _date(r["start_date"]), "end": _date(r["end_date"]),
            **{d: r[d] == "1" for d in ("monday", "tuesday", "wednesday", "thursday",
                                       "friday", "saturday", "sunday")}} for r in calendar_rows}
        cd_rows = list(_reader(zf, "calendar_dates.txt"))
        exceptions = {(r["service_id"], _date(r["date"])): int(r["exception_type"]) for r in cd_rows}
        services: set[str] = set()
        # Start a day early: the day before the window has trips running past midnight into it
        # (a 12:30 AM clue checks yesterday's service at 24:30).
        d = window_start - timedelta(days=1)
        while d <= window_end:
            services |= active_on(d, calendar, exceptions)
            d += timedelta(days=1)
        if not services:
            raise ValueError(f"no service runs between {window_start} and {window_end}: is the feed current?")
        starts = [c["start"] for c in calendar.values()]
        ends = [c["end"] for c in calendar.values()]

        routes = list(_reader(zf, "routes.txt"))
        by_route = {r["route_id"]: r for r in routes}
        shorts = Counter(r["route_short_name"] for r in routes)
        if any(n > 1 for n in shorts.values()):
            warnings.append("route_short_name is not unique: " + ", ".join(s for s, n in shorts.items() if n > 1))

        stops: dict[str, dict] = {}
        for r in _reader(zf, "stops.txt"):
            try:
                lon, lat = float(r["stop_lon"]), float(r["stop_lat"])
            except (KeyError, ValueError):
                warnings.append(f"stop {r.get('stop_id')} has no coordinates; skipped")
                continue
            stops[r["stop_id"]] = {
                "stop_id": r["stop_id"], "stop_name": r["stop_name"], "norm_name": normalize_place(r["stop_name"]),
                "location_type": int(r.get("location_type") or 0), "parent_station": r.get("parent_station") or None,
                "lon": lon, "lat": lat,
            }

        trips: dict[str, dict] = {}
        for r in _reader(zf, "trips.txt"):
            if r["service_id"] not in services:
                continue
            route = by_route.get(r["route_id"])
            if route is None:
                warnings.append(f"trip {r['trip_id']} has unknown route {r['route_id']}; skipped")
                continue
            trips[r["trip_id"]] = {
                "trip_id": r["trip_id"], "route_id": r["route_id"], "route_short_name": route["route_short_name"],
                "route_type": route.get("route_type"), "service_id": r["service_id"],
                "direction_id": int(r["direction_id"]) if r.get("direction_id", "") != "" else None,
                "block_id": r.get("block_id") or None, "shape_id": r.get("shape_id") or None,
                "headsign": r.get("trip_headsign") or None,
            }

        # one streaming pass over stop_times: counts, stop_routes, rail stop sequences
        n_rows = 0
        bad = 0
        served: dict[tuple, Counter] = defaultdict(Counter)
        rail_seq: dict[str, list[tuple[int, str]]] = defaultdict(list)
        stops_by_mode: dict[str, set[str]] = defaultdict(set)   # route_type -> stops it serves
        for r in _reader(zf, "stop_times.txt"):
            trip = trips.get(r.get("trip_id"))
            if trip is None:
                continue
            row = _stop_time_row(r, trips, stops)
            if row is None:
                bad += 1
                continue
            n_rows += 1
            stops_by_mode[trip["route_type"]].add(row[2])
            if trip["direction_id"] is not None:
                served[(row[2], trip["route_short_name"], trip["direction_id"])][trip["headsign"]] += 1
            if trip["route_type"] == "1":
                rail_seq[trip["trip_id"]].append((row[1], row[2]))
        if bad:
            warnings.append(f"{bad} stop_times rows skipped (unknown stop or unreadable time)")

        wanted_shapes = {t["shape_id"] for t in trips.values() if t["shape_id"]}
        points: dict[str, list[tuple[int, float, float]]] = defaultdict(list)
        for r in _reader(zf, "shapes.txt"):
            if r["shape_id"] in wanted_shapes:
                points[r["shape_id"]].append((int(r["shape_pt_sequence"]), float(r["shape_pt_lon"]),
                                              float(r["shape_pt_lat"])))
        shapes = {sid: [(lon, lat) for _, lon, lat in sorted(pts)] for sid, pts in points.items() if len(pts) >= 2}

    stop_routes = [{"stop_id": stop, "route_short_name": route, "direction_id": direction,
                    "headsign": heads.most_common(1)[0][0], "n_trips": sum(heads.values())}
                   for (stop, route, direction), heads in sorted(served.items(), key=lambda kv: kv[0])]

    platforms = _rail_platforms(trips, rail_seq, stops, warnings)
    station_map = _rail_station_map(stops, trips, rail_seq, extra_destinations, extra_stations, warnings)
    aliases = _place_aliases(stops, alias_seeds, warnings,
                             rail_stops=stops_by_mode.get("1", set()), bus_stops=stops_by_mode.get("3", set()))

    return StaticFeed(
        source=zip_path.name, sha256=sha, window_start=window_start, window_end=window_end,
        service_ids=sorted(services), feed_start=min(starts) if starts else None,
        feed_end=max(ends) if ends else None,
        routes=routes, stops=stops,
        calendar=[{"service_id": sid, **c} for sid, c in calendar.items()],
        calendar_dates=[{"service_id": s, "date": d, "exception_type": k} for (s, d), k in exceptions.items()],
        trips=trips, shapes=shapes, stop_routes=stop_routes, rail_platforms=platforms,
        rail_station_map=station_map, place_aliases=aliases, stop_time_count=n_rows,
        warnings=warnings, _zip_path=zip_path,
    )


def _rail_platforms(trips: dict, rail_seq: dict, stops: dict, warnings: list[str]) -> list[dict]:
    """Longest scheduled trip per line + direction = the full ordered platform list."""
    longest: dict[tuple[str, int], list[str]] = {}
    for trip_id, seq in rail_seq.items():
        t = trips[trip_id]
        key = (t["route_short_name"], t["direction_id"])
        ordered = [stop for _, stop in sorted(seq)]
        if key not in longest or len(ordered) > len(longest[key]):
            longest[key] = ordered
    out = []
    for (line, direction_id), platform_ids in sorted(longest.items()):
        letters = RAIL_DIRECTIONS.get(line)
        if letters is None or direction_id not in (0, 1):
            warnings.append(f"rail line {line} direction {direction_id}: no direction letter; skipped")
            continue
        for i, pid in enumerate(platform_ids):
            stop = stops[pid]
            parent = stop["parent_station"] or pid
            name = stops.get(parent, stop)["stop_name"]
            out.append({
                "line": line, "direction": letters[direction_id], "seq": i + 1,
                "parent_stop_id": parent, "platform_stop_id": pid, "station_name": display_name(name),
                "is_terminus": i in (0, len(platform_ids) - 1), "lon": stop["lon"], "lat": stop["lat"],
            })
    return out


def _rail_station_map(stops: dict, trips: dict, rail_seq: dict, extra_destinations: set[str],
                      extra_stations: set[str], warnings: list[str]) -> list[dict]:
    """Every spelling the rail API uses -> parent station.

    Built from the schedule itself so it never depends on which polls we happened to see:
    STATION forms are the GTFS names, the API aliases and their hyphen-free variants;
    DESTINATION forms are every station's display name (any station can be a short turn,
    e.g. weekend Green ending at King Memorial) plus the last stop of every scheduled trip.
    Spellings seen in our own archive are added on top.
    """
    rows: dict[str, dict] = {}

    def add(api_name: str, kind: str, parent: str | None) -> None:
        if api_name and parent in stops and api_name not in rows:
            rows[api_name] = {"api_name": api_name, "kind": kind, "parent_stop_id": parent,
                              "display_name": display_name(stops[parent]["stop_name"])}

    for parent, (name, _, _) in RAIL_STATIONS.items():
        add(name, "station", parent)
        add(norm_api_name(name), "station", parent)
    for api_name, parent in API_ALIASES.items():
        add(api_name, "station", parent)
        add(norm_api_name(api_name), "station", parent)
    for name in sorted(extra_stations):
        parent = station_id_for(name)
        if parent is None:
            warnings.append(f"rail station name {name!r} matches no station")
        add(name, "station", parent)

    for parent, (name, _, _) in RAIL_STATIONS.items():
        add(display_name(name), "destination", parent)
        add(name.removesuffix(" STATION").title(), "destination", parent)
    for name, parent in KNOWN_DESTINATIONS.items():
        add(name, "destination", parent)
    for trip_id, seq in rail_seq.items():          # where every scheduled train actually ends
        last = max(seq)[1]
        parent = stops[last]["parent_station"] or last
        if parent in stops:
            add(display_name(stops[parent]["stop_name"]), "destination", parent)
    candidates = set(extra_destinations)
    for t in trips.values():
        if t["route_type"] == "1" and t["headsign"] and " TO " in t["headsign"]:
            candidates.add(t["headsign"].split(" TO ", 1)[1].removesuffix(" STATION").title())
    for name in sorted(candidates):
        parent = station_id_for(name)
        if parent is None:
            warnings.append(f"rail destination {name!r} matches no station")
        add(name, "destination", parent)
    return sorted(rows.values(), key=lambda r: r["api_name"])


def _resolve_targets(spec: str, stops: dict) -> list[str]:
    ids: list[str] = []
    for part in filter(None, (p.strip() for p in spec.split("|"))):
        kind, _, value = part.partition(":")
        if kind == "station":
            ids += [s for s, v in stops.items() if v["parent_station"] == value and v["location_type"] == 0]
        elif kind == "stop":
            if value in stops:
                ids.append(value)
        elif kind == "near":
            lat, lon, radius = (float(x) for x in re.split(r"[;\s]+", value.strip()))
            ids += [s for s, v in stops.items()
                    if v["location_type"] == 0 and _haversine_m(lon, lat, v["lon"], v["lat"]) <= radius]
        else:
            raise ValueError(f"unknown alias target {part!r}")
    return sorted(set(ids))


def _place_aliases(stops: dict, seeds: Path | None, warnings: list[str],
                   rail_stops: set[str] = frozenset(), bus_stops: set[str] = frozenset()) -> list[dict]:
    """stop_ids is everything the name covers; rail_stop_ids and bus_stop_ids split it by what
    actually serves each stop, so a bus clue at "Five Points" never anchors on train platforms."""
    out: dict[str, dict] = {}

    def add(alias: str, label: str, spec: str, multi: bool, origin: str) -> None:
        key = normalize_place(alias)
        if not key:
            return
        if key in out:
            if out[key]["label"] != label and origin == "seed":
                warnings.append(f"alias {key!r} defined twice ({out[key]['label']} / {label})")
            return
        ids = _resolve_targets(spec, stops)
        if not ids:
            warnings.append(f"alias {key!r} resolves to no stops ({spec}); skipped")
            return
        out[key] = {"alias": key, "label": label, "stop_ids": ids, "multi": multi,
                    "rail_stop_ids": [i for i in ids if i in rail_stops],
                    "bus_stop_ids": [i for i in ids if i in bus_stops],
                    "lon": sum(stops[i]["lon"] for i in ids) / len(ids),
                    "lat": sum(stops[i]["lat"] for i in ids) / len(ids)}

    if seeds is not None and Path(seeds).is_file():
        with open(seeds, newline="") as f:
            for r in csv.DictReader(line for line in f if not line.lstrip().startswith("#")):
                add(r["alias"], r["label"], r["targets"], r["multi"].strip().lower() in ("1", "true", "yes"), "seed")
    # every rail station, under its GTFS name and its API spellings
    for parent, (name, _, _) in RAIL_STATIONS.items():
        if parent in stops:
            add(name, display_name(name), f"station:{parent}", False, "station")
    for api_name, parent in API_ALIASES.items():
        if parent in stops:
            add(api_name, display_name(stops[parent]["stop_name"]), f"station:{parent}", False, "station")
    return sorted(out.values(), key=lambda a: a["alias"])


def rail_names_from_archive(data_dir: Path, max_files: int = 3000) -> tuple[set[str], set[str]]:
    """(STATION spellings, DESTINATION spellings) the rail API actually used, sampled evenly
    across our whole archive so old short turns are not forgotten."""
    import gzip
    import json

    from .archive import RawArchive

    stations: set[str] = set()
    destinations: set[str] = set()
    files = list(RawArchive(data_dir).iter_files("marta-rail"))
    step = max(1, len(files) // max_files)
    for path in files[::step]:
        try:
            doc = json.loads(gzip.open(path).read())
        except Exception:  # noqa: BLE001  a damaged file just contributes nothing
            continue
        for kind in ("open", "keyed"):
            for r in doc.get(kind) or []:
                if isinstance(r, dict):
                    if r.get("STATION"):
                        stations.add(str(r["STATION"]).strip())
                    if r.get("DESTINATION"):
                        destinations.add(str(r["DESTINATION"]).strip())
    return stations, destinations


# ----------------------------------------------------------------------------- database

STATIC_TABLES = ("gtfs_stop_times", "gtfs_stop_routes", "gtfs_trips", "gtfs_shapes", "gtfs_stops",
                 "gtfs_routes", "gtfs_calendar_dates", "gtfs_calendar", "rail_platforms",
                 "rail_station_map", "place_aliases")


def _copy(cur, table: str, columns: list[str], rows) -> None:
    with cur.copy(f"COPY {table} ({', '.join(columns)}) FROM STDIN") as cp:
        for row in rows:
            cp.write_row(row)


def write_feed(conn, feed: StaticFeed) -> None:
    """Replace every static table in one transaction."""
    from psycopg.types.json import Jsonb

    with conn.transaction(), conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = 0")  # a million stop_times over the WAN takes a while
        cur.execute("TRUNCATE " + ", ".join(STATIC_TABLES))
        _copy(cur, "gtfs_routes", ["route_id", "route_short_name", "route_long_name", "route_type", "route_color"],
              ((r["route_id"], r["route_short_name"], r.get("route_long_name") or None,
                int(r["route_type"]) if r.get("route_type") else None, r.get("route_color") or None)
               for r in feed.routes))
        _copy(cur, "gtfs_stops", ["stop_id", "stop_name", "norm_name", "location_type", "parent_station",
                                  "lon", "lat", "geom"],
              ((s["stop_id"], s["stop_name"], s["norm_name"], s["location_type"], s["parent_station"],
                s["lon"], s["lat"], f"SRID=4326;POINT({s['lon']} {s['lat']})") for s in feed.stops.values()))
        _copy(cur, "gtfs_calendar", ["service_id", "mon", "tue", "wed", "thu", "fri", "sat", "sun",
                                     "start_date", "end_date"],
              ((c["service_id"], c["monday"], c["tuesday"], c["wednesday"], c["thursday"], c["friday"],
                c["saturday"], c["sunday"], c["start"], c["end"]) for c in feed.calendar))
        _copy(cur, "gtfs_calendar_dates", ["service_id", "date", "exception_type"],
              ((c["service_id"], c["date"], c["exception_type"]) for c in feed.calendar_dates))
        _copy(cur, "gtfs_trips", ["trip_id", "route_id", "route_short_name", "service_id", "direction_id",
                                  "block_id", "shape_id", "headsign"],
              ((t["trip_id"], t["route_id"], t["route_short_name"], t["service_id"], t["direction_id"],
                t["block_id"], t["shape_id"], t["headsign"]) for t in feed.trips.values()))
        _copy(cur, "gtfs_stop_times", ["trip_id", "stop_sequence", "stop_id", "arr_s", "dep_s", "timepoint"],
              feed.stop_times())
        _copy(cur, "gtfs_shapes", ["shape_id", "geom"],
              ((sid, "SRID=4326;LINESTRING(" + ", ".join(f"{lon} {lat}" for lon, lat in pts) + ")")
               for sid, pts in feed.shapes.items()))
        _copy(cur, "gtfs_stop_routes", ["stop_id", "route_short_name", "direction_id", "headsign", "n_trips"],
              ((r["stop_id"], r["route_short_name"], r["direction_id"], r["headsign"], r["n_trips"])
               for r in feed.stop_routes))
        cur.executemany(
            "INSERT INTO rail_platforms (line, direction, seq, parent_stop_id, platform_stop_id, station_name, "
            "is_terminus, lon, lat) VALUES (%(line)s, %(direction)s, %(seq)s, %(parent_stop_id)s, "
            "%(platform_stop_id)s, %(station_name)s, %(is_terminus)s, %(lon)s, %(lat)s)", feed.rail_platforms)
        cur.executemany(
            "INSERT INTO rail_station_map (api_name, kind, parent_stop_id, display_name) "
            "VALUES (%(api_name)s, %(kind)s, %(parent_stop_id)s, %(display_name)s)", feed.rail_station_map)
        cur.executemany(
            "INSERT INTO place_aliases (alias, label, stop_ids, rail_stop_ids, bus_stop_ids, multi, lon, lat) "
            "VALUES (%(alias)s, %(label)s, %(stop_ids)s, %(rail_stop_ids)s, %(bus_stop_ids)s, %(multi)s, "
            "%(lon)s, %(lat)s)", feed.place_aliases)
        cur.execute(
            "INSERT INTO static_feed_info (id, loaded_at, source, sha256, feed_start, feed_end, window_start, "
            "window_end, service_ids, counts) VALUES (true, now(), %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (id) DO UPDATE SET loaded_at = EXCLUDED.loaded_at, source = EXCLUDED.source, "
            "sha256 = EXCLUDED.sha256, feed_start = EXCLUDED.feed_start, feed_end = EXCLUDED.feed_end, "
            "window_start = EXCLUDED.window_start, window_end = EXCLUDED.window_end, "
            "service_ids = EXCLUDED.service_ids, counts = EXCLUDED.counts",
            (feed.source, feed.sha256, feed.feed_start, feed.feed_end, feed.window_start, feed.window_end,
             feed.service_ids, Jsonb(feed.counts)))


def report(feed: StaticFeed) -> str:
    """A human-readable summary for the CLI (dry run and after a load)."""
    lines = [f"schedule {feed.source} (sha256 {feed.sha256[:12]}), valid {feed.feed_start} to {feed.feed_end}",
             f"window {feed.window_start} to {feed.window_end}: services {', '.join(feed.service_ids)}",
             "counts: " + ", ".join(f"{k} {v:,}" for k, v in feed.counts.items())]
    by_line: dict[tuple, list] = defaultdict(list)
    for p in feed.rail_platforms:
        by_line[(p["line"], p["direction"])].append(p)
    for (line, direction), ps in sorted(by_line.items()):
        lines.append(f"  {line:5} {direction}: {len(ps):2} platforms, {ps[0]['station_name']} -> {ps[-1]['station_name']}")
    shared = Counter((p["direction"], p["platform_stop_id"]) for p in feed.rail_platforms)
    lines.append(f"  shared platforms (two lines, same track and direction): {sum(1 for n in shared.values() if n > 1)}")
    lines.append(f"rail names mapped: {sum(1 for r in feed.rail_station_map if r['kind'] == 'station')} station spellings, "
                 f"{sum(1 for r in feed.rail_station_map if r['kind'] == 'destination')} destination spellings")
    lines.append(f"place aliases: {len(feed.place_aliases)} ({sum(1 for a in feed.place_aliases if a['multi'])} ask a confirming tap)")
    for w in feed.warnings:
        lines.append(f"warning: {w}")
    return "\n".join(lines)
