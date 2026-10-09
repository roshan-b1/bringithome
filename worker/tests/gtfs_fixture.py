"""A tiny synthetic GTFS feed with MARTA's quirks, built as a zip in a temp dir."""

from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path


def _csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def build_zip(path: Path) -> Path:
    routes = [
        {"route_id": "29229", "route_short_name": "RED", "route_long_name": "RED", "route_type": "1", "route_color": "CE242B"},
        {"route_id": "29227", "route_short_name": "GOLD", "route_long_name": "GOLD", "route_type": "1", "route_color": "D4A723"},
        {"route_id": "29170", "route_short_name": "49", "route_long_name": "McDonough Blvd", "route_type": "3", "route_color": ""},
    ]
    S = lambda sid, name, lat, lon, lt="", parent="": {  # noqa: E731
        "stop_id": sid, "stop_name": name, "stop_lat": lat, "stop_lon": lon,
        "location_type": lt, "parent_station": parent}
    stops = [
        S("510037", "AIRPORT STATION", "33.6408", "-84.4462", "1"),
        S("908695", "AIRPORT STATION", "33.6410", "-84.4463", "", "510037"),   # shared departure platform
        S("906837", "AIRPORT STATION", "33.6407", "-84.4461", "", "510037"),   # Red arrival
        S("906647", "AIRPORT STATION", "33.6406", "-84.4460", "", "510037"),   # Gold arrival
        S("510015", "FIVE POINTS STATION", "33.75387", "-84.39157", "1"),
        S("908986", "FIVE POINTS STATION", "33.7540", "-84.3916", "", "510015"),  # N, Red + Gold
        S("908976", "FIVE POINTS STATION", "33.7537", "-84.3915", "", "510015"),  # S, Red + Gold
        S("510024", "LINDBERGH CENTER STATION", "33.82291", "-84.36947", "1"),
        S("907775", "LINDBERGH CENTER STATION", "33.8230", "-84.3695", "", "510024"),  # Red N
        S("907823", "LINDBERGH CENTER STATION", "33.8228", "-84.3694", "", "510024"),  # Red S
        S("908055", "LINDBERGH CENTER STATION", "33.8231", "-84.3696", "", "510024"),  # Gold N
        S("908054", "LINDBERGH CENTER STATION", "33.8227", "-84.3693", "", "510024"),  # Gold S
        S("510028", "NORTH SPRINGS STATION", "33.94498", "-84.3572", "1"),
        S("909001", "NORTH SPRINGS STATION", "33.9450", "-84.3572", "", "510028"),
        S("510010", "DORAVILLE STATION", "33.90296", "-84.28026", "1"),
        S("909002", "DORAVILLE STATION", "33.9030", "-84.2803", "", "510010"),
        S("210555", "PIEDMONT AVE SE @ GILMER ST SE", "33.75300", "-84.38490"),
        S("212000", "COURTLAND ST NE @ ELLIS ST NE", "33.75980", "-84.38500"),
    ]
    calendar = [
        {"service_id": "5", "monday": "1", "tuesday": "1", "wednesday": "1", "thursday": "1", "friday": "1",
         "saturday": "0", "sunday": "0", "start_date": "20260822", "end_date": "20261211"},
        {"service_id": "3", "monday": "0", "tuesday": "0", "wednesday": "0", "thursday": "0", "friday": "0",
         "saturday": "1", "sunday": "0", "start_date": "20260822", "end_date": "20261211"},
        {"service_id": "4", "monday": "0", "tuesday": "0", "wednesday": "0", "thursday": "0", "friday": "0",
         "saturday": "0", "sunday": "1", "start_date": "20260822", "end_date": "20261211"},
        {"service_id": "2", "monday": "0", "tuesday": "0", "wednesday": "0", "thursday": "0", "friday": "0",
         "saturday": "0", "sunday": "0", "start_date": "20260822", "end_date": "20261211"},   # never active
    ]
    calendar_dates = [
        {"service_id": "45", "date": "20261126", "exception_type": "1"},   # holiday-only (Thanksgiving)
        {"service_id": "5", "date": "20261126", "exception_type": "2"},
    ]
    T = lambda tid, route, svc, d, head, block="b1", shape="": {  # noqa: E731
        "trip_id": tid, "route_id": route, "service_id": svc, "direction_id": d, "trip_headsign": head,
        "block_id": block, "shape_id": shape}
    trips = [
        T("rn1", "29229", "5", "0", "RED NORTHBOUND TO NORTH SPRINGS STATION"),
        T("rs1", "29229", "5", "1", "RED SOUTHBOUND TO AIRPORT STATION"),
        T("rs_turn", "29229", "5", "1", "RED SOUTHBOUND TO LINDBERGH STATION"),   # short turn
        T("gn1", "29227", "3", "0", "GOLD NORTHBOUND TO DORAVILLE STATION"),
        T("gs1", "29227", "3", "1", "GOLD SOUTHBOUND TO AIRPORT STATION"),
        T("b49n", "29170", "5", "0", "NORTH AVE STATION", "blk49", "sh49"),
        T("b49n2", "29170", "3", "0", "NORTH AVE STATION", "blk49", "sh49"),
        T("b49s", "29170", "4", "1", "MCDONOUGH BLVD", "blk49", "sh49"),
        T("never", "29170", "2", "0", "NEVER RUNS", "blkx", "sh49"),
        T("holiday", "29170", "45", "0", "HOLIDAY ONLY", "blky", "sh49"),
    ]
    st = []

    def run(tid, stops_, start_s):
        for i, sid in enumerate(stops_):
            t = start_s + 120 * i
            hhmmss = f"{t // 3600:2d}:{t % 3600 // 60:02d}:{t % 60:02d}"   # ' 4:40:00' style padding
            st.append({"trip_id": tid, "arrival_time": hhmmss, "departure_time": hhmmss, "stop_id": sid,
                       "stop_sequence": str(i + 1), "timepoint": "1" if i == 0 else "0"})

    run("rn1", ["908695", "908986", "907775", "909001"], 4 * 3600 + 40 * 60)
    run("rs1", ["909001", "907823", "908976", "906837"], 25 * 3600 + 10 * 60)   # past midnight: 25:10:00
    run("rs_turn", ["907823", "908976"], 21 * 3600)
    run("gn1", ["908695", "908986", "908055", "909002"], 10 * 3600)
    run("gs1", ["909002", "908054", "908976", "906647"], 11 * 3600)
    run("b49n", ["210555", "212000"], 9 * 3600)
    run("b49n2", ["210555", "212000"], 10 * 3600)
    run("b49s", ["212000", "210555"], 12 * 3600)
    run("never", ["210555", "212000"], 9 * 3600)
    run("holiday", ["210555", "212000"], 9 * 3600)
    st.append({"trip_id": "b49n", "arrival_time": "", "departure_time": "", "stop_id": "999999",
               "stop_sequence": "9", "timepoint": ""})   # unknown stop: skipped with a warning
    shapes = [{"shape_id": "sh49", "shape_pt_lat": lat, "shape_pt_lon": lon, "shape_pt_sequence": str(i)}
              for i, (lat, lon) in enumerate([("33.7530", "-84.3849"), ("33.7560", "-84.3850"), ("33.7598", "-84.3850")])]
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("routes.txt", _csv(routes))
        z.writestr("stops.txt", _csv(stops))
        z.writestr("calendar.txt", _csv(calendar))
        z.writestr("calendar_dates.txt", _csv(calendar_dates))
        z.writestr("trips.txt", _csv(trips))
        z.writestr("stop_times.txt", _csv(st))
        z.writestr("shapes.txt", _csv(shapes))
    return path
