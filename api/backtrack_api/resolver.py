"""Deterministic place resolution, held in memory (ARCHITECTURE.md decision 5).

Turns what a rider says or taps into real stop ids. No model and no geocoder
ever produces a location: every coordinate comes from MARTA's static GTFS.

Order for a typed or spoken place:
  1. exact place alias ("gsu", "the airport"); names covering several places ask to confirm
  2. route context: a route plus a landmark becomes that route's stops within 400 m
     ("the 49 near Peachtree Center" -> Courtland St @ Ellis St)
  3. fuzzy stop names (token-set similarity); auto-picked only with score >= 0.8 and a 0.1 lead,
     otherwise the top 3 come back to confirm
For "use my location": the 3 nearest stop clusters (same-name stops within 300 m grouped).

The index is built from a StaticFeed (the schedule zip). The same shape can be filled from
the database tables later; nothing here touches the network.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from backtrack_worker.places import normalize_place
from backtrack_worker.static_gtfs import StaticFeed

CLUSTER_M = 300
ROUTE_CONTEXT_M = 400
FUZZY_ACCEPT = 0.8
FUZZY_LEAD = 0.1
MODE_OF_ROUTE_TYPE = {"3": "bus", "1": "rail", "0": "streetcar"}
LINES = ("RED", "GOLD", "BLUE", "GREEN")


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371008.8 * math.asin(math.sqrt(a))


_KEEP_UPPER = {"NE", "NW", "SE", "SW", "MARTA", "SEC", "GSU", "ATL", "MLK", "US", "GA", "CDC", "VA", "II", "III"}


def pretty_stop_name(raw: str) -> str:
    """'PIEDMONT AVE SE @ GILMER ST SE' -> 'Piedmont Ave SE @ Gilmer St SE'."""
    out = []
    for w in raw.split():
        letters = re.sub(r"[^A-Za-z]", "", w)
        if letters.upper() in _KEEP_UPPER:
            out.append(w.upper())
        elif w.isupper() or w.islower():
            out.append(w.capitalize())
        else:
            out.append(w)
    return " ".join(out)


def token_set_ratio(a: str, b: str) -> float:
    """Order-insensitive similarity of two normalized names (0..1), stdlib only."""
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    inter = " ".join(sorted(ta & tb))
    s2 = " ".join(filter(None, [inter, " ".join(sorted(ta - tb))]))
    s3 = " ".join(filter(None, [inter, " ".join(sorted(tb - ta))]))

    def ratio(x: str, y: str) -> float:
        return SequenceMatcher(None, x, y).ratio() if x and y else 0.0

    return max(ratio(inter, s2), ratio(inter, s3), ratio(s2, s3)) if inter else ratio(s2, s3)


@dataclass
class Stop:
    stop_id: str
    name: str
    label: str
    norm: str
    lon: float
    lat: float
    parent: str | None
    routes: set[str] = field(default_factory=set)
    modes: set[str] = field(default_factory=set)


@dataclass
class Station:
    parent_id: str
    label: str
    lon: float
    lat: float
    platform_ids: list[str]
    lines: list[str]
    child_ids: list[str]


@dataclass
class Place:
    """One answer to "where": what the API returns as `Place`."""
    place_id: str
    label: str
    kind: str                       # stop | station | alias
    lat: float
    lon: float
    stop_ids: list[str]
    routes: list[str]
    lines: list[str]
    needs_confirm: bool = False
    distance_m: float | None = None

    def to_dict(self) -> dict:
        d = {k: getattr(self, k) for k in ("place_id", "label", "kind", "lat", "lon", "stop_ids",
                                           "routes", "lines", "needs_confirm")}
        if self.distance_m is not None:
            d["distance_m"] = round(self.distance_m)
        return d


@dataclass
class SearchResult:
    resolved: Place | None
    choices: list[Place]
    method: str                     # alias | station | stop_fuzzy | route_context | none
    needs_confirm: bool


class PlaceIndex:
    def __init__(self, stops: dict[str, Stop], stations: dict[str, Station], aliases: dict[str, dict],
                 stop_routes: list[dict], rail_platforms: list[dict], route_modes: dict[str, str],
                 station_map: dict[str, str] | None = None):
        self.station_map = station_map or {}   # rail API name (STATION or DESTINATION) -> parent station
        self.stops = stops
        self.stations = stations
        self.aliases = aliases
        self.stop_routes = stop_routes
        self.rail_platforms = rail_platforms
        self.route_modes = route_modes
        self._platform_parent = {p["platform_stop_id"]: p["parent_stop_id"] for p in rail_platforms}
        self._by_norm: dict[str, list[str]] = defaultdict(list)
        self._by_token: dict[str, set[str]] = defaultdict(set)
        for s in stops.values():
            if s.parent and s.parent in stations and s.stop_id in self._platform_parent:
                continue  # rail platforms are reached through their station
            self._by_norm[s.norm].append(s.stop_id)
            for t in s.norm.split():
                self._by_token[t].add(s.norm)
        self._route_stops: dict[str, set[str]] = defaultdict(set)
        for r in stop_routes:
            self._route_stops[r["route_short_name"]].add(r["stop_id"])

    # building -------------------------------------------------------------------

    @classmethod
    def from_feed(cls, feed: StaticFeed) -> PlaceIndex:
        route_modes = {r["route_short_name"]: MODE_OF_ROUTE_TYPE.get(r.get("route_type", ""), "bus")
                       for r in feed.routes}
        stops: dict[str, Stop] = {}
        for s in feed.stops.values():
            if s["location_type"] != 0:
                continue
            stops[s["stop_id"]] = Stop(s["stop_id"], s["stop_name"], pretty_stop_name(s["stop_name"]),
                                       s["norm_name"], s["lon"], s["lat"], s["parent_station"])
        for r in feed.stop_routes:
            st = stops.get(r["stop_id"])
            if st is not None:
                st.routes.add(r["route_short_name"])
                st.modes.add(route_modes.get(r["route_short_name"], "bus"))
        stations: dict[str, Station] = {}
        by_parent: dict[str, list[dict]] = defaultdict(list)
        for p in feed.rail_platforms:
            by_parent[p["parent_stop_id"]].append(p)
        for parent, plats in by_parent.items():
            ps = feed.stops[parent]
            children = sorted(sid for sid, s in stops.items() if s.parent == parent)
            stations[parent] = Station(
                parent_id=parent, label=plats[0]["station_name"], lon=ps["lon"], lat=ps["lat"],
                platform_ids=sorted({p["platform_stop_id"] for p in plats}),
                lines=sorted({p["line"] for p in plats}, key=LINES.index), child_ids=children)
        aliases = {a["alias"]: a for a in feed.place_aliases}
        station_map = {r["api_name"]: r["parent_stop_id"] for r in feed.rail_station_map}
        index = cls(stops, stations, aliases, feed.stop_routes, feed.rail_platforms, route_modes, station_map)
        index.feed = feed   # the in-memory store's schedule comes from the same feed
        return index

    def parent_of_destination(self, name: str | None) -> str | None:
        """A rail DESTINATION (or STATION) as the API wrote it -> GTFS parent station."""
        if not name:
            return None
        if name in self.station_map:
            return self.station_map[name]
        from backtrack_worker.connectors.rail_stations import station_id_for

        return station_id_for(name)   # same normalization the table was built with

    def platforms_for(self, stop_ids, line: str | None = None, direction: str | None = None) -> list[dict]:
        ids = set(stop_ids)
        return [p for p in self.rail_platforms if p["platform_stop_id"] in ids
                and (line is None or p["line"] == line) and (direction is None or p["direction"] == direction)]

    # helpers -----------------------------------------------------------------------

    def _lines_at(self, stop_ids) -> list[str]:
        lines = {p["line"] for p in self.rail_platforms if p["platform_stop_id"] in set(stop_ids)}
        return sorted(lines, key=LINES.index)

    def _routes_at(self, stop_ids) -> list[str]:
        routes = set()
        for sid in stop_ids:
            st = self.stops.get(sid)
            if st:
                routes |= {r for r in st.routes if self.route_modes.get(r) != "rail"}
        return sorted(routes, key=lambda r: (not r.isdigit(), int(r) if r.isdigit() else 0, r))

    def _for_mode(self, stop_ids: list[str], mode: str | None) -> list[str]:
        if mode in (None, "any", "unknown"):
            return list(stop_ids)
        return [s for s in stop_ids if s in self.stops and mode in self.stops[s].modes]

    def _centroid(self, stop_ids) -> tuple[float, float]:
        pts = [self.stops[s] for s in stop_ids if s in self.stops]
        return sum(p.lon for p in pts) / len(pts), sum(p.lat for p in pts) / len(pts)

    def station_place(self, parent: str, mode: str | None = None) -> Place:
        st = self.stations[parent]
        if mode == "rail":
            ids = st.platform_ids
        elif mode in ("bus", "streetcar"):
            ids = self._for_mode(st.child_ids, mode)
        else:
            ids = sorted(set(st.platform_ids) | set(st.child_ids))
        return Place(f"station:{parent}", f"{st.label} station", "station", st.lat, st.lon, ids,
                     self._routes_at(ids), st.lines)

    def stop_place(self, stop_id: str, mode: str | None = None, cluster: bool = True) -> Place:
        s = self.stops[stop_id]
        if s.stop_id in self._platform_parent:
            return self.station_place(self._platform_parent[s.stop_id], mode)
        ids = [stop_id]
        if cluster:  # same-name stops close together are one place to a rider (both sides of a street)
            ids = sorted(o for o in self._by_norm.get(s.norm, [])
                         if haversine_m(s.lon, s.lat, self.stops[o].lon, self.stops[o].lat) <= CLUSTER_M)
        ids = self._for_mode(ids, mode) or ids
        lon, lat = self._centroid(ids)
        return Place(f"stop:{stop_id}", s.label, "stop", lat, lon, ids, self._routes_at(ids), self._lines_at(ids))

    def alias_place(self, key: str, mode: str | None = None) -> Place:
        a = self.aliases[key]
        if mode == "rail" and a.get("rail_stop_ids"):
            ids = a["rail_stop_ids"]
        elif mode == "bus" and a.get("bus_stop_ids"):
            ids = a["bus_stop_ids"]
        else:
            ids = a["stop_ids"]
        return Place(f"alias:{key}", a["label"], "alias", a["lat"], a["lon"], list(ids),
                     self._routes_at(ids), self._lines_at(ids), needs_confirm=bool(a["multi"]))

    def place(self, place_id: str, mode: str | None = None) -> Place | None:
        kind, _, key = place_id.partition(":")
        if kind == "alias" and key in self.aliases:
            return self.alias_place(key, mode)
        if kind == "station" and key in self.stations:
            return self.station_place(key, mode)
        if kind == "stop" and key in self.stops:
            return self.stop_place(key, mode)
        return None

    # nearby -------------------------------------------------------------------------

    def nearby(self, lat: float, lon: float, mode: str | None = None, k: int = 3) -> list[Place]:
        """The k nearest places: stations for rail riders, stop clusters for bus riders."""
        found: list[Place] = []
        seen: set[str] = set()
        candidates = []
        if mode in (None, "any", "unknown", "rail"):
            for st in self.stations.values():
                d = min(haversine_m(lon, lat, self.stops[p].lon, self.stops[p].lat)
                        for p in st.platform_ids if p in self.stops)
                candidates.append((d, "station", st.parent_id))
        if mode != "rail":
            for s in self.stops.values():
                if s.stop_id in self._platform_parent or (mode not in (None, "any", "unknown") and mode not in s.modes):
                    continue
                if not s.modes:
                    continue  # a stop nothing serves in the loaded window
                candidates.append((haversine_m(lon, lat, s.lon, s.lat), "stop", s.stop_id))
        for d, kind, key in sorted(candidates):
            p = self.station_place(key, mode) if kind == "station" else self.stop_place(key, mode)
            if p.place_id in seen or any(set(p.stop_ids) & set(q.stop_ids) for q in found):
                continue
            seen.add(p.place_id)
            p.distance_m = d
            found.append(p)
            if len(found) == k:
                break
        return found

    # search -------------------------------------------------------------------------

    def search(self, q: str, mode: str | None = None, route: str | None = None) -> SearchResult:
        norm = normalize_place(q or "")
        if not norm:
            return SearchResult(None, [], "none", False)

        landmark: Place | None = None
        method = "none"
        if norm in self.aliases:
            landmark, method = self.alias_place(norm, mode), "alias"
        else:
            fuzzy = self._fuzzy(norm, mode, route)
            exact_station = next((pl for _, pl in fuzzy
                                  if pl.kind == "station" and normalize_place(pl.label) == norm), None)
            if exact_station is not None:  # the whole station name, not just one of its words
                landmark, method = exact_station, "station"
            elif fuzzy and fuzzy[0][0] >= FUZZY_ACCEPT and (len(fuzzy) == 1 or fuzzy[0][0] - fuzzy[1][0] >= FUZZY_LEAD):
                landmark, method = fuzzy[0][1], "stop_fuzzy"
            elif not route:
                choices = [p for _, p in fuzzy[:3]]
                return SearchResult(None, choices, "stop_fuzzy" if choices else "none", bool(choices))
            elif fuzzy:
                landmark, method = fuzzy[0][1], "stop_fuzzy"

        if route and landmark is not None:
            ctx = self._route_context(route, landmark)
            if ctx is not None:
                return ctx
        if landmark is None:
            return SearchResult(None, [], "none", False)
        return SearchResult(landmark, [], method, landmark.needs_confirm)

    def _fuzzy(self, norm: str, mode: str | None, route: str | None) -> list[tuple[float, Place]]:
        tokens = norm.split()
        pool: set[str] = set()
        for t in tokens:
            pool |= self._by_token.get(t, set())
        station_names = {normalize_place(st.label): pid for pid, st in self.stations.items()}
        scored: list[tuple[float, Place]] = []
        for name, pid in station_names.items():
            score = token_set_ratio(norm, name)
            if score >= 0.6 and mode in (None, "any", "unknown", "rail"):
                scored.append((score, self.station_place(pid, mode)))
        for name in pool:
            score = token_set_ratio(norm, name)
            if score < 0.6:
                continue
            sid = self._by_norm[name][0]
            if mode not in (None, "any", "unknown") and not any(mode in self.stops[s].modes for s in self._by_norm[name]):
                continue
            if route and not any(route in self.stops[s].routes for s in self._by_norm[name]):
                continue
            scored.append((score, self.stop_place(sid, mode)))
        # ties (many stops share a word like "peachtree"): stations first, then the busiest stops
        scored.sort(key=lambda x: (-round(x[0], 3), x[1].kind != "station", -len(x[1].routes), x[1].label))
        unique, seen = [], set()
        for score, p in scored:
            if p.place_id not in seen:
                seen.add(p.place_id)
                unique.append((score, p))
        return unique

    def _route_context(self, route: str, landmark: Place) -> SearchResult | None:
        route_stops = self._route_stops.get(route)
        if not route_stops:
            return None
        anchors = [self.stops[s] for s in landmark.stop_ids if s in self.stops] or None
        ref = anchors or [Stop("", "", "", "", landmark.lon, landmark.lat, None)]
        dist = {sid: min(haversine_m(a.lon, a.lat, self.stops[sid].lon, self.stops[sid].lat) for a in ref)
                for sid in route_stops if sid in self.stops}
        if not dist:
            return None
        inside = sorted((d, s) for s, d in dist.items() if d <= ROUTE_CONTEXT_M)
        if inside:
            d0, s0 = inside[0]
            place = self.stop_place(s0, "bus" if self.route_modes.get(route) == "bus" else None)
            place.label = f"{place.label} (route {route} near {landmark.label})"
            place.distance_m = d0
            return SearchResult(place, [], "route_context", landmark.needs_confirm)
        nearest = sorted((d, s) for s, d in dist.items())[:3]
        choices = []
        for d, s in nearest:
            p = self.stop_place(s)
            p.distance_m = d
            choices.append(p)
        return SearchResult(None, choices, "route_context", True)

    # directions ------------------------------------------------------------------

    def directions(self, place: Place, mode: str, route: str | None = None, line: str | None = None) -> dict:
        """Which way was it going? Options come only from what serves these stops."""
        if mode == "rail":
            plats = [p for p in self.rail_platforms if p["platform_stop_id"] in set(place.stop_ids)
                     and (line is None or p["line"] == line)]
            ends: dict[tuple[str, str], str] = {}
            for p in self.rail_platforms:
                key = (p["line"], p["direction"])
                if key not in ends or p["seq"] > ends[key][0]:
                    ends[key] = (p["seq"], p["station_name"])
            by_dir: dict[str, dict] = {}
            for p in plats:
                opt = by_dir.setdefault(p["direction"], {"direction_id": 0 if p["direction"] in "NE" else 1,
                                                         "direction": p["direction"], "terminals": set(),
                                                         "stop_ids": set()})
                opt["terminals"].add(ends[(p["line"], p["direction"])][1])
                opt["stop_ids"].add(p["platform_stop_id"])
            options = [{"direction_id": o["direction_id"], "direction": o["direction"],
                        "label": "To " + " or ".join(sorted(o["terminals"])), "stop_ids": sorted(o["stop_ids"])}
                       for _, o in sorted(by_dir.items(), key=lambda kv: kv[1]["direction_id"])]
        else:
            rows = [r for r in self.stop_routes if r["stop_id"] in set(place.stop_ids)
                    and (route is None or r["route_short_name"] == route)]
            by_dir: dict[int, dict] = {}
            for r in rows:
                opt = by_dir.setdefault(r["direction_id"], {"heads": defaultdict(int), "stop_ids": set()})
                opt["heads"][r["headsign"]] += r["n_trips"]
                opt["stop_ids"].add(r["stop_id"])
            options = []
            for d, o in sorted(by_dir.items()):
                head = max(o["heads"], key=o["heads"].get) if o["heads"] else None
                options.append({"direction_id": d, "direction": None,
                                "label": f"Toward {_pretty_headsign(head)}" if head else "This direction",
                                "stop_ids": sorted(o["stop_ids"])})
        return {"ask": len(options) > 1, "options": options}


def _pretty_headsign(head: str) -> str:
    """'North Ave Stn' -> 'North Ave Station'; 'NORTH AVE STATION' -> 'North Ave Station'."""
    text = re.sub(r"\bStn\b\.?", "Station", head, flags=re.IGNORECASE)
    if text.isupper():
        text = pretty_stop_name(text)
    return text
