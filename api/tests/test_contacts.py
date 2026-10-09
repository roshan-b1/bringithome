"""Contacts and hours (ARCHITECTURE.md decision 10, PREP-REPORT section 6): the verified list loads only when every
entry has its sources, the forbidden things are absent, and office hours are right at their edges in New York time,
across a DST change."""

import copy
import json
import re
from datetime import datetime, timezone

import pytest

from backtrack_worker.timeutil import ATL
from backtrack_api import contacts
from backtrack_api.contacts import CONTACTS, CONTACTS_PATH, Contacts, ContactsError
from backtrack_api.copylint import lint

UTC = timezone.utc
RAW = json.loads(CONTACTS_PATH.read_text())


def ny(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=ATL)


def utc(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


def edited(fn) -> dict:
    raw = copy.deepcopy(RAW)
    fn(raw)
    return raw


# ----------------------------------------------------------------------------- the list itself


def test_every_entry_has_source_urls_and_verified_at_2026_10_08():
    for key, c in CONTACTS.entries.items():
        assert c.source_urls and all(u.startswith("https://") for u in c.source_urls), key
        assert c.verified_at == "2026-10-08", key


@pytest.mark.parametrize("field", ["source_urls", "verified_at"])
def test_a_missing_source_or_verified_at_stops_the_load(field):
    raw = edited(lambda r: r["entries"]["lost_found_phone"].pop(field))
    with pytest.raises(ContactsError, match="lost_found_phone"):
        Contacts(raw)


def test_an_empty_or_http_source_list_stops_the_load():
    with pytest.raises(ContactsError):
        Contacts(edited(lambda r: r["entries"]["police_emergency"].update(source_urls=[])))
    with pytest.raises(ContactsError):
        Contacts(edited(lambda r: r["entries"]["police_emergency"].update(source_urls=["http://itsmarta.com"])))


def test_the_shipped_file_is_valid_and_loads_at_import():
    assert contacts.validate(RAW) == []
    assert isinstance(contacts.load(), Contacts)


def test_numbers_match_the_verified_list():
    expect = {"lost_found_phone": ("404-848-3208", "tel:+14048483208"),
              "police_emergency": ("404-848-4911", "tel:+14048484911"),
              "police_non_emergency": ("404-848-4900", "tel:+14048484900"),
              "customer_service": ("404-848-5000", "tel:+14048485000"),
              "route_schedule_info": ("404-848-5000", "tel:+14048485000"),
              "emergency_911": ("911", "tel:911")}
    for key, (value, tel) in expect.items():
        c = contacts.contact(key)
        assert (c.value, c.tel) == (value, tel)
    assert contacts.contact("police_text").sms == "sms:+18773718477"
    assert contacts.contact("lost_found_email").mailto == "mailto:lostandfound@itsmarta.com"
    assert contacts.contact("lost_found_form").url == "https://itsmarta.com/tplecrmcontact-v2.aspx"


def test_never_a_lost_found_address():
    text = CONTACTS_PATH.read_text()
    assert "lost_found_address" not in RAW["entries"]
    assert not any("address" in e for e in RAW["entries"].values())
    assert not re.search(r"\b\d{2,5}\s+\w+\s+(?:Ave|St|Rd|Blvd|Dr)\b", text)       # no street address anywhere
    assert "Ashby" not in text and "1484" not in text and "30307" not in text


def test_an_address_slipped_into_any_entry_stops_the_load():
    raw = edited(lambda r: r["entries"]["lost_found_hours"].update(note="Office at 2424 Piedmont Rd NE, Atlanta"))
    with pytest.raises(ContactsError, match="street address"):
        Contacts(raw)
    raw = edited(lambda r: r["entries"].update(lost_found_address={**r["entries"]["lost_found_hours"]}))
    with pytest.raises(ContactsError, match="never_show"):
        Contacts(raw)


def test_never_see_and_say():
    assert "see_and_say" not in RAW["entries"]
    for key, e in RAW["entries"].items():
        for field in ("value", "display", "tel", "sms", "mailto", "url", "email"):
            assert not re.search(r"see\s*(&|and)?\s*say", str(e.get(field) or ""), re.I), key
    raw = edited(lambda r: r["entries"]["marta_app_ios"].update(note="x", display="Report with See & Say"))
    with pytest.raises(ContactsError, match="See & Say"):
        Contacts(raw)


def test_no_conflicting_detective_or_mobility_numbers():
    text = CONTACTS_PATH.read_text()
    for number in ("404-848-5656", "404-848-3924", "404-848-5826"):
        assert number not in text
    assert {"detective_numbers", "mobility_numbers"} <= {r["key"] for r in RAW["rules"]["never_show"]}


def test_police_property_stays_hidden_until_fridays_call_confirms_it():
    assert contacts.police_property_confirmed() is False
    with pytest.raises(KeyError):
        contacts.contact("police_property_unit")
    confirmed = Contacts(edited(lambda r: r["entries"]["police_property_unit"].update(status="verified",
                                                                                     show_in_app=True)))
    assert confirmed.police_property_confirmed() is True
    assert confirmed.usable("police_property_unit").value == "404-848-3617"


def test_the_form_reason_reads_confirm_on_fridays_call():
    assert contacts.form_reason() == "confirm on Friday's call"
    labels = contacts.form_labels()
    assert labels["bus_number"] == "Bus Number" and labels["rail_car_number"] == "Rail Car Number"
    assert contacts.contact("lost_found_form").extra["prefill"] is False


def test_a_broken_mailto_or_tel_stops_the_load():
    with pytest.raises(ContactsError, match="mailto"):
        Contacts(edited(lambda r: r["entries"]["lost_found_email"].update(mailto="mailto://lostandfound@itsmarta.com")))
    with pytest.raises(ContactsError, match="tel"):
        Contacts(edited(lambda r: r["entries"]["lost_found_phone"].update(tel="tel:4048483208")))


def test_rider_facing_contact_text_passes_the_copy_lint():
    for key, c in CONTACTS.entries.items():
        for text in (c.value, c.display):
            if text and c.kind in ("fact", "hours", "calendar"):
                assert lint(text) == [], (key, text)


def test_actions_are_tel_sms_mailto_or_link():
    assert contacts.action("lost_found_phone").model_dump() == {"type": "tel", "label_id": "lost_found_phone",
                                                                "value": "tel:+14048483208"}
    assert contacts.action("police_text").value == "sms:+18773718477"
    assert contacts.action("lost_found_email").type == "mailto"
    assert contacts.action("lost_found_form", "report_open_form").type == "link"
    facts, sources = CONTACTS.card_facts("police_non_emergency", "emergency_911")
    assert facts == {"police_non_emergency": "404-848-4900", "emergency_911": "911"}
    assert set(sources.values()) == {"contacts"}


# ----------------------------------------------------------------------------- Lost & Found hours


MON_830 = utc(2026, 10, 12, 12, 30)        # Mon Oct 12, 8:30 AM EDT


@pytest.mark.parametrize("now,open_now,next_open,usually", [
    (ny(2026, 10, 9, 16, 59), True, utc(2026, 10, 12, 12, 30), True),     # Fri 4:59 PM: open
    (ny(2026, 10, 9, 17, 0), False, MON_830, True),                       # Fri 5:00 PM: closed for the weekend
    (ny(2026, 10, 9, 20, 30), False, MON_830, True),                      # kickoff
    (ny(2026, 10, 10, 12, 0), False, MON_830, True),                      # Sat noon
    (ny(2026, 10, 11, 23, 0), False, MON_830, True),                      # Sun 11 PM
    (ny(2026, 10, 12, 8, 29), False, MON_830, True),                      # Mon 8:29 AM
    (ny(2026, 10, 12, 8, 30), True, utc(2026, 10, 13, 12, 30), False),    # Mon 8:30 AM: open; next is Tue
])
def test_lost_found_hours_at_the_edges(now, open_now, next_open, usually):
    s = contacts.lost_found_status(now)
    assert (s.open_now, s.next_open) == (open_now, next_open)
    assert s.next_open.tzinfo == UTC
    if not open_now:
        assert s.next_open_usually is usually


def test_lost_found_open_is_a_pair():
    assert contacts.lost_found_open(ny(2026, 10, 9, 20, 30)) == (False, MON_830)


def test_columbus_day_opening_is_flagged_usually_and_friday_is_not():
    fri = contacts.lost_found_status(ny(2026, 10, 9, 10))
    assert fri.open_now and not fri.open_now_usually and fri.closes_at == utc(2026, 10, 9, 21)
    mon = contacts.lost_found_status(ny(2026, 10, 12, 10))
    assert mon.open_now and mon.open_now_usually            # Columbus Day: MARTA lists no closure, unverified


def test_fall_back_weekend_opens_monday_at_830_eastern_standard_time():
    s = contacts.lost_found_status(ny(2026, 10, 30, 18))     # Fri Oct 30, EDT
    assert s.next_open == utc(2026, 11, 2, 13, 30)           # Mon Nov 2, 8:30 AM EST
    assert s.next_open.astimezone(ATL).hour == 8
    ambiguous_hour = datetime(2026, 11, 1, 5, 30, tzinfo=UTC)   # Sun Nov 1, 1:30 AM EDT (the repeated hour)
    assert contacts.lost_found_open(ambiguous_hour) == (False, utc(2026, 11, 2, 13, 30))
    assert contacts.lost_found_status(utc(2026, 11, 2, 13, 29)).open_now is False
    assert contacts.lost_found_status(utc(2026, 11, 2, 13, 30)).open_now is True


def test_spring_forward_weekend_opens_monday_at_830_eastern_daylight_time():
    s = contacts.lost_found_status(ny(2027, 3, 12, 17, 30))  # Fri Mar 12 2027, EST
    assert s.next_open == utc(2027, 3, 15, 12, 30)           # Mon Mar 15, 8:30 AM EDT


def test_a_naive_now_is_refused():
    with pytest.raises(ValueError):
        contacts.lost_found_status(datetime(2026, 10, 9, 12))


def test_a_listed_holiday_closes_the_office_and_moves_the_info_line_to_sunday_hours():
    def close_monday(r):
        r["entries"]["holidays"]["closed_dates"] = ["2026-10-12"]
    c = Contacts(edited(close_monday))
    s = c.lost_found_status(ny(2026, 10, 9, 20))
    assert s.next_open == utc(2026, 10, 13, 12, 30)           # Tue
    info = {line.key: line for line in c.line_statuses(ny(2026, 10, 12, 7, 30))}["route_schedule_info"]
    assert not info.open_now and info.next_open == utc(2026, 10, 12, 12)   # holiday: 8 AM like a Sunday


# ----------------------------------------------------------------------------- the call card's lines


POLICE = ["police_non_emergency", "police_emergency", "police_text", "emergency_911"]


@pytest.mark.parametrize("now,keys", [
    (ny(2026, 10, 9, 16, 59), ["lost_found_phone", "customer_service", "route_schedule_info", *POLICE]),
    (ny(2026, 10, 9, 17, 30), ["route_schedule_info", *POLICE]),     # weekday info line runs to 7 PM
    (ny(2026, 10, 9, 20, 30), POLICE),                               # kickoff: police lines only
    (ny(2026, 10, 10, 7, 59), POLICE),
    (ny(2026, 10, 10, 10, 0), ["route_schedule_info", *POLICE]),     # Sat: the 404-848-5000 line, 8 AM to 5 PM
    (ny(2026, 10, 11, 17, 0), POLICE),                               # Sun 5 PM: closed again
])
def test_which_lines_answer_right_now(now, keys):
    assert [line.key for line in contacts.lines_open(now)] == keys


def test_closed_lines_say_when_they_open_next():
    by_key = {line.key: line for line in contacts.line_statuses(ny(2026, 10, 9, 20, 30))}
    assert by_key["route_schedule_info"].next_open == utc(2026, 10, 10, 12)        # Sat 8 AM EDT
    assert by_key["customer_service"].next_open == utc(2026, 10, 12, 12)           # Mon 8 AM EDT
    assert by_key["lost_found_phone"].usually is True
    assert by_key["emergency_911"].open_now and by_key["emergency_911"].next_open is None
    assert by_key["police_text"].action.value == "sms:+18773718477"
    assert "police_property_unit" not in by_key and "tty" not in by_key
