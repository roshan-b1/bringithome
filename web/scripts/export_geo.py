#!/usr/bin/env python3
"""Static GeoJSON for the overlay map style (ARCHITECTURE.md decision 12: offline and ?stage=1 draw no tiles and use
no glyph server, only our own shapes). Python 3 stdlib only, so it runs on any laptop with no venv.

Reads data/static/google_transit.zip (the same MARTA schedule the API loads) and writes, into web/public/geo/:

  rail_lines.geojson           one LineString per rail line: the line's longest scheduled shape, with its name and
                               the feed's own route_color (a MARTA public-feed fact, never a logo)
  rail_stations.geojson        one Point per rail station (GTFS parent stop), place_id "station:<id>" like the API,
                               the label the API uses ("Five Points station") and the lines that stop there
  downtown_bus_routes.geojson  faint street stand-ins: every bus (and the streetcar) shape clipped to about 3 km
                               around Five Points, one MultiLineString per route, simplified hard

Why simplified and rounded: these ship in the static export and load on a borrowed phone on a bad connection, so
every byte counts. Rail lines keep 6 m of accuracy (the replay draws trains on them); downtown streets keep 15 m
(they are only context). Coordinates round to 5 decimals (about 1.1 m).

Usage: python3 web/scripts/export_geo.py [path/to/google_transit.zip]
"""

from __future__ import annotations

import csv
import io
import json
import math
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent
REPO = WEB.parent
DEFAULT_ZIP = REPO / "data" / "static" / "google_transit.zip"
OUT = WEB / "public" / "geo"

LINES = ["RED", "GOLD", "BLUE", "GREEN"]          # display order everywhere (matches the API's LINES)
FIVE_POINTS = "510015"                            # GTFS parent stop of Five Points station
DOWNTOWN_RADIUS_M = 3000
RAIL_TOLERANCE_M = 6.0
BUS_TOLERANCE_M = 15.0


def rows(z: zipfile.ZipFile, name: str):
    with z.open(name) as f:
        yield from csv.DictReader(io.TextIOWrapper(f, "utf-8-sig"))


def display_name(gtfs_name: str) -> str:
    """'LAKEWOOD-FT MCPHERSON STATION' -> 'Lakewood-Ft McPherson'. Mirrors worker/static_gtfs.display_name, so the
    map's labels read exactly like the API's place labels."""
    name = gtfs_name.removesuffix(" STATION").title()
    for wrong, right in (("Sec ", "SEC "), ("Mcpherson", "McPherson"), ("H E ", "H.E. ")):
        name = name.replace(wrong, right)
    return name


def haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371008.8 * math.asin(math.sqrt(h))


def length_m(pts: list[tuple[float, float]]) -> float:
    return sum(haversine(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def simplify(pts: list[tuple[float, float]], tol_m: float) -> list[tuple[float, float]]:
    """Ramer-Douglas-Peucker in a local equirectangular projection (fine at city scale), iterative so a long shape
    never hits the recursion limit."""
    if len(pts) < 3:
        return pts
    lat0 = math.radians(sum(p[1] for p in pts) / len(pts))
    kx, ky = 111320.0 * math.cos(lat0), 110540.0
    xy = [(p[0] * kx, p[1] * ky) for p in pts]
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        (ax, ay), (bx, by) = xy[i], xy[j]
        dx, dy = bx - ax, by - ay
        seg = math.hypot(dx, dy)
        worst, at = 0.0, -1
        for k in range(i + 1, j):
            px, py = xy[k]
            d = abs(dy * px - dx * py + bx * ay - by * ax) / seg if seg else math.hypot(px - ax, py - ay)
            if d > worst:
                worst, at = d, k
        if worst > tol_m:
            keep[at] = True
            stack += [(i, at), (at, j)]
    return [p for p, k in zip(pts, keep) if k]


def r5(pts):
    out, prev = [], None
    for lon, lat in pts:
        q = [round(lon, 5), round(lat, 5)]
        if q != prev:   # rounding can collapse neighbours
            out.append(q)
        prev = q
    return out


def clip_runs(pts, centre, radius_m):
    """Runs of consecutive points inside the circle, each extended by one point outside so lines reach the edge."""
    inside = [haversine(p, centre) <= radius_m for p in pts]
    runs, cur = [], []
    for i, p in enumerate(pts):
        if inside[i] or (i + 1 < len(pts) and inside[i + 1]) or (i > 0 and inside[i - 1]):
            cur.append(p)
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    return [r for r in runs if len(r) >= 2]


def write(name: str, features: list[dict]) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    body = json.dumps({"type": "FeatureCollection", "features": features}, separators=(",", ":"))
    (OUT / name).write_text(body + "\n", encoding="utf-8")
    return len(body) + 1


def main(zip_path: Path) -> None:
    z = zipfile.ZipFile(zip_path)

    rail, bus = {}, {}                                  # route_id -> (short_name, color) / short_name
    for r in rows(z, "routes.txt"):
        if r["route_type"] == "1":
            rail[r["route_id"]] = (r["route_short_name"].upper(), "#" + (r["route_color"] or "888888").upper())
        elif r["route_type"] in ("3", "0"):             # buses, and the streetcar as a street stand-in too
            bus[r["route_id"]] = (r["route_short_name"], "streetcar" if r["route_type"] == "0" else "bus")

    rail_trips: dict[str, str] = {}                     # trip_id -> line
    shapes_of: dict[tuple[str, str], set[str]] = defaultdict(set)   # (route_id, direction) -> shape ids
    for t in rows(z, "trips.txt"):
        rid = t["route_id"]
        if rid in rail:
            rail_trips[t["trip_id"]] = rail[rid][0]
        if (rid in rail or rid in bus) and t["shape_id"]:
            shapes_of[(rid, t["direction_id"])].add(t["shape_id"])

    wanted = set().union(*shapes_of.values())
    raw: dict[str, list[tuple[int, float, float]]] = defaultdict(list)
    for s in rows(z, "shapes.txt"):
        if s["shape_id"] in wanted:
            raw[s["shape_id"]].append((int(s["shape_pt_sequence"]), float(s["shape_pt_lon"]), float(s["shape_pt_lat"])))
    shapes = {sid: [(lon, lat) for _, lon, lat in sorted(p)] for sid, p in raw.items()}
    lengths = {sid: length_m(p) for sid, p in shapes.items()}

    def longest(route_id: str, direction: str | None = None) -> str | None:
        ids = [sid for (rid, d), s in shapes_of.items() if rid == route_id and direction in (None, d) for sid in s]
        ids = [i for i in ids if i in shapes]
        return max(ids, key=lengths.__getitem__, default=None)

    # rail lines: one feature per line, its longest shape (both directions run on the same track)
    lines_out = []
    for rid, (line, color) in sorted(rail.items(), key=lambda kv: LINES.index(kv[1][0]) if kv[1][0] in LINES else 9):
        sid = longest(rid)
        if sid is None:
            continue
        lines_out.append({"type": "Feature",
                          "properties": {"line": line, "name": f"{line.title()} Line", "color": color},
                          "geometry": {"type": "LineString", "coordinates": r5(simplify(shapes[sid], RAIL_TOLERANCE_M))}})

    # rail stations: parents of every platform a rail trip stops at, with the lines that stop there
    stops = {s["stop_id"]: s for s in rows(z, "stops.txt")}
    station_lines: dict[str, set[str]] = defaultdict(set)
    for st in rows(z, "stop_times.txt"):            # about 108 MB; streamed, only rail trips kept
        line = rail_trips.get(st["trip_id"])
        if line:
            stop = stops[st["stop_id"]]
            station_lines[stop["parent_station"] or stop["stop_id"]].add(line)
    stations_out = []
    for parent, lines in sorted(station_lines.items(), key=lambda kv: display_name(stops[kv[0]]["stop_name"])):
        s = stops[parent]
        stations_out.append({"type": "Feature",
                             "properties": {"place_id": f"station:{parent}",
                                            "label": f"{display_name(s['stop_name'])} station",
                                            "lines": [l for l in LINES if l in lines]},
                             "geometry": {"type": "Point",
                                          "coordinates": [round(float(s["stop_lon"]), 5), round(float(s["stop_lat"]), 5)]}})

    # downtown street stand-ins: each route's longest shape per direction (downtown one-way pairs differ), clipped
    centre = (float(stops[FIVE_POINTS]["stop_lon"]), float(stops[FIVE_POINTS]["stop_lat"]))
    bus_out = []
    for rid, (name, mode) in bus.items():
        parts = []
        for direction in ("0", "1"):
            sid = longest(rid, direction)
            if sid is None:
                continue
            for run in clip_runs(shapes[sid], centre, DOWNTOWN_RADIUS_M):
                pts = r5(simplify(run, BUS_TOLERANCE_M))
                if len(pts) >= 2:
                    parts.append(pts)
        if parts:
            bus_out.append({"type": "Feature", "properties": {"route": name, "mode": mode},
                            "geometry": {"type": "MultiLineString", "coordinates": parts}})
    bus_out.sort(key=lambda f: (f["properties"]["mode"], len(f["properties"]["route"]), f["properties"]["route"]))

    sizes = {
        "rail_lines.geojson": write("rail_lines.geojson", lines_out),
        "rail_stations.geojson": write("rail_stations.geojson", stations_out),
        "downtown_bus_routes.geojson": write("downtown_bus_routes.geojson", bus_out),
    }
    print(f"{len(lines_out)} rail lines, {len(stations_out)} stations, {len(bus_out)} downtown routes "
          f"within {DOWNTOWN_RADIUS_M / 1000:g} km of Five Points")
    for name, n in sizes.items():
        print(f"  {name}: {n / 1024:.1f} KB")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_ZIP)
