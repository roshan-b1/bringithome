"""Verified MARTA contacts and office hours, from data/contacts.json (ARCHITECTURE.md decision 10, section 8).

Why a data file with a strict loader: phones, emails, links and hours are the one kind of fact the plan can't get
from the recorders or the schedule, and a wrong one sends a rider somewhere useless at the worst moment. So they
come only from PREP-REPORT.md section 6, each with its source_urls and verified_at, and this module refuses to
import if any entry lacks them. The file also carries the rules that go with the list: never a Lost & Found
address, never See & Say, no conflicting detective or mobility numbers, the police property unit hidden until
Friday's call confirms it, and the form Reason shown as "confirm on Friday's call".

Hours are computed in New York wall-clock time and returned as UTC instants (DST-correct: an 8:30 AM opening is
12:30 UTC in October and 13:30 UTC after the November change). A day counts as verified only inside the range the
holiday page was checked for (Oct 9 to 12, 2026) and not on an unverified date (Columbus Day, Mon Oct 12); an
opening on any other day is flagged `usually`, which is why the copy says "usually opens the next weekday at
8:30 AM".
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Literal, Mapping

from backtrack_worker.timeutil import ATL, UTC

from .copylint import lint
from .models import CardAction

CONTACTS_PATH = Path(__file__).resolve().parent / "data" / "contacts.json"
FACT_SOURCE = "contacts"                 # the fact_sources value for anything that comes from here
STATUSES = ("verified", "partly", "confirm_friday", "do_not_use")
KINDS = ("phone", "text", "email", "url", "hours", "fact", "calendar")
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
ALWAYS = "always"
LOOKAHEAD_DAYS = 15                      # far enough to cross any long weekend plus a holiday

Status = Literal["verified", "partly", "confirm_friday", "do_not_use"]

_PHONE = re.compile(r"^(?:\d{3}-\d{3}-\d{4}|1-\d{3}-\d{3}-\d{4}|911)$")
_EMAIL = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}$")
_HHMM = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
# A street address in any string: "1484 DeKalb Ave NE", "55 Gilmer St SE", or a ZIP after GA / Atlanta.
_ADDRESS = re.compile(
    r"\b\d{1,5}\s+(?:[A-Z][\w.'-]*\s+){1,4}(?:Ave|Avenue|St|Street|Rd|Road|Blvd|Boulevard|Dr|Drive|Way|Pkwy|"
    r"Parkway|Pl|Place|Ln|Lane|Ct|Court|Hwy|Highway|Cir|Circle)\b|\b(?:GA|Georgia|Atlanta),?\s+(?:GA\s+)?\d{5}\b")
_SEE_SAY = re.compile(r"see\s*(?:&|and|\+|-|n)?\s*say", re.IGNORECASE)
# Fields a rider can see or tap. Source URLs and notes are evidence for us, not links for riders.
_RIDER_FIELDS = ("value", "display", "tel", "sms", "mailto", "url", "email")


class ContactsError(ValueError):
    """contacts.json broke a rule. Raised at import, so a bad file never serves a single plan."""


@dataclass(frozen=True)
class Contact:
    key: str
    kind: str
    value: str | None
    status: Status
    verified_at: str
    source_urls: tuple[str, ...]
    tel: str | None = None
    sms: str | None = None
    mailto: str | None = None
    url: str | None = None
    email: str | None = None
    display: str | None = None
    hours: str | None = None             # an hours entry's key, "always", or None (not published)
    show_in_app: bool = True
    note: str | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)   # form labels, schedule, hold time ...

    @property
    def usable(self) -> bool:
        """May the app show it? Not when MARTA's pages conflict, nor while it waits on Friday's call."""
        return self.status != "do_not_use" and self.show_in_app


@dataclass(frozen=True)
class Hours:
    key: str
    schedule: tuple[tuple[tuple[time, time], ...], ...]      # Monday first, local wall clock
    on_holidays: str                                         # "closed" | "same" | a day whose hours apply


@dataclass(frozen=True)
class Calendar:
    checked_from: date
    checked_to: date
    closed: frozenset[date]
    unverified: frozenset[date]

    def verified(self, d: date) -> bool:
        """Do we know this day runs normally? Only inside the checked range and not on an unverified date."""
        return self.checked_from <= d <= self.checked_to and d not in self.unverified


@dataclass(frozen=True)
class OfficeStatus:
    open_now: bool
    next_open: datetime | None           # the next opening strictly after now (UTC); None for always-open lines
    closes_at: datetime | None           # when the current opening ends (UTC), if open now
    open_now_usually: bool               # open now, but on a day whose opening is unverified
    next_open_usually: bool              # the next opening falls on an unverified day: say "usually"


@dataclass(frozen=True)
class LineStatus:
    key: str
    kind: str                            # phone | text
    value: str
    action: CardAction
    open_now: bool
    closes_at: datetime | None
    next_open: datetime | None
    usually: bool


# ----------------------------------------------------------------------------- loading and validation


def _e164(display: str) -> str:
    digits = re.sub(r"\D", "", display)
    if digits == "911":
        return digits
    return "+" + (digits if len(digits) == 11 else "1" + digits)


def _check_entry(key: str, e: Mapping[str, Any], never: set[str], hours_keys: set[str]) -> list[str]:
    bad: list[str] = []
    say = lambda msg: bad.append(f"{key}: {msg}")   # noqa: E731
    if key in never:
        say("is on the never_show list")
    if e.get("kind") not in KINDS:
        say(f"kind {e.get('kind')!r} is not one of {KINDS}")
    if e.get("status") not in STATUSES:
        say(f"status {e.get('status')!r} is not one of {STATUSES}")
    verified_at = e.get("verified_at")
    try:
        date.fromisoformat(verified_at)
    except (TypeError, ValueError):
        say("missing or malformed verified_at")
    urls = e.get("source_urls")
    if not urls or not isinstance(urls, list) or not all(isinstance(u, str) and u.startswith("https://")
                                                          for u in urls):
        say("needs at least one https source_url")
    if e.get("status") == "do_not_use" and e.get("value") is not None:
        say("a do_not_use entry may not carry a value")
    kind, value = e.get("kind"), e.get("value")
    if kind == "phone":
        if not isinstance(value, str) or not _PHONE.match(value):
            say(f"phone {value!r} is not written like 404-848-3208")
        elif e.get("tel") != f"tel:{_e164(value)}":
            say(f"tel must be tel:{_e164(value)}")
    if kind == "text":
        if not isinstance(value, str) or not _PHONE.match(value):
            say(f"text number {value!r} is not written like 1-877-371-8477")
        elif e.get("sms") != f"sms:{_e164(value)}":
            say(f"sms must be sms:{_e164(value)}")
    if kind == "email" and (not isinstance(value, str) or not _EMAIL.match(value)):
        say(f"email {value!r} is malformed")
    address = value if kind == "email" else e.get("email")
    if address is not None and e.get("mailto") != f"mailto:{address}":
        say(f"mailto must be mailto:{address} (never mailto://)")
    if kind == "url" and (e.get("url") != value or not str(value).startswith("https://")):
        say("a url entry needs url == value, over https")
    if kind in ("phone", "text", "email", "url") and e.get("hours") is not None \
            and e.get("hours") != ALWAYS and e.get("hours") not in hours_keys:
        say(f"hours {e.get('hours')!r} is not an hours entry")
    if kind == "hours":
        sched = e.get("schedule") or {}
        if set(sched) != set(DAYS):
            say("schedule must list all seven days")
        for day, spans in sched.items():
            for span in spans:
                if len(span) != 2 or not all(isinstance(t, str) and _HHMM.match(t) for t in span) or span[0] >= span[1]:
                    say(f"schedule {day} has a bad span {span!r}")
        if e.get("on_holidays") not in ("closed", "same", *DAYS):
            say("on_holidays must be closed, same or a day")
    if kind == "calendar":
        try:
            lo, hi = date.fromisoformat(e["checked_from"]), date.fromisoformat(e["checked_to"])
            [date.fromisoformat(d) for d in e.get("closed_dates", [])]
            [date.fromisoformat(d["date"]) for d in e.get("unverified_dates", [])]
            if lo > hi:
                say("checked_from is after checked_to")
        except (KeyError, TypeError, ValueError):
            say("calendar dates are malformed")
    for name, text in _strings(e):
        if _ADDRESS.search(text):
            say(f"{name} looks like a street address; addresses are never shown")
        if name in _RIDER_FIELDS and _SEE_SAY.search(text):
            say(f"{name} mentions See & Say, which is retired")
        if name in ("value", "display") and kind in ("fact", "hours", "calendar"):
            for p in lint(text):
                say(f"{name} fails the copy lint ({p})")
    return bad


def _strings(e: Mapping[str, Any], prefix: str = ""):
    """Every string in an entry except its source_urls, with its top-level field name."""
    for k, v in e.items():
        if k == "source_urls":
            continue
        name = prefix or k
        if isinstance(v, str):
            yield name, v
        elif isinstance(v, Mapping):
            yield from _strings(v, name)
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, str):
                    yield name, item
                elif isinstance(item, Mapping):
                    yield from _strings(item, name)


def validate(raw: Mapping[str, Any]) -> list[str]:
    """Every rule the file breaks; empty when it is fit to serve."""
    bad: list[str] = []
    if raw.get("schema") != 1:
        bad.append("schema must be 1")
    entries = raw.get("entries")
    rules = raw.get("rules")
    if not isinstance(entries, Mapping) or not entries:
        return bad + ["no entries"]
    if not isinstance(rules, Mapping):
        return bad + ["no rules"]
    never = {r["key"] for r in rules.get("never_show", [])}
    for required in ("lost_found_address", "see_and_say"):
        if required not in never:
            bad.append(f"rules.never_show must name {required}")
    hours_keys = {k for k, e in entries.items() if isinstance(e, Mapping) and e.get("kind") == "hours"}
    for key, e in entries.items():
        if not isinstance(e, Mapping):
            bad.append(f"{key}: not an object")
            continue
        bad += _check_entry(key, e, never, hours_keys)
    for key in rules.get("call_card_lines", []):
        if entries.get(key, {}).get("kind") not in ("phone", "text"):
            bad.append(f"rules.call_card_lines: {key} is not a phone or text entry")
    if entries.get("holidays", {}).get("kind") != "calendar":
        bad.append("needs a holidays calendar entry")
    for key in ("lost_found_hours", "lost_found_phone", "lost_found_form", "lost_found_form_reason"):
        if key not in entries:
            bad.append(f"missing {key}")
    return bad


_FIELDS = ("kind", "value", "status", "verified_at", "source_urls", "tel", "sms", "mailto", "url", "email",
           "display", "hours", "show_in_app", "note")


class Contacts:
    """The loaded, validated list. Built once at import as CONTACTS; tests can build their own."""

    def __init__(self, raw: Mapping[str, Any]):
        problems = validate(raw)
        if problems:
            raise ContactsError("contacts.json is not fit to serve:\n  " + "\n  ".join(problems))
        self.raw = raw
        self.rules: Mapping[str, Any] = raw["rules"]
        self.entries: dict[str, Contact] = {}
        for key, e in raw["entries"].items():
            self.entries[key] = Contact(
                key=key, kind=e["kind"], value=e.get("value"), status=e["status"], verified_at=e["verified_at"],
                source_urls=tuple(e["source_urls"]), tel=e.get("tel"), sms=e.get("sms"), mailto=e.get("mailto"),
                url=e.get("url"), email=e.get("email"), display=e.get("display"), hours=e.get("hours"),
                show_in_app=e.get("show_in_app", True), note=e.get("note"),
                extra={k: v for k, v in e.items() if k not in _FIELDS})
        self._hours = {k: _hours_of(k, raw["entries"][k]) for k, c in self.entries.items() if c.kind == "hours"}
        h = raw["entries"]["holidays"]
        self.calendar = Calendar(
            checked_from=date.fromisoformat(h["checked_from"]), checked_to=date.fromisoformat(h["checked_to"]),
            closed=frozenset(date.fromisoformat(d) for d in h.get("closed_dates", [])),
            unverified=frozenset(date.fromisoformat(d["date"]) for d in h.get("unverified_dates", [])))

    # accessors ------------------------------------------------------------------------------

    def get(self, key: str) -> Contact:
        """Any entry, usable or not. KeyError for a key that isn't on the verified list."""
        return self.entries[key]

    def usable(self, key: str) -> Contact:
        """An entry the app may show. KeyError if it is unknown, conflicting, or waiting on Friday's call."""
        c = self.entries[key]
        if not c.usable:
            raise KeyError(f"{key} is not shown in the app (status {c.status})")
        return c

    def hours(self, key: str) -> Hours:
        return self._hours[key]

    def form_reason(self) -> str:
        """What the report card says about the form's Reason dropdown."""
        c = self.entries["lost_found_form_reason"]
        return c.value if c.status == "verified" and c.value else (c.display or "")

    def form_labels(self) -> dict[str, str]:
        """report field -> the form's own label ("bus_number" -> "Bus Number")."""
        return dict(self.entries["lost_found_form"].extra["field_labels"])

    def police_property_confirmed(self) -> bool:
        """Decision 10 card 8: the police_property card appears only once Friday's call confirmed the unit."""
        c = self.entries.get("police_property_unit")
        return bool(c and c.status == "verified" and c.show_in_app)

    def action(self, key: str, label_id: str | None = None) -> CardAction:
        """The tap for a contact: tel for a phone, an sms: link for the text line, mailto, or a link."""
        c = self.usable(key)
        if c.kind == "phone":
            kind, value = "tel", c.tel
        elif c.kind == "text":
            kind, value = "link", c.sms
        elif c.kind == "email":
            kind, value = "mailto", c.mailto
        elif c.kind == "url":
            kind, value = "link", c.url
        else:
            raise KeyError(f"{key} is a {c.kind}, not something to tap")
        return CardAction(type=kind, label_id=label_id or key, value=value)

    def card_facts(self, *keys: str) -> tuple[dict[str, str], dict[str, str]]:
        """facts and fact_sources for a card that quotes these entries' values."""
        facts = {k: self.usable(k).value for k in keys}
        return facts, {k: FACT_SOURCE for k in facts}

    # hours ------------------------------------------------------------------------------------

    def _spans_on(self, h: Hours, d: date) -> list[tuple[datetime, datetime, date]]:
        day = d.weekday()
        if d in self.calendar.closed:
            if h.on_holidays == "closed":
                return []
            if h.on_holidays != "same":
                day = DAYS.index(h.on_holidays)
        return [(_at(d, a), _at(d, b), d) for a, b in h.schedule[day]]

    def status(self, hours_key: str, now: datetime) -> OfficeStatus:
        """Open now?, when it closes, and the next opening strictly after `now` (all UTC)."""
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        if hours_key == ALWAYS:
            return OfficeStatus(True, None, None, False, False)
        h = self._hours[hours_key]
        today = now.astimezone(ATL).date()
        spans = [s for i in range(-1, LOOKAHEAD_DAYS) for s in self._spans_on(h, today + timedelta(days=i))]
        current = next(((a, b, d) for a, b, d in spans if a <= now < b), None)
        upcoming = min(((a, d) for a, b, d in spans if a > now), default=None)
        return OfficeStatus(
            open_now=current is not None,
            next_open=upcoming[0].astimezone(UTC) if upcoming else None,
            closes_at=current[1].astimezone(UTC) if current else None,
            open_now_usually=bool(current) and not self.calendar.verified(current[2]),
            next_open_usually=bool(upcoming) and not self.calendar.verified(upcoming[1]))

    def lost_found_status(self, now: datetime) -> OfficeStatus:
        return self.status(self.entries["lost_found_phone"].hours, now)

    def line_statuses(self, now: datetime) -> list[LineStatus]:
        """Every call-card line, in the card's order, with whether it answers right now."""
        out = []
        for key in self.rules.get("call_card_lines", []):
            c = self.entries[key]
            if not c.usable or c.hours is None:
                continue
            s = self.status(c.hours, now)
            usually = s.open_now_usually if s.open_now else s.next_open_usually
            out.append(LineStatus(key=key, kind=c.kind, value=c.value, action=self.action(key), open_now=s.open_now,
                                  closes_at=s.closes_at, next_open=s.next_open, usually=usually))
        return out


def _at(d: date, t: time) -> datetime:
    """New York wall clock on day d -> UTC. Office hours never sit in the 2 AM DST gap or repeat."""
    return datetime.combine(d, t, tzinfo=ATL).astimezone(UTC)


def _hours_of(key: str, e: Mapping[str, Any]) -> Hours:
    sched = tuple(tuple((time.fromisoformat(a), time.fromisoformat(b)) for a, b in e["schedule"][day]) for day in DAYS)
    return Hours(key=key, schedule=sched, on_holidays=e["on_holidays"])


def load(path: Path = CONTACTS_PATH) -> Contacts:
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise ContactsError(f"cannot read {path.name}: {type(e).__name__}: {e}") from e
    return Contacts(raw)


CONTACTS = load()   # at import: a bad or missing file stops the API from starting, loudly


# ----------------------------------------------------------------------------- module-level shortcuts


def contact(key: str) -> Contact:
    """A contact the app may show (KeyError otherwise)."""
    return CONTACTS.usable(key)


def lost_found_open(now: datetime) -> tuple[bool, datetime]:
    """(open now, next opening strictly after now in UTC) for the Lost & Found phone: Mon to Fri, 8:30 AM to 5 PM
    New York time. For the `usually` flag and the closing time use lost_found_status()."""
    s = CONTACTS.lost_found_status(now)
    return s.open_now, s.next_open


def lost_found_status(now: datetime) -> OfficeStatus:
    return CONTACTS.lost_found_status(now)


def lines_open(now: datetime) -> list[LineStatus]:
    """The call card's lines that answer right now: Lost & Found and customer service on weekday office hours,
    the 404-848-5000 info line (weekdays 7 AM to 7 PM, weekends 8 AM to 5 PM), the police lines and 911 always."""
    return [s for s in CONTACTS.line_statuses(now) if s.open_now]


def line_statuses(now: datetime) -> list[LineStatus]:
    return CONTACTS.line_statuses(now)


def form_reason() -> str:
    return CONTACTS.form_reason()


def form_labels() -> dict[str, str]:
    return CONTACTS.form_labels()


def police_property_confirmed() -> bool:
    return CONTACTS.police_property_confirmed()


def action(key: str, label_id: str | None = None) -> CardAction:
    return CONTACTS.action(key, label_id)
