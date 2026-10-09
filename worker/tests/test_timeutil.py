from datetime import date, datetime, timezone

import pytest

from backtrack_worker.timeutil import (
    ATL,
    gtfs_time_to_seconds,
    parse_marta_rail_time,
    parse_user_clock,
    service_day_candidates,
    service_day_start,
)

UTC = timezone.utc


def local(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=ATL).astimezone(UTC)


class TestRailTime:
    def test_daylight_time_is_utc_minus_4(self):
        assert parse_marta_rail_time("10/08/2026 11:51:32 PM") == datetime(2026, 10, 9, 3, 51, 32, tzinfo=UTC)

    def test_standard_time_is_utc_minus_5(self):
        assert parse_marta_rail_time("01/15/2026 03:00:00 PM") == datetime(2026, 1, 15, 20, 0, tzinfo=UTC)

    def test_noon_and_midnight(self):
        assert parse_marta_rail_time("10/10/2026 12:00:00 PM") == datetime(2026, 10, 10, 16, 0, tzinfo=UTC)
        assert parse_marta_rail_time("10/10/2026 12:00:00 AM") == datetime(2026, 10, 10, 4, 0, tzinfo=UTC)

    def test_fall_back_repeated_hour_takes_first_occurrence(self):
        # Nov 1 2026, 1:30 AM happens twice; we pick the daylight-time one.
        assert parse_marta_rail_time("11/01/2026 01:30:00 AM") == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)

    def test_whitespace_tolerated_and_garbage_rejected(self):
        assert parse_marta_rail_time(" 10/08/2026 11:51:32 PM ").tzinfo == UTC
        with pytest.raises(ValueError):
            parse_marta_rail_time("11:51:32 PM")


class TestUserClock:
    """CLAUDE.md: parse user times as America/New_York (riders say "3:10", they mean Atlanta time)."""

    NOW = local(2026, 10, 10, 15, 40)  # Saturday 3:40 PM in Atlanta

    def test_bare_time_means_most_recent_past_occurrence(self):
        assert parse_user_clock("3:10", self.NOW) == local(2026, 10, 10, 15, 10)

    def test_explicit_pm_am_and_24h(self):
        assert parse_user_clock("3:10pm", self.NOW) == local(2026, 10, 10, 15, 10)
        assert parse_user_clock("3:10 p.m.", self.NOW) == local(2026, 10, 10, 15, 10)
        assert parse_user_clock("15:10", self.NOW) == local(2026, 10, 10, 15, 10)
        assert parse_user_clock("3:10 am", self.NOW) == local(2026, 10, 10, 3, 10)
        assert parse_user_clock("3 pm", self.NOW) == local(2026, 10, 10, 15, 0)

    def test_after_midnight_a_bare_afternoon_time_is_yesterday(self):
        now = local(2026, 10, 11, 1, 0)
        assert parse_user_clock("3:10", now) == local(2026, 10, 10, 15, 10)

    def test_late_night_time_said_just_after_midnight(self):
        now = local(2026, 10, 11, 0, 20)
        assert parse_user_clock("11:50", now) == local(2026, 10, 10, 23, 50)

    def test_small_future_slack_allowed(self):
        # "around 3:45" said at 3:40 is still today, not 12 hours ago
        assert parse_user_clock("3:45", self.NOW) == local(2026, 10, 10, 15, 45)

    def test_noon_and_midnight_readings(self):
        assert parse_user_clock("12:05", local(2026, 10, 10, 12, 30)) == local(2026, 10, 10, 12, 5)
        assert parse_user_clock("12:05 am", local(2026, 10, 10, 12, 30)) == local(2026, 10, 10, 0, 5)

    def test_result_is_utc(self):
        assert parse_user_clock("3:10", self.NOW).tzinfo == UTC
        assert parse_user_clock("3:10", self.NOW) == datetime(2026, 10, 10, 19, 10, tzinfo=UTC)

    @pytest.mark.parametrize("bad", ["25:00", "3:61", "abc", "13pm", "", "3:1"])
    def test_rejects_nonsense(self, bad):
        with pytest.raises(ValueError):
            parse_user_clock(bad, self.NOW)


class TestGtfsTimes:
    def test_leading_space_and_after_midnight_hours(self):
        assert gtfs_time_to_seconds(" 4:40:00") == 4 * 3600 + 40 * 60
        assert gtfs_time_to_seconds("25:10:00") == 25 * 3600 + 10 * 60
        assert gtfs_time_to_seconds("26:11:00") == 26 * 3600 + 11 * 60

    def test_service_day_starts_at_noon_minus_12h(self):
        assert service_day_start(date(2026, 10, 10)) == local(2026, 10, 10, 0, 0)
        # US DST starts Mar 8 2026: noon EDT minus 12h is 11 PM EST the night before.
        assert service_day_start(date(2026, 3, 8)) == datetime(2026, 3, 8, 4, 0, tzinfo=UTC)

    def test_half_past_midnight_belongs_to_two_service_days(self):
        t = local(2026, 10, 10, 0, 30)
        assert service_day_candidates(t) == [(date(2026, 10, 10), 1800), (date(2026, 10, 9), 24 * 3600 + 1800)]

    def test_afternoon_belongs_to_one_service_day(self):
        assert service_day_candidates(local(2026, 10, 10, 15, 0)) == [(date(2026, 10, 10), 15 * 3600)]


def test_late_night_after_midnight_window_is_bounded():
    # 4:00 AM Saturday is 28:00 on Friday's service day: still a candidate
    assert (date(2026, 10, 9), 28 * 3600) in service_day_candidates(local(2026, 10, 10, 4, 0))
    # 7:00 AM is 31:00 on Friday's service day: past MARTA's last trip, not a candidate
    assert service_day_candidates(local(2026, 10, 10, 7, 0)) == [(date(2026, 10, 10), 7 * 3600)]


def test_fall_back_hour_uses_the_poll_time_to_pick_the_right_reading():
    """Review finding: the second (standard time) 1:30 AM was stored an hour early."""
    text = "11/01/2026 01:30:00 AM"
    first = datetime(2026, 11, 1, 5, 30, tzinfo=UTC)    # 1:30 EDT
    second = datetime(2026, 11, 1, 6, 30, tzinfo=UTC)   # 1:30 EST
    assert parse_marta_rail_time(text, near=datetime(2026, 11, 1, 5, 30, 20, tzinfo=UTC)) == first
    assert parse_marta_rail_time(text, near=datetime(2026, 11, 1, 6, 30, 20, tzinfo=UTC)) == second
    # a server clock a few seconds ahead must not flip it back
    assert parse_marta_rail_time(text, near=datetime(2026, 11, 1, 6, 29, 55, tzinfo=UTC)) == second
