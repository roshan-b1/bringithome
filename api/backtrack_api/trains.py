"""Which train are you on now? (ARCHITECTURE.md 6.2 endpoint 5): GET /api/trains/nearby.

A rider who is on a train (and may be chasing the bag's train) taps theirs from a short list.
The answer becomes intake.on_vehicle, so the matcher never picks the rider's own train and the
meet check can compare the two trains' predictions (decision 9, 7.8).

At time t (now, or as_of):
  1. lines: every line that shares a platform_stop_id with the given line in the given
     direction, from the rail platforms in the schedule. Red and Gold share the trunk from
     Airport to Arts Center and Blue and Green from Ashby to King Memorial, so a rider on that
     stretch sees both lines, each train labeled with its own;
  2. trains: rail vehicles whose newest row in [t - 120 s, t] is on one of those lines and
     goes that direction, and is live by decision 8 against the rail feed's freshness reference
     (where.freshness_ref and where.vehicle_state, as /now judges it). Rail rows are stamped with
     the poll time, so 120 s is about six polls: a train missing from all of them is not running
     anywhere a rider can be on it. The live test adds t - ref <= 90 s, so a frozen feed lists no
     train at all (Oct 9, 18:25 UTC: the newest poll was 102 s old and 8 trains looked live).
     A NULL direction falls back to the train's archived prediction, then to the direction whose
     line ends at its DESTINATION. A train whose direction is still unknown is kept when it names
     a destination (the rider can judge it from the label, and it may be theirs), shown with
     direction null, and never preselected. One with neither is left out: nothing would tell
     the rider which way it is going;
  3. next_station: the soonest prediction at or after t in the newest archived rail poll at or
     before t (railpred), else null. destination_label: the DESTINATION's display name, or the
     line's end in that direction when the feed named none;
  4. distance_m, only when the rider sent lat and lon: metres to the train's newest coordinates
     in the same 120 s (a keyed-only poll has none, so it can be older than the row in step 2);
  5. preselect, at most one train: the nearest one, and only if accuracy_m <= 100, fix_age_s < 30,
     it is within 600 m and its direction is known;
  6. at most 12 trains: nearest first with a location, else soonest next station first.

as_of never reads a live-only source: rows and the archived poll are both at or before t. Rail
rows are stamped with their poll time (ts = polled_at), so ts <= t also keeps out every row a
later poll delivered.
Privacy (decision 16): the rider's lat, lon, accuracy and fix age are used in this call only.
Nothing here logs or stores them, and the response carries distances, never coordinates.

The read has a Postgres path and a Python twin side by side; tests/test_trains.py holds them
to the same answers on the same rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .railpred import RailFileCache, StationNames, TrainPrediction, rail_predictions_at
from .resolver import LINES
from .store import haversine_m
from .where import freshness_ref, vehicle_state

DIRECTIONS = ("N", "S", "E", "W")
LOOKBACK = timedelta(seconds=120)
MAX_TRAINS = 12
PRESELECT_ACCURACY_M = 100
PRESELECT_FIX_AGE_S = 30            # strictly under
PRESELECT_DISTANCE_M = 600


class BadTrainsRequest(ValueError):
    """A line and direction the schedule has no trains for, or half a location: 400 bad_request."""


# ----------------------------------------------------------------------------- static: which lines

def shared_lines(index, line: str, direction: str) -> list[str]:
    """`line` and every line that stops at one of its platforms going the same direction
    (the rail_platforms join of 7.8, from the schedule the API holds)."""
    mine = {p["platform_stop_id"] for p in index.rail_platforms if p["line"] == line and p["direction"] == direction}
    found = {p["line"] for p in index.rail_platforms if p["direction"] == direction and p["platform_stop_id"] in mine}
    return sorted(found, key=LINES.index)


def _line_end(index, line: str, direction: str) -> str | None:
    """Where a train on `line` going `direction` ends a full run (the last platform's station)."""
    plats = [p for p in index.rail_platforms if p["line"] == line and p["direction"] == direction]
    return max(plats, key=lambda p: p["seq"])["station_name"] if plats else None


def _direction_toward(index, line: str, destination_parent: str | None) -> str | None:
    """The one direction of `line` whose full run ends at this station, if exactly one does."""
    if destination_parent is None:
        return None
    ends = set()
    for d in DIRECTIONS:
        plats = [p for p in index.rail_platforms if p["line"] == line and p["direction"] == d]
        if plats and max(plats, key=lambda p: p["seq"])["parent_stop_id"] == destination_parent:
            ends.add(d)
    return ends.pop() if len(ends) == 1 else None


# ----------------------------------------------------------------------------- the read: Postgres and its Python twin

@dataclass(frozen=True, slots=True)
class RecentTrain:
    vehicle_id: str
    ts: datetime                         # its newest row in [t - 120 s, t]
    line: str | None                     # from that row
    direction: str | None
    destination: str | None
    lon: float | None                    # its newest coordinates in the same window
    lat: float | None


RECENT_SQL = """
WITH recent AS (
  SELECT vehicle_id, source, ts, route_short, direction, destination, lon, lat
  FROM vehicle_positions
  WHERE mode = 'rail' AND ts >= %(t)s - interval '120 seconds' AND ts <= %(t)s
), latest AS (
  SELECT DISTINCT ON (vehicle_id) vehicle_id, ts, route_short, direction, destination
  FROM recent ORDER BY vehicle_id, ts DESC, source
), fix AS (
  SELECT DISTINCT ON (vehicle_id) vehicle_id, lon, lat
  FROM recent WHERE lon IS NOT NULL AND lat IS NOT NULL
  ORDER BY vehicle_id, ts DESC, source
)
SELECT l.vehicle_id, l.ts, l.route_short, l.direction, l.destination, f.lon, f.lat
FROM latest l LEFT JOIN fix f USING (vehicle_id)
ORDER BY l.vehicle_id
"""


def _newer(r, cur) -> bool:
    """SQL's ORDER BY ts DESC, source: the later row, and on a tie the smaller source."""
    return cur is None or r.ts > cur.ts or (r.ts == cur.ts and r.source < cur.source)


def recent_trains(store, t: datetime) -> list[RecentTrain]:
    """Every train with a row in [t - 120 s, t]: its newest row, and its newest coordinates there."""
    if hasattr(store, "_q"):
        return [RecentTrain(r[0], r[1], r[2], r[3].strip() if r[3] else None, r[4], r[5], r[6])
                for r in store._q(RECENT_SQL, {"t": t})]
    latest: dict[str, object] = {}
    fix: dict[str, object] = {}
    for r in store._rows_for("rail", t - LOOKBACK, t):
        if r.mode != "rail" or not (t - LOOKBACK <= r.ts <= t):
            continue
        if _newer(r, latest.get(r.vehicle_id)):
            latest[r.vehicle_id] = r
        if r.lon is not None and r.lat is not None and _newer(r, fix.get(r.vehicle_id)):
            fix[r.vehicle_id] = r
    out = []
    for vid in sorted(latest):
        r, f = latest[vid], fix.get(vid)
        out.append(RecentTrain(vid, r.ts, r.route_short, r.direction.strip() if r.direction else None, r.destination,
                               f.lon if f is not None else None, f.lat if f is not None else None))
    return out


# ----------------------------------------------------------------------------- the endpoint's core

def check_request(index, line: str, direction: str, lat: float | None, lon: float | None) -> list[str]:
    """The lines to show, or BadTrainsRequest for a request no train can answer."""
    if line not in LINES:
        raise BadTrainsRequest(f"line must be one of {', '.join(LINES)}")
    if direction not in DIRECTIONS:
        raise BadTrainsRequest("direction must be N, S, E or W")
    lines = shared_lines(index, line, direction)
    if not lines:
        runs = sorted({p["direction"] for p in index.rail_platforms if p["line"] == line})
        raise BadTrainsRequest(f"{line} trains run {' or '.join(runs) or 'nowhere in this schedule'}, not {direction}")
    if (lat is None) != (lon is None):
        raise BadTrainsRequest("give lat and lon together, or neither")
    return lines


def trains_nearby(store, index, line: str, direction: str, t: datetime, *, as_of: bool, data_dir: Path,
                  lat: float | None = None, lon: float | None = None, accuracy_m: float | None = None,
                  fix_age_s: float | None = None, rail_files: RailFileCache | None = None) -> dict:
    """The /trains/nearby body (models.TrainsNearby) at t. Raises BadTrainsRequest (400) before
    reading anything when the request can't be answered. Database errors propagate."""
    if t.tzinfo is None:
        raise ValueError("t must be timezone-aware")
    lines = check_request(index, line, direction, lat, lon)
    names = StationNames.from_index(index)
    snap = rail_predictions_at(data_dir, t, names=names, cache=rail_files)
    predictions = snap.trains if snap is not None else {}
    with_gps = lat is not None and lon is not None
    ref = freshness_ref(store, "rail", t, as_of=as_of, data_dir=data_dir)

    found = []
    for tr in recent_trains(store, t):
        if vehicle_state(t, tr.ts, ref) != "live":
            continue
        pred = predictions.get(tr.vehicle_id)
        item = _train(index, names, tr, pred, lines, direction)
        if item is None:
            continue
        dist = haversine_m(lon, lat, tr.lon, tr.lat) if with_gps and tr.lon is not None and tr.lat is not None else None
        item["distance_m"] = round(dist) if dist is not None else None
        found.append((dist, item))

    def order(entry):
        dist, item = entry
        eta = item["next_station"]["eta"] if item["next_station"] else None
        by_eta = (eta is None, eta or datetime.min.replace(tzinfo=t.tzinfo))
        by_dist = (dist is None, dist or 0.0) if with_gps else ()
        return (*by_dist, *by_eta, item["vehicle_key"])

    found.sort(key=order)
    trusted_fix = (accuracy_m is not None and accuracy_m <= PRESELECT_ACCURACY_M
                   and fix_age_s is not None and fix_age_s < PRESELECT_FIX_AGE_S)
    if trusted_fix and found:
        # only the nearest train can be the rider's; if it does not qualify, nothing is preselected
        dist, item = found[0]
        item["preselect"] = dist is not None and dist <= PRESELECT_DISTANCE_M and item["direction"] is not None
    return {"trains": [item for _, item in found[:MAX_TRAINS]], "source": "as_of" if as_of else "live"}


def _train(index, names: StationNames, tr: RecentTrain, pred: TrainPrediction | None, lines: list[str],
           direction: str) -> dict | None:
    """One NearbyTrain without its distance, or None when it is not on these lines going this way."""
    line = tr.line if tr.line in LINES else (pred.line if pred is not None and pred.line in LINES else None)
    if line not in lines:
        return None
    destination = tr.destination or (pred.destination if pred is not None else None)
    heading = tr.direction or (pred.direction if pred is not None else None) \
        or _direction_toward(index, line, names.parent(destination))
    if heading is not None and heading != direction:
        return None
    label = names.label(destination) if destination else (_line_end(index, line, heading) if heading else None)
    if label is None:
        return None
    nxt = min(pred.stops, key=lambda s: s.eta) if pred is not None and pred.stops else None
    return {"vehicle_key": f"rail:{tr.vehicle_id}", "line": line, "direction": heading, "destination_label": label,
            "next_station": {"place_id": nxt.place_id, "label": nxt.label, "eta": nxt.eta} if nxt else None,
            "distance_m": None, "preselect": False}
