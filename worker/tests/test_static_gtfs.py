"""The schedule loader, on a synthetic feed with MARTA's quirks and on the real feed."""

from datetime import date
from pathlib import Path

import pytest

from backtrack_worker.places import normalize_place
from backtrack_worker.static_gtfs import DEFAULT_ZIP, build_feed, display_name

from gtfs_fixture import build_zip

FRI = date(2026, 10, 9)


@pytest.fixture(scope="module")
def seeds(tmp_path_factory):
    p = tmp_path_factory.mktemp("seeds") / "aliases.csv"
    p.write_text("# comment line\nalias,label,multi,targets\n"
                 "gsu,Georgia State University,true,stop:210555|station:510015\n"
                 "student center,GSU Student Center East,false,near:33.75267;-84.3849;120\n"
                 "nowhere,Nowhere,false,stop:000000\n")
    return p


@pytest.fixture(scope="module")
def feed(tmp_path_factory, seeds):
    z = build_zip(tmp_path_factory.mktemp("gtfs") / "feed.zip")
    return build_feed(z, window_start=FRI, days=4, alias_seeds=seeds,
                      extra_destinations={"Lindbergh Center", "Hamilton E. Holmes", "Mars Base"})


class TestServices:
    def test_window_picks_weekday_saturday_and_sunday_only(self, feed):
        assert feed.service_ids == ["3", "4", "5"]          # never-active 2 and holiday-only 45 left out
        assert "never" not in feed.trips and "holiday" not in feed.trips

    def test_holiday_service_is_included_when_its_day_is_in_the_window(self, tmp_path, seeds):
        z = build_zip(tmp_path / "f.zip")
        f = build_feed(z, window_start=date(2026, 11, 26), days=1, alias_seeds=seeds)
        assert f.service_ids == ["45", "5"]   # Thanksgiving swaps 5 for 45; 5 is Wednesday's (the day before)

    def test_the_day_before_the_window_is_loaded_for_after_midnight_trips(self, tmp_path, seeds):
        """Review: a Saturday-only window dropped Friday's trips that run past midnight."""
        z = build_zip(tmp_path / "f.zip")
        f = build_feed(z, window_start=date(2026, 10, 10), days=1, alias_seeds=seeds)
        assert "5" in f.service_ids and "rs1" in f.trips   # Friday's 25:10 train

    def test_window_with_no_service_is_an_error(self, tmp_path, seeds):
        z = build_zip(tmp_path / "f.zip")
        with pytest.raises(ValueError, match="no service"):
            build_feed(z, window_start=date(2027, 6, 1), days=3, alias_seeds=seeds)


class TestStopTimes:
    def test_times_become_seconds_including_padding_and_past_midnight(self, feed):
        rows = {(r[0], r[1]): r for r in feed.stop_times()}
        assert rows[("rn1", 1)][3] == 4 * 3600 + 40 * 60         # ' 4:40:00'
        assert rows[("rs1", 1)][3] == 25 * 3600 + 10 * 60        # '25:10:00'
        assert rows[("rn1", 1)][5] is True and rows[("rn1", 2)][5] is False   # timepoint

    def test_unknown_stops_are_skipped_and_counted(self, feed):
        assert all(r[2] != "999999" for r in feed.stop_times())
        assert any("stop_times rows skipped" in w for w in feed.warnings)
        assert feed.stop_time_count == sum(1 for _ in feed.stop_times())


class TestStopRoutes:
    def test_both_directions_of_route_49_with_headsigns(self, feed):
        at_gsu = {(r["route_short_name"], r["direction_id"]): r for r in feed.stop_routes if r["stop_id"] == "210555"}
        assert at_gsu[("49", 0)]["headsign"] == "NORTH AVE STATION" and at_gsu[("49", 0)]["n_trips"] == 2
        assert at_gsu[("49", 1)]["headsign"] == "MCDONOUGH BLVD"


class TestRailPlatforms:
    def by(self, feed, line, direction):
        return [p for p in feed.rail_platforms if p["line"] == line and p["direction"] == direction]

    def test_longest_trip_gives_ordered_platforms_with_termini(self, feed):
        red_s = self.by(feed, "RED", "S")
        assert [p["station_name"] for p in red_s] == ["North Springs", "Lindbergh Center", "Five Points", "Airport"]
        assert [p["is_terminus"] for p in red_s] == [True, False, False, True]   # the short turn is not a terminus here
        assert [p["seq"] for p in red_s] == [1, 2, 3, 4]

    def test_direction_letters_follow_the_line(self, feed):
        assert {p["direction"] for p in feed.rail_platforms if p["line"] in ("RED", "GOLD")} == {"N", "S"}

    def test_red_and_gold_share_five_points_but_not_lindbergh(self, feed):
        def platform(line, d, parent):
            return next(p["platform_stop_id"] for p in self.by(feed, line, d) if p["parent_stop_id"] == parent)
        assert platform("RED", "N", "510015") == platform("GOLD", "N", "510015") == "908986"
        assert platform("RED", "N", "510024") != platform("GOLD", "N", "510024")
        assert platform("RED", "S", "510037") != platform("GOLD", "S", "510037")   # separate arrival platforms


class TestStationMap:
    def test_api_spellings_and_destinations_map_to_parents(self, feed):
        m = {r["api_name"]: r for r in feed.rail_station_map}
        assert m["LINDBERGH STATION"]["parent_stop_id"] == "510024"           # API spelling
        assert m["Lindbergh Center"]["kind"] == "destination"                 # seen in the API
        assert m["Lindbergh"]["parent_stop_id"] == "510024"                   # from the short-turn headsign
        assert m["Airport"]["parent_stop_id"] == "510037" and m["Airport"]["display_name"] == "Airport"
        assert m["Five Points"]["kind"] == "destination"            # the short turn's last stop
        assert "Hamilton E. Holmes" not in m                                  # its station isn't in this feed
        assert any("Mars Base" in w for w in feed.warnings)


class TestAliases:
    def test_seeds_resolve_and_stations_are_added(self, feed):
        a = {x["alias"]: x for x in feed.place_aliases}
        assert a["gsu"]["multi"] and set(a["gsu"]["stop_ids"]) >= {"210555", "908986", "908976"}
        assert a["student center"]["stop_ids"] == ["210555"]                  # 30 m away; Courtland is not
        assert a["five points"]["stop_ids"] == ["908976", "908986"]           # auto, platforms only
        assert a["five points"]["rail_stop_ids"] == ["908976", "908986"] and a["five points"]["bus_stop_ids"] == []
        assert a["gsu"]["bus_stop_ids"] == ["210555"]                         # the bus stop, not the platforms
        assert a["lindbergh"]["label"] == "Lindbergh Center"                  # API spelling, normalized
        assert normalize_place("the Lindbergh station") in a                  # riders' phrasing finds it
        assert "nowhere" not in a and any("nowhere" in w for w in feed.warnings)


@pytest.mark.parametrize("raw,norm", [
    ("PIEDMONT AVE SE @ GILMER ST SE", "piedmont avenue se gilmer street se"),
    ("Piedmont Ave & Gilmer St", "piedmont avenue gilmer street"),
    ("FIVE POINTS STATION", "five points"),
    ("the Airport", "airport"),
    ("MLK Jr Dr", "martin luther king jr drive"),
    ("Station", "station"),        # never normalize to nothing
])
def test_normalize_place(raw, norm):
    assert normalize_place(raw) == norm


def test_display_names():
    assert display_name("LAKEWOOD-FT MCPHERSON STATION") == "Lakewood-Ft McPherson"
    assert display_name("SEC DISTRICT STATION") == "SEC District"


# --- the real MARTA feed (PREP-REPORT facts); runs when data/static/google_transit.zip exists ---

real = pytest.mark.skipif(not DEFAULT_ZIP.is_file(), reason="run load-static --download --dry-run first")


@pytest.fixture(scope="module")
def marta():
    return build_feed(DEFAULT_ZIP, window_start=FRI, days=4)


@real
class TestRealFeed:
    def test_event_weekend_services_and_sizes(self, marta):
        assert marta.service_ids == ["3", "4", "5"]
        assert len(marta.trips) == 22460 and marta.stop_time_count == 1038732
        assert not marta.warnings

    def test_rail_lines_have_the_verified_station_counts(self, marta):
        counts = {}
        for p in marta.rail_platforms:
            counts[(p["line"], p["direction"])] = counts.get((p["line"], p["direction"]), 0) + 1
        assert counts == {("RED", "N"): 19, ("RED", "S"): 19, ("GOLD", "N"): 18, ("GOLD", "S"): 18,
                          ("BLUE", "E"): 15, ("BLUE", "W"): 15, ("GREEN", "E"): 9, ("GREEN", "W"): 9}

    def test_verified_platform_ids(self, marta):
        p = {(x["line"], x["direction"], x["parent_stop_id"]): x["platform_stop_id"] for x in marta.rail_platforms}
        assert p[("RED", "N", "510040")] == "908601" and p[("RED", "S", "510040")] == "907842"   # Peachtree Center
        assert p[("RED", "N", "510015")] == p[("GOLD", "N", "510015")] == "908986"               # Five Points shared
        assert p[("RED", "S", "510015")] == p[("GOLD", "S", "510015")] == "908976"
        assert (p[("RED", "N", "510024")], p[("RED", "S", "510024")]) == ("907775", "907823")    # Lindbergh, per line
        assert (p[("GOLD", "N", "510024")], p[("GOLD", "S", "510024")]) == ("908055", "908054")
        assert p[("RED", "N", "510037")] == p[("GOLD", "N", "510037")] == "908695"               # Airport departure
        assert (p[("RED", "S", "510037")], p[("GOLD", "S", "510037")]) == ("906837", "906647")   # Airport arrivals

    def test_red_and_gold_split_after_lindbergh(self, marta):
        def stations(line):
            return [x["parent_stop_id"] for x in marta.rail_platforms if x["line"] == line and x["direction"] == "N"]
        red, gold = stations("RED"), stations("GOLD")
        shared = [s for s in red if s in gold]
        assert len(shared) == 14 and shared[-1] == "510024"                  # Airport through Lindbergh

    def test_all_38_stations_and_headsign_destinations_map(self, marta):
        m = {r["api_name"]: r["parent_stop_id"] for r in marta.rail_station_map}
        assert {r["parent_stop_id"] for r in marta.rail_station_map if r["kind"] == "station"} == \
            {p for p in marta.stops if p.startswith("5100") and p not in ("510031", "510035")}
        assert m["OMNI DOME STATION"] == "510039"
        assert m["Candler Park"] == "510014" and m["H E Holmes"] == "510017" and m["Lindbergh"] == "510024"
        assert m["EDGEWOOD CANDLER PARK STATION"] == "510014"          # the API's hyphen-free form

    def test_every_spelling_the_live_api_sent_is_in_the_table_without_the_archive(self, marta):
        """Review: the exact-match table missed 'EDGEWOOD CANDLER PARK STATION' and every short
        turn not seen in recent polls. Built here with NO archive extras."""
        import json

        names = {r["api_name"] for r in marta.rail_station_map}
        fix = Path(__file__).parent / "fixtures"
        seen = {(k, r[k]) for f in ("rail_keyed.json", "rail_open.json")
                for r in json.loads((fix / f).read_text()) for k in ("STATION", "DESTINATION") if r.get(k)}
        assert {v for _, v in seen} <= names
        for dest in ("King Memorial", "Vine City", "Five Points", "Lindbergh Center", "Edgewood-Candler Park",
                     "Hamilton E. Holmes", "Airport", "North Springs", "Doraville", "Indian Creek", "Bankhead"):
            assert dest in names, dest

    def test_alias_stop_lists_are_split_by_mode(self, marta):
        a = {x["alias"]: x for x in marta.place_aliases}
        rail = set(a["five points"]["rail_stop_ids"])
        assert rail == {"908976", "908981", "908986", "908990"}
        assert a["five points"]["bus_stop_ids"] and not rail & set(a["five points"]["bus_stop_ids"])

    @pytest.mark.parametrize("seen", ["Airport", "Bankhead", "Doraville", "Edgewood-Candler Park", "Five Points",
                                      "Hamilton E. Holmes", "Indian Creek", "Lindbergh Center", "North Springs",
                                      "Vine City"])
    def test_every_destination_the_live_api_used_maps(self, seen):
        """The 11 DESTINATION values in the Oct 8-9 archive (PREP + recorder), incl. short turns."""
        from backtrack_worker.connectors.rail_stations import station_id_for

        assert station_id_for(seen) is not None

    def test_routes_at_the_venue_stop(self, marta):
        """PREP section 5: stop 210555 (70 m from Student Center East) serves 20, 21, 22, 42, 49, 55,
        and route 49 there only runs northbound, toward North Ave (the field-test ride)."""
        here = {(r["route_short_name"], r["direction_id"], r["headsign"]) for r in marta.stop_routes
                if r["stop_id"] == "210555"}
        assert {route for route, _, _ in here} == {"20", "21", "22", "42", "49", "55"}
        assert {(d, h) for route, d, h in here if route == "49"} == {(0, "North Ave Stn")}

    def test_the_49_near_peachtree_center_is_courtland_at_ellis(self, marta):
        """The demo clue: route 49's closest stop to Peachtree Center's platforms (~305 m, PREP section 1)."""
        from backtrack_worker.static_gtfs import _haversine_m

        plats = [marta.stops[s] for s in ("908601", "907842")]
        stops49 = {r["stop_id"] for r in marta.stop_routes if r["route_short_name"] == "49"}
        dist = {s: min(_haversine_m(p["lon"], p["lat"], marta.stops[s]["lon"], marta.stops[s]["lat"]) for p in plats)
                for s in stops49}
        nearest = min(dist, key=dist.get)
        assert "COURTLAND" in marta.stops[nearest]["stop_name"] and "ELLIS" in marta.stops[nearest]["stop_name"]
        assert 250 < dist[nearest] < 400

    def test_venue_aliases_resolve(self, marta):
        a = {x["alias"]: x for x in marta.place_aliases}
        assert "210555" in a["gsu"]["stop_ids"] and a["gsu"]["multi"]
        assert a["student center"]["stop_ids"] and "210555" in a["student center"]["stop_ids"]
        assert normalize_place("the airport") == "airport" and a["airport"]["stop_ids"]
        assert {"908695", "906837", "906647"} <= set(a["airport"]["stop_ids"])


class TestDownload:
    """Review: --download replaced the known-good zip before checking the new one."""

    def run(self, monkeypatch, tmp_path, body, extra=()):
        import backtrack_worker.connectors.base as base
        from backtrack_worker.cli import main
        from backtrack_worker.connectors.base import HttpResponse

        monkeypatch.setattr(base, "http_get", lambda *a, **k: HttpResponse(200, body, {}))
        monkeypatch.setenv("BACKTRACK_DATA_DIR", str(tmp_path / "data"))
        monkeypatch.setenv("BACKTRACK_ENV_FILE", str(tmp_path / "none"))
        current = tmp_path / "google_transit.zip"
        build_zip(current)
        before = current.read_bytes()
        try:
            code = main(["load-static", "--zip", str(current), "--download", "--dry-run", *extra])
        except SystemExit as e:
            code = e.code
        return current, before, code

    def test_a_corrupt_download_is_rejected_and_the_old_zip_kept(self, monkeypatch, tmp_path):
        current, before, code = self.run(monkeypatch, tmp_path, b"PK\x03\x04 this is not a zip")
        assert code not in (0, None) and current.read_bytes() == before
        assert not (tmp_path / "google_transit.new.zip").exists()

    def test_a_good_download_is_staged_but_not_swapped_on_dry_run(self, monkeypatch, tmp_path):
        good = build_zip(tmp_path / "upstream.zip").read_bytes()
        current, before, code = self.run(monkeypatch, tmp_path, good, ("--start", "2026-10-09", "--days", "2"))
        assert code == 0 and current.read_bytes() == before
        assert (tmp_path / "google_transit.new.zip").read_bytes() == good
