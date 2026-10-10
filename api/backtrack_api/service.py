"""POST /api/sessions: a rider's tapped clue -> a ranked, explained match (ARCHITECTURE.md 7.1 to 7.6).

Flow, per mode (bus, rail, or both when the rider isn't sure):
  1. anchors: the tapped places' stops, narrowed to the answered direction, as points
  2. pieces:  store.passes() (vehicle_passes in SQL, or its Python twin)
  3. rank:    matching.rank() -> pass events, cost, soft penalties
  4. coverage: scheduled vehicles that were NOT reporting could have been the one
  5. finish:  confidence, level, status, plus the evidence the rider and judges see
The session (tap answers and the result only) is kept for under 24 hours.
"""

from __future__ import annotations

import time
from collections import Counter
from datetime import datetime, timedelta
from typing import Callable

from .matching import (BOARD_MAX, CANDIDATE_MAX_D, RAIL_PLATFORM_WINDOW, Anchor, Candidate, Clue, MatchResult,
                       PEN_LINE_SEPARATE, PEN_LINE_SHARED, Unreported, finish, rank, unreported_cost)
from .models import Intake
from .resolver import PlaceIndex, _pretty_headsign
from .store import PASSES_SQL, Store, new_session_id

SESSION_PROMISE = timedelta(hours=2)   # deleted 2 h after its last use (store.SESSION_IDLE); this is the first deadline


class StoreLookups:
    """matching.Lookups backed by a store and the place index."""

    def __init__(self, store: Store, index: PlaceIndex, mode: str):
        self.store, self.index, self.mode = store, index, mode
        self._trips: dict[str, dict] = {}

    def _vid(self, key: str) -> str:
        return key.split(":", 1)[1]

    def is_terminus_pass(self, anchor, ev) -> bool:
        if self.mode != "rail":
            return False
        plats = self.index.platforms_for(anchor.stop_ids)
        if any(p["is_terminus"] and p["line"] == ev.route_short for p in plats):
            return True
        parent = self.index.parent_of_destination(ev.destination)   # short turns: it ends here today
        return parent is not None and parent in {p["parent_stop_id"] for p in plats}

    def trip(self, trip_id: str | None) -> dict | None:
        if not trip_id:
            return None
        if trip_id not in self._trips:
            self._trips.update(self.store.trip_info([trip_id]))
            self._trips.setdefault(trip_id, {})
        return self._trips[trip_id] or None

    def trip_direction(self, trip_id):
        t = self.trip(trip_id)
        return t.get("direction_id") if t else None

    def route_seen(self, key, route, t0, t1):
        return self.store.route_seen(self.mode, self._vid(key), route, t0, t1)

    def lines_share_platform(self, anchor, line_a, line_b):
        a = {p["platform_stop_id"] for p in self.index.platforms_for(anchor.stop_ids, line=line_a)}
        b = {p["platform_stop_id"] for p in self.index.platforms_for(anchor.stop_ids, line=line_b)}
        return bool(a & b)

    def off_shape(self, key, trip_id, at):
        return self.store.off_shape(self.mode, self._vid(key), trip_id, at)

    def trip_changed_near(self, key, at, within):
        return self.store.trip_changed_near(self.mode, self._vid(key), at, within)


class MatchService:
    def __init__(self, store: Store, index: PlaceIndex, now: Callable[[], datetime]):
        self.store, self.index, self.now = store, index, now

    # 7.1 intake -> clues ------------------------------------------------------------------

    def _anchors(self, intake: Intake, mode: str) -> list[Anchor]:
        out = []
        for i, a in enumerate(intake.anchors):
            ids = [s for s in a.stop_ids if s in self.index.stops]
            if mode == "rail":
                plats = self.index.platforms_for(ids, line=None, direction=intake.direction)
                if intake.line:  # the rider's line, or any line sharing its platforms
                    line_plats = {p["platform_stop_id"] for p in self.index.platforms_for(ids, line=intake.line)}
                    plats = [p for p in plats if p["platform_stop_id"] in line_plats] or plats
                ids = sorted({p["platform_stop_id"] for p in plats})
            else:
                ids = [s for s in ids if mode in self.index.stops[s].modes]
                if intake.direction_id is not None:
                    served = {r["stop_id"] for r in self.index.stop_routes
                              if r["stop_id"] in set(ids) and r["direction_id"] == intake.direction_id
                              and (intake.route is None or r["route_short_name"] == intake.route)}
                    ids = [s for s in ids if s in served] or ids
            if not ids:
                continue
            place = self.index.place(a.place_id)
            out.append(Anchor(idx=i + 1, role=a.role, place_label=place.label if place else a.place_id,
                              stop_ids=ids, points=[(self.index.stops[s].lon, self.index.stops[s].lat) for s in ids],
                              t_center=a.at, w_min=a.window_min))
        return out

    # the whole match ------------------------------------------------------------------------

    def match(self, intake: Intake) -> tuple[MatchResult, dict]:
        modes = ["bus", "rail"] if intake.mode == "unknown" else [intake.mode]
        all_cands: list[Candidate] = []
        all_unrep: list[Unreported] = []
        sched_rows: list[tuple[dict, Anchor]] = []
        near = 0
        gap = False
        scanned = considered = query_ms = 0
        engine = "python"
        window_from = window_to = None
        primary_label = None
        for mode in modes:
            anchors = self._anchors(intake, mode)
            if not anchors:
                continue
            primary = sorted(anchors, key=lambda a: a.role != "alight")[0]
            primary_label = primary_label or primary.place_label
            t_from = min(a.t_center - timedelta(minutes=a.w_min + 3) for a in anchors)
            t_to = max(a.t_center + timedelta(minutes=a.w_min + 3) for a in anchors)
            window_from = min(filter(None, [window_from, t_from]))
            window_to = max(filter(None, [window_to, t_to]))
            clue = Clue(mode=mode if mode != "streetcar" else "bus", anchors=anchors, route=intake.route,
                        line=intake.line, direction=intake.direction, direction_id=intake.direction_id,
                        own_vehicle_key=intake.on_vehicle.vehicle_key if intake.on_vehicle else None)
            lookups = StoreLookups(self.store, self.index, mode)
            pieces: list = []
            if mode != "streetcar":   # the streetcar shares no location: schedule only
                started = time.monotonic()
                pieces, stats = self.store.passes(mode, anchors, t_from, t_to)
                query_ms += stats.get("query_ms", round((time.monotonic() - started) * 1000))
                scanned += stats.get("positions_scanned", 0)
                engine = stats.get("engine", engine)
                considered += len({(p.source, p.vehicle_id) for p in pieces})
                cands, n_near = rank(pieces, clue, lookups)
                all_cands += cands
                near += n_near
                gap = gap or self.store.gap_overlaps(mode, t_from, t_to)
            rows = self.store.scheduled(mode, primary.stop_ids, primary.t_center, (primary.w_min + 3) * 60,
                                        intake.route if mode != "rail" else None,
                                        intake.direction_id if mode != "rail" else None)
            if mode == "rail" and intake.direction:
                rows = [r for r in rows if r["direction_id"] == (0 if intake.direction in "NE" else 1)]
            sched_rows += [(r, primary) for r in rows]
            all_unrep += self._unreported(mode, rows, pieces, primary, clue, lookups, t_from, t_to, gap)
        result = finish(all_cands, all_unrep, recorder_gap=gap, vehicles_near=near, has_schedule=bool(sched_rows))
        meta = {"modes": modes, "scanned": scanned, "considered": considered, "query_ms": query_ms,
                "engine": engine, "window": (window_from, window_to), "place_label": primary_label or "",
                "sched_rows": sched_rows}
        return result, meta

    # 7.5 coverage --------------------------------------------------------------------------

    def _unreported(self, mode, rows, pieces, anchor, clue, lookups, t_from, t_to, gap) -> list[Unreported]:
        if not rows:
            return []
        out: dict[str, Unreported] = {}
        if mode in ("bus", "streetcar"):
            live_trips = {p.trip_id for p in pieces if p.trip_id}
            info = self.store.trip_info(live_trips) if live_trips else {}
            live_blocks = {(t["service_id"], t["block_id"]) for t in info.values() if t.get("block_id")}
            for r in rows:
                if (r["service_id"], r["block_id"]) in live_blocks or r["trip_id"] in out:
                    continue
                out[r["trip_id"]] = Unreported(r["trip_id"], r["route"], r["headsign"], r["scheduled_at"],
                                               r["stop_id"], r["service_id"], r["block_id"],
                                               unreported_cost(r["scheduled_at"], anchor))
        else:
            # Rail: the static timetable is wrong on special-schedule days and every live train reports,
            # so a scheduled train counts as missing only when we know we missed data (ARCHITECTURE 7.5).
            if not (gap or self.store.rail_dropout(clue.line, t_from, t_to)):
                return []
            live_times = [p.t_pass for p in pieces if p.anchor_idx == anchor.idx and p.dist_m <= CANDIDATE_MAX_D]
            for r in rows:
                if any(abs(t - r["scheduled_at"]) <= RAIL_PLATFORM_WINDOW for t in live_times) or r["trip_id"] in out:
                    continue
                cost = unreported_cost(r["scheduled_at"], anchor)
                if clue.line and r["route"] != clue.line:
                    cost += PEN_LINE_SHARED if lookups.lines_share_platform(anchor, clue.line, r["route"]) \
                        else PEN_LINE_SEPARATE
                out[r["trip_id"]] = Unreported(r["trip_id"], r["route"], r["headsign"], r["scheduled_at"],
                                               r["stop_id"], r["service_id"], r["block_id"], cost)
        return list(out.values())

    # the response ---------------------------------------------------------------------------

    def direction_label(self, c: Candidate, lookups_trip: Callable[[str | None], dict | None]) -> str:
        if c.mode == "rail":
            dest = c.passes[0].destination
            parent = self.index.parent_of_destination(dest)
            name = self.index.stations[parent].label if parent in self.index.stations else dest
            return f"To {name}" if name else "Direction unknown"
        trip = lookups_trip(c.trip_id)
        head = trip.get("headsign") if trip else None
        return f"Toward {_pretty_headsign(head)}" if head else "Direction unknown"

    def build_match(self, intake: Intake, result: MatchResult, meta: dict, at: datetime) -> dict:
        trips = {}

        def trip(tid):
            if tid and tid not in trips:
                trips.update(self.store.trip_info([tid]))
                trips.setdefault(tid, {})
            return trips.get(tid) or None

        anchors_by_idx = {i + 1: a for i, a in enumerate(intake.anchors)}
        labels = {}
        for idx, a in anchors_by_idx.items():
            p = self.index.place(a.place_id)
            labels[idx] = p.label if p else a.place_id
        candidates = []
        for c in result.candidates[:5]:
            candidates.append({
                "vehicle_key": c.vehicle_key, "mode": c.mode, "vehicle_id": c.vehicle_id, "label": c.label,
                "route": c.route, "continuing_as": c.continuing_as, "line": c.line,
                "direction_label": self.direction_label(c, trip), "confidence": round(c.p, 3),
                "level": c.level or "low", "flags": c.flags, "source": "live",
                "evidence": [{"role": e.role, "place_label": labels.get(e.anchor_idx, ""), "at": e.t_event,
                              "t_in": e.t_in, "t_out": e.t_out, "distance_m": round(e.d_star)} for e in c.passes],
            })
        stop_label = {}
        schedule = []
        for u in sorted(result.unreported, key=lambda u: u.cost)[:5] or []:
            stop_label.setdefault(u.stop_id, self.index.stops[u.stop_id].label if u.stop_id in self.index.stops else u.stop_id)
            block = self.store.block_vehicle_now(u.service_id, u.block_id, at) if u.route and u.block_id else None
            schedule.append({"trip_id": u.trip_id, "route": u.route, "headsign": u.headsign,
                             "scheduled_at": u.scheduled_at, "stop_label": stop_label[u.stop_id],
                             "status": "scheduled", "block_vehicle_now": block})
        if not schedule and result.status in ("not_reporting", "no_match"):
            for r, _a in sorted(meta["sched_rows"], key=lambda ra: abs(ra[0]["scheduled_at"] - ra[1].t_center))[:5]:
                label = self.index.stops[r["stop_id"]].label if r["stop_id"] in self.index.stops else r["stop_id"]
                schedule.append({"trip_id": r["trip_id"], "route": r["route"], "headsign": r["headsign"],
                                 "scheduled_at": r["scheduled_at"], "stop_label": label, "status": "scheduled"})
        modes = meta["modes"]
        counted = "buses and trains" if len(modes) > 1 else "trains" if modes == ["rail"] else "buses"
        w_from, w_to = meta["window"]
        body = {
            "status": result.status, "level": result.level, "candidates": candidates,
            "schedule_candidates": schedule,
            "coverage": {"unreported_share": round(result.unreported_share, 3), "recorder_gap": result.recorder_gap},
            "context": {"vehicles_near": result.vehicles_near, "counted": counted, "radius_m": 400,
                        "place_label": meta["place_label"],
                        "window": {"from": w_from or at, "to": w_to or at}},
            "stats": {"positions_scanned": meta["scanned"], "vehicles_considered": meta["considered"],
                      "query_ms": meta["query_ms"], "sql": " ".join(PASSES_SQL.split()), "engine": meta["engine"]},
        }
        top = result.candidates
        if result.status == "ambiguous":
            body["tie_break"] = {"options": [
                {"vehicle_key": c.vehicle_key,
                 "facts": {"line": c.line or c.route or "", "place_label": labels.get(c.passes[0].anchor_idx, ""),
                           "at": c.passes[0].t_event.isoformat()}} for c in top[:2]]}
        if result.status == "uncertain" and len(top) > 1:
            body["separate_by"] = self._separate_by(intake, top[:3], trip)
        return body

    def _separate_by(self, intake: Intake, top: list[Candidate], trip) -> dict | None:
        """The unanswered question whose answers best split the top 3."""
        questions = []
        if intake.route is None and intake.mode != "rail":
            questions.append(("route", {c.vehicle_key: f"Route {c.route}" for c in top if c.route}))
        if intake.direction is None and intake.direction_id is None:
            questions.append(("direction", {c.vehicle_key: self.direction_label(c, trip) for c in top}))
        best = max(questions, key=lambda q: len(set(q[1].values())), default=None)
        if not best or len(set(best[1].values())) < 2:
            return None
        groups: dict[str, list[str]] = {}
        for key, value in best[1].items():
            groups.setdefault(value, []).append(key)
        return {"question": best[0], "options": [{"label": v, "vehicle_keys": ks} for v, ks in groups.items()]}

    # sessions ---------------------------------------------------------------------------------

    def at(self, as_of: datetime | None) -> MatchService:
        """This service as of `as_of`: every positions read sees only rows recorded and fetched by then
        (decision 13), so a replayed session ranks exactly what the app could have known at that moment."""
        return self if as_of is None else MatchService(self.store.with_horizon(as_of), self.index, self.now)

    def create(self, intake: Intake, as_of: datetime | None, record: bool = False) -> dict:
        created = self.now()
        svc = self.at(as_of)
        result, meta = svc.match(intake)
        match = svc.build_match(intake, result, meta, as_of or created)
        sid = new_session_id()
        response = {"session_id": sid, "deletes_by": created + SESSION_PROMISE, "as_of": as_of, "record": record,
                    "match": match, "source": "as_of" if as_of else "live"}
        self.store.save_session({"id": sid, "created_at": created, "as_of": as_of, "record": record,
                                 "intake": intake.model_dump(mode="json"),
                                 "result": _jsonable(response)})
        return response


def _jsonable(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_jsonable(v) for v in obj]
    return obj
