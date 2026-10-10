"""The rule-based time parser for /api/parse (feature 7): every phrase in timeparse.py's table, on an ordinary
afternoon, at New York midnight and across both DST changes; bare hours never land in the future; windows match the
time buttons where the phrase means the same thing; and the finder that the regex fallback uses picks out time
phrases without reading route numbers or "Five Points" as times."""

from datetime import datetime, timedelta, timezone

import pytest

from backtrack_api.timeparse import MAX_WINDOW_MIN, find_time_phrases, instants, read_time
from backtrack_api.timewindow import BUTTONS
from backtrack_worker.timeutil import ATL

UTC = timezone.utc
AFTERNOON = datetime(2026, 10, 9, 17, 30, tzinfo=UTC)      # Fri Oct 9, 1:30 PM in New York (EDT)
MIDNIGHT = datetime(2026, 10, 10, 4, 0, tzinfo=UTC)        # Sat Oct 10, 12:00 AM in New York
FALL_BACK = datetime(2026, 11, 1, 6, 30, tzinfo=UTC)       # Sun Nov 1, 1:30 AM EST: the second 1:30 that night
SPRING_FORWARD = datetime(2026, 3, 8, 7, 30, tzinfo=UTC)   # Sun Mar 8, 3:30 AM EDT: 2:00 to 2:59 never happened

PHRASES = ("around 1", "1:15", "at 3pm", "3 pm", "noon", "20 min ago", "an hour ago", "half an hour ago", "just now",
           "this morning", "earlier", "last night", "yesterday at 5", "Thursday around 6")


def u(month: int, day: int, hour: int, minute: int = 0, year: int = 2026) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


# phrase -> (center in UTC, half-width in minutes, day label)
EXPECTED = {
    AFTERNOON: {
        "around 1": (u(10, 9, 17), 30, "today"),                 # 1:00 PM, the most recent past 1 o'clock
        "1:15": (u(10, 9, 17, 15), 10, "today"),
        "at 3pm": (u(10, 8, 19), 15, "yesterday"),               # 3 PM hasn't come today: 24 h back
        "3 pm": (u(10, 8, 19), 15, "yesterday"),
        "noon": (u(10, 9, 16), 15, "today"),
        "20 min ago": (u(10, 9, 17, 10), 10, "today"),
        "an hour ago": (u(10, 9, 16, 30), 30, "today"),
        "half an hour ago": (u(10, 9, 17), 15, "today"),
        "just now": (u(10, 9, 17, 25), 5, "today"),
        "this morning": (u(10, 9, 12, 30), 210, "today"),        # 5 AM to noon: 8:30 AM +- 3.5 h
        "earlier": (u(10, 9, 16), 90, "today"),
        "last night": (u(10, 9, 2), 180, "yesterday"),           # Thu 7 PM to Fri 1 AM: 10 PM +- 3 h
        "yesterday at 5": (u(10, 8, 21), 15, "yesterday"),       # 5 PM, the later reading of that day
        "Thursday around 6": (u(10, 8, 22), 30, "yesterday"),
    },
    MIDNIGHT: {
        "around 1": (u(10, 9, 17), 30, "yesterday"),
        "1:15": (u(10, 9, 17, 15), 10, "yesterday"),
        "at 3pm": (u(10, 9, 19), 15, "yesterday"),
        "3 pm": (u(10, 9, 19), 15, "yesterday"),
        "noon": (u(10, 9, 16), 15, "yesterday"),
        "20 min ago": (u(10, 10, 3, 40), 10, "yesterday"),
        "an hour ago": (u(10, 10, 3), 30, "yesterday"),
        "half an hour ago": (u(10, 10, 3, 30), 15, "yesterday"),
        "just now": (u(10, 10, 3, 55), 5, "yesterday"),
        "this morning": (u(10, 9, 12, 30), 210, "yesterday"),    # today's morning hasn't begun: Friday's
        "earlier": (u(10, 10, 2, 30), 90, "yesterday"),
        "last night": (u(10, 10, 1, 30), 150, "yesterday"),      # Fri 7 PM to now (midnight): 9:30 PM +- 2.5 h
        "yesterday at 5": (u(10, 9, 21), 15, "yesterday"),
        "Thursday around 6": (u(10, 8, 22), 30, "Thursday"),
    },
    FALL_BACK: {
        "around 1": (u(11, 1, 6), 30, "today"),                  # 1:00 AM EST, the later of the two 1:00s
        "1:15": (u(11, 1, 6, 15), 10, "today"),
        "at 3pm": (u(10, 31, 19), 15, "yesterday"),              # 3 PM EDT
        "3 pm": (u(10, 31, 19), 15, "yesterday"),
        "noon": (u(10, 31, 16), 15, "yesterday"),
        "20 min ago": (u(11, 1, 6, 10), 10, "today"),
        "an hour ago": (u(11, 1, 5, 30), 30, "today"),           # elapsed time: 1:30 AM EDT, the first 1:30
        "half an hour ago": (u(11, 1, 6), 15, "today"),
        "just now": (u(11, 1, 6, 25), 5, "today"),
        "this morning": (u(10, 31, 12, 30), 210, "yesterday"),
        "earlier": (u(11, 1, 5), 90, "today"),
        "last night": (u(11, 1, 2, 30), 210, "yesterday"),       # Sat 7 PM EDT to 1 AM EST: 7 h
        "yesterday at 5": (u(10, 31, 21), 15, "yesterday"),
        "Thursday around 6": (u(10, 29, 22), 30, "Thursday"),
    },
    SPRING_FORWARD: {
        "around 1": (u(3, 8, 6), 30, "today"),                   # 1:00 AM EST
        "1:15": (u(3, 8, 6, 15), 10, "today"),
        "at 3pm": (u(3, 7, 20), 15, "yesterday"),                # 3 PM EST
        "3 pm": (u(3, 7, 20), 15, "yesterday"),
        "noon": (u(3, 7, 17), 15, "yesterday"),
        "20 min ago": (u(3, 8, 7, 10), 10, "today"),
        "an hour ago": (u(3, 8, 6, 30), 30, "today"),            # 1:30 AM EST: an hour of elapsed time
        "half an hour ago": (u(3, 8, 7), 15, "today"),
        "just now": (u(3, 8, 7, 25), 5, "today"),
        "this morning": (u(3, 7, 13, 30), 210, "yesterday"),
        "earlier": (u(3, 8, 6), 90, "today"),
        "last night": (u(3, 8, 3), 180, "yesterday"),            # Sat 7 PM EST to Sun 1 AM EST
        "yesterday at 5": (u(3, 7, 22), 15, "yesterday"),
        "Thursday around 6": (u(3, 5, 23), 30, "Thursday"),
    },
}


@pytest.mark.parametrize("now", list(EXPECTED), ids=["afternoon", "ny_midnight", "fall_back", "spring_forward"])
@pytest.mark.parametrize("phrase", PHRASES)
def test_every_phrase_in_the_table_reads_in_new_york_time(now, phrase):
    r = read_time(phrase, now)
    assert r is not None, phrase
    assert (r.at, r.window_min, r.day_label) == EXPECTED[now][phrase]
    assert r.at.tzinfo is not None and r.at.utcoffset() == timedelta(0)


@pytest.mark.parametrize("phrase,minutes_ago,window", [
    ("just now", 5, 5), ("right now", 5, 5), ("a moment ago", 5, 5), ("a few minutes ago", 5, 5),
    ("a couple minutes ago", 5, 5), ("5 min ago", 5, 5), ("15 minutes ago", 15, 10), ("20min ago", 20, 10),
    ("45 mins ago", 45, 15), ("half an hour ago", 30, 15), ("a half hour ago", 30, 15), ("an hour ago", 60, 30),
    ("1 hour ago", 60, 30), ("an hour and a half ago", 90, 30), ("1.5 hours ago", 90, 30), ("2 hours ago", 120, 30),
    ("3 hours ago", 180, 60), ("a couple of hours ago", 120, 60), ("a few hours ago", 240, 120),
    ("earlier", 90, 90), ("earlier today", 90, 90), ("a while ago", 90, 90), ("like 20 min ago", 20, 10),
    ("about an hour ago", 60, 30), ("twenty minutes ago", 20, 10),
])
def test_relative_phrases_count_back_from_now_with_wider_windows_when_vaguer(phrase, minutes_ago, window):
    r = read_time(phrase, AFTERNOON)
    assert r.at == AFTERNOON - timedelta(minutes=minutes_ago)
    assert r.window_min == window


def test_phrases_that_mean_a_time_button_get_that_buttons_window():
    for phrase, button in (("just now", "under_10"), ("earlier", "not_sure"), ("a few hours ago", "over_2h")):
        r = read_time(phrase, AFTERNOON)
        ago, half = BUTTONS[button]
        assert (r.at, r.window_min) == (AFTERNOON - timedelta(minutes=ago), half), phrase
    # the "N min ago" brackets are the buttons' half-widths: under 10, 10 to 30, 30 to 60, 1 to 2 h
    assert [read_time(p, AFTERNOON).window_min for p in ("8 min ago", "25 min ago", "50 min ago", "100 min ago")] \
        == [BUTTONS[b][1] for b in ("under_10", "10_30", "30_60", "1_2h")]


@pytest.mark.parametrize("phrase,local,window", [
    ("1:15", "13:15", 10), ("about 1:15", "13:15", 20), ("at 1", "13:00", 15), ("around 1", "13:00", 30),
    ("1ish", "13:00", 30), ("1-ish", "13:00", 30), ("1 or so", "13:00", 30), ("at 11am", "11:00", 15),
    ("11 a.m.", "11:00", 15), ("9:40 AM", "09:40", 10), ("6 o'clock", "06:00", 15), ("noon", "12:00", 15),
    ("around noon", "12:00", 30), ("midnight", "00:00", 15), ("quarter past 1", "13:15", 10),
    ("half past 12", "12:30", 10), ("13:05", "13:05", 10), ("around one", "13:00", 30),
])
def test_clock_phrases_and_their_windows(phrase, local, window):
    r = read_time(phrase, AFTERNOON)
    assert r.at.astimezone(ATL).strftime("%H:%M") == local
    assert r.window_min == window


def test_a_bare_hour_is_the_most_recent_past_occurrence_never_the_future():
    start = datetime(2026, 10, 9, 4, 0, tzinfo=UTC)             # Fri 12:00 AM in New York
    for step in range(0, 48 * 60, 37):                          # two days in 37 min steps, across midnight twice
        now = start + timedelta(minutes=step)
        for hour in range(1, 13):
            for phrase in (f"around {hour}", f"at {hour}", f"{hour}:30", f"{hour} pm", f"{hour} am"):
                r = read_time(phrase, now)
                assert r is not None and r.at <= now, (phrase, now)
                assert now - r.at < timedelta(hours=24), (phrase, now)
    # "around 1" at 12:20 PM: 1 PM is 40 min off, outside its 30 min window, so the most recent past 1 o'clock
    r = read_time("around 1", datetime(2026, 10, 9, 16, 20, tzinfo=UTC))
    assert r.at.astimezone(ATL).strftime("%H:%M") == "01:00"


@pytest.mark.parametrize("phrase,said_at,window", [
    ("around 1", "12:50", 30),        # 1 PM is 10 min off, inside its 30 min window
    ("at 1", "12:50", 15),
    ("1:15", "13:08", 10),
    ("at 3pm", "14:55", 15),
    ("noon", "11:50", 15),
    ("midnight", "23:52", 15),        # tomorrow's midnight is 8 min off: now, not last midnight
])
def test_a_clock_still_within_its_window_of_now_reads_as_now_never_the_future(phrase, said_at, window):
    h, m = map(int, said_at.split(":"))
    now = datetime(2026, 10, 9, h, m, tzinfo=ATL).astimezone(UTC)
    r = read_time(phrase, now)
    assert (r.at, r.window_min, r.day_label) == (now, window, "today")


def test_a_named_day_that_is_over_never_reads_as_now():
    thursday_555pm = datetime(2026, 10, 8, 21, 55, tzinfo=UTC)
    assert read_time("yesterday at 6", thursday_555pm).at.astimezone(ATL).strftime("%a %H:%M") == "Wed 18:00"
    # a weekday takes its latest past reading on that day: this morning's 6, not "now" 5 min before 6 PM
    assert read_time("Thursday at 6", thursday_555pm).at.astimezone(ATL).strftime("%m-%d %H:%M") == "10-08 06:00"


def test_near_picks_the_past_reading_closest_to_the_other_anchor():
    yesterday_3pm = u(10, 8, 19)
    r = read_time("around 3:30", AFTERNOON, near=yesterday_3pm)              # alone it is 3:30 AM today
    assert read_time("around 3:30", AFTERNOON).at == u(10, 9, 7, 30)
    assert r.at == u(10, 8, 19, 30) and r.day_label == "yesterday"
    assert read_time("20 min ago", AFTERNOON, near=yesterday_3pm).at == u(10, 9, 17, 10)   # not a clock: unmoved
    assert read_time("around 3:30", AFTERNOON, near=AFTERNOON + timedelta(hours=5)).at <= AFTERNOON


def test_a_day_with_a_bare_hour_takes_its_later_reading_and_am_pm_or_a_part_of_day_picks():
    assert read_time("yesterday at 5", AFTERNOON).at.astimezone(ATL).strftime("%a %H:%M") == "Thu 17:00"
    assert read_time("yesterday at 5 am", AFTERNOON).at.astimezone(ATL).strftime("%a %H:%M") == "Thu 05:00"
    assert read_time("this morning around 9", AFTERNOON).at.astimezone(ATL).strftime("%a %H:%M") == "Fri 09:00"
    assert read_time("last night at 11", AFTERNOON).at.astimezone(ATL).strftime("%a %H:%M") == "Thu 23:00"
    assert read_time("last night at 12:30", AFTERNOON).at.astimezone(ATL).strftime("%a %H:%M") == "Fri 00:30"
    assert read_time("this morning at 3pm", AFTERNOON) is None      # a contradiction is not a time


def test_a_weekday_whose_hour_has_not_come_today_is_last_weeks():
    thursday_5am = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)          # Thu Oct 8, 5:00 AM
    r = read_time("Thursday around 6", thursday_5am)
    assert r.at.astimezone(ATL).strftime("%Y-%m-%d %H:%M") == "2026-10-01 18:00"
    assert r.day_label == "Thursday"
    r = read_time("Thursday around 6", datetime(2026, 10, 8, 10, 30, tzinfo=UTC))   # 6:30 AM: this morning's
    assert r.at.astimezone(ATL).strftime("%Y-%m-%d %H:%M") == "2026-10-08 06:00"


def test_spans_are_cut_at_now_and_capped_at_six_hours():
    r = read_time("this morning", datetime(2026, 10, 9, 13, 0, tzinfo=UTC))    # 9 AM: 5 AM to 9 AM
    assert r.at.astimezone(ATL).strftime("%H:%M") == "07:00" and r.window_min == 120
    r = read_time("yesterday", AFTERNOON)                                        # 5 AM to 1 AM: 20 h
    assert r.at.astimezone(ATL).strftime("%a %H:%M") == "Thu 15:00" and r.window_min == MAX_WINDOW_MIN
    assert read_time("Thursday", AFTERNOON).day_label == "yesterday"
    assert read_time("Wednesday", AFTERNOON).day_label == "Wednesday"
    assert read_time("today", datetime(2026, 10, 9, 7, 0, tzinfo=UTC)) is None  # 3 AM: today's day hasn't begun


def test_a_wall_time_that_never_happened_is_skipped():
    assert instants(datetime(2026, 3, 8).date(), 2, 30) == []
    assert len(instants(datetime(2026, 11, 1).date(), 1, 30)) == 2
    r = read_time("2:30", SPRING_FORWARD)                         # this morning's 2:30 never was: yesterday 2:30 PM
    assert r.at == u(3, 7, 19, 30)


@pytest.mark.parametrize("phrase", ["", "   ", "five points", "the 49", "route 1", "49", "my bag", "around",
                                    "at", "around 1 at", "at 25", "13 pm", "1:75", "tomorrow at 5", "blue line",
                                    "in 20 min", "next week", "ignore the rules"])
def test_anything_else_is_not_a_time(phrase):
    assert read_time(phrase, AFTERNOON) is None


def test_every_window_is_within_the_contracts_bounds():
    phrases = list(PHRASES) + ["yesterday", "Thursday", "tonight", "this afternoon", "this evening", "36 hours ago",
                               "a few hours ago", "5 min ago", "today"]
    for now in EXPECTED:
        for phrase in phrases:
            r = read_time(phrase, now)
            if r is not None:
                assert 1 <= r.window_min <= MAX_WINDOW_MIN, (phrase, now)
    assert read_time("40 hours ago", AFTERNOON) is None


# ----------------------------------------------------------------------------- the finder


def found(text: str) -> list[str]:
    return [text[a:b] for a, b in find_time_phrases(text)]


@pytest.mark.parametrize("text,phrases", [
    ("I left my bag on the 49 near GSU around 1", ["around 1"]),
    ("got off the red line at five points like 20 min ago, northbound, back car", ["like 20 min ago"]),
    ("got on at peachtree center at 1:15 p.m. and got off at college park", ["at 1:15 p.m"]),
    ("on the 5 yesterday", ["yesterday"]),
    ("route 12 at 3pm", ["at 3pm"]),
    ("bus 12 at noon near the airport", ["at noon"]),
    ("Thursday around 6 at north ave", ["Thursday around 6"]),
    ("at one of the stops around 3", ["around 3"]),
    ("got off at 5 points just now", ["just now"]),
    ("this morning on the 110, then again last night", ["this morning", "last night"]),
    ("yesterday at 5 on the gold line", ["yesterday at 5"]),
    ("I was on route 1 near the airport", []),
    ("the 49 at Five Points", []),
    ("got off the train at Five Points after 2 stations", []),            # a count, not a time
    ("got off at five points at 12 midnight", ["at 12 midnight"]),
    ("and now I can't find my phone", []),
])
def test_the_finder_picks_time_phrases_and_leaves_routes_and_places(text, phrases):
    assert found(text) == phrases


@pytest.mark.parametrize("phrase,local,window", [
    ("12 noon", "Fri 12:00", 15), ("around 12 noon", "Fri 12:00", 30), ("at 12 noon", "Fri 12:00", 15),
    ("12:00 noon", "Fri 12:00", 10), ("12 midnight", "Fri 00:00", 15), ("at 12 midnight", "Fri 00:00", 15),
])
def test_twelve_noon_and_twelve_midnight_are_noon_and_midnight(phrase, local, window):
    r = read_time(phrase, AFTERNOON)
    assert (r.at.astimezone(ATL).strftime("%a %H:%M"), r.window_min) == (local, window)
    assert found(f"got off at five points {phrase}") == [phrase]
    assert read_time("1 noon", AFTERNOON) is None and read_time("12 pm noon", AFTERNOON) is None


def test_earlier_with_a_part_of_the_day_reads_as_that_part():
    three_pm = datetime(2026, 10, 9, 19, 0, tzinfo=UTC)
    assert read_time("earlier this afternoon", three_pm) == read_time("this afternoon", three_pm)
    assert read_time("earlier this afternoon", three_pm).at.astimezone(ATL).strftime("%H:%M") == "13:30"


def test_a_bare_now_is_not_a_time_but_just_now_and_right_now_are():
    assert read_time("now", AFTERNOON) is None and read_time("about now", AFTERNOON) is None
    assert read_time("just now", AFTERNOON).window_min == read_time("right now", AFTERNOON).window_min == 5


def test_a_range_ago_reads_its_middle_wide_enough_for_both_ends():
    now = datetime(2026, 10, 10, 16, 0, tzinfo=timezone.utc)
    for phrase in ("5-10 minutes ago", "5 to 10 min ago", "about 5 - 10 mins ago", "five to ten minutes ago"):
        r = read_time(phrase, now)
        assert r is not None and r.kind == "ago", phrase
        assert r.at == now - timedelta(minutes=7.5) and r.window_min == 5, phrase   # 2.5 to 12.5 min ago
    r = read_time("2 or 3 hours ago", now)
    assert r.at == now - timedelta(minutes=150) and r.window_min == 60
    from backtrack_api.timeparse import is_time_phrase
    assert is_time_phrase("5-10 minutes ago")
