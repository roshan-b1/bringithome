"""MARTA's lost-item form, filled from a saved session (ARCHITECTURE.md decision 10 card 5, section 8 item 1).

Why it is built from the session alone: the report is what a rider pastes into MARTA's official form, so every
value must be a fact we can point to. The session holds the rider's taps (intake) and the match the rider saw
(result), and nothing else is needed: vehicle numbers, routes, lines and pass times come from the recorded
positions ("db"), stop and station names and headsigns from the schedule ("static_gtfs"), and the rider's own
time and direction answers from the server-side time rules ("rules"). Each filled field carries that source.

The rules that keep the form honest:
  - matched: the chosen vehicle's numbers. A bus fills route_number and bus_number (its painted fleet number,
    the feed `label`); a train fills rail_line, rail_station and train_run.
  - ambiguous with both vehicles chosen: both, "1405 or 1412". We never pick one train or bus on a tie.
  - uncertain: bus_number (and train_run) stay blank, and the top candidates go in candidates_note ("possibly one
    of 1405, 1412 or 1433"). A low-confidence result never puts one wrong number on MARTA's form.
  - not_reporting / no_match: no vehicle at all; the route, line and direction the rider tapped or the schedule
    agrees on.
  - A TRAIN_ID is a run number, never a railcar number, so it fills train_run ("MARTA train run 405, from MARTA's
    public feeds") and never rail_car_number, which only the rider's own "Saw a car number inside?" answer fills,
    on the phone.
Times are formatted for a rider in New York ("Fri Oct 9, about 1:18 PM"); comparisons stay in UTC.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Mapping, Sequence

from backtrack_worker.timeutil import ATL

from . import contacts, wording
from .models import CardAction, Report, ReportFormFields
from .resolver import _pretty_headsign

FactSource = Literal["db", "static_gtfs", "contacts", "rules"]
CardSource = Literal["live", "schedule", "static"]

MAX_KEYS = 2                     # decision 9: 2 on a tie
NOTE_MAX = 3                     # "possibly one of 1405, 1412 or 1433"
UNKNOWN_DIRECTION = "Direction unknown"
LINE_NAMES = {"RED": "Red", "GOLD": "Gold", "BLUE": "Blue", "GREEN": "Green"}
COMPASS = {"N": "Northbound", "S": "Southbound", "E": "Eastbound", "W": "Westbound"}
# Labels the form doesn't define but the reviewed plan_body_3 copy uses (PREP-REPORT section 10).
LOCATION_LABEL, TIME_LABEL, NOTE_LABEL = "Location", "Time", "Note"
FEEDS = "from MARTA's public feeds"


class ReportError(ValueError):
    """The request names vehicles the session never matched (the API answers 400)."""


@dataclass(frozen=True)
class ReportFacts:
    form_fields: ReportFormFields
    candidates_note: str | None
    fact_sources: dict[str, FactSource]      # one per filled form field, plus candidates_note when set
    tokens: dict[str, str]                   # wording token -> value (VEHICLE, ROUTE, ...); never ITEM
    mode: str                                # template family: bus | rail | streetcar | unknown
    role: str                                # alight | board: "I got off at" or "I got on at"
    status: str                              # the match status the report was built for
    vehicle_keys: tuple[str, ...]            # vehicles named on the form (none at uncertain)
    source: CardSource                       # live: named vehicles; schedule: from the timetable; static: taps only
    form_url: str = field(default_factory=lambda: contacts.contact("lost_found_form").url)

    def card_facts(self) -> tuple[dict[str, str], dict[str, FactSource]]:
        """facts and fact_sources for the report card: exactly the filled fields and the note."""
        facts = {k: v for k, v in self.form_fields.model_dump().items() if v}
        if self.candidates_note:
            facts["candidates_note"] = self.candidates_note
        return facts, {k: self.fact_sources[k] for k in facts}

    def copy_text(self) -> str:
        """What the Copy action puts on the clipboard: the form's own labels with our values, one per line."""
        labels = contacts.form_labels()
        f = self.form_fields
        rows = [(labels["rail_line"], f.rail_line), (labels["rail_station"], f.rail_station),
                (labels["route_number"], f.route_number), (labels["bus_number"], f.bus_number),
                (labels["direction"], f.direction), (LOCATION_LABEL, f.location), (TIME_LABEL, f.time)]
        lines = [f"{label}: {value}" for label, value in rows if value]
        if f.train_run:
            lines.append(f.train_run)
        if self.candidates_note:
            lines.append(f"{NOTE_LABEL}: {self.candidates_note}")
        return "\n".join(lines)

    def actions(self) -> list[CardAction]:
        """The report card's taps: open MARTA's form, and copy the details to paste into it."""
        return [CardAction(type="link", label_id="report_open_form", value=self.form_url),
                CardAction(type="copy", label_id="report_copy", value=self.copy_text())]


# ----------------------------------------------------------------------------- time, as a rider reads it


def _parse(t: Any) -> datetime:
    if isinstance(t, datetime):
        dt = t
    else:
        dt = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("session times must carry an offset")
    return dt


def _minute(t: datetime) -> datetime:
    """New York wall clock to the minute, cut like a phone clock: at 1:18:52 the rider's screen said 1:18."""
    return t.astimezone(ATL).replace(second=0, microsecond=0)


def rider_clock(t: datetime) -> str:
    """"1:18 PM", "12:05 AM": no leading zero, 12-hour, New York time."""
    m = _minute(t)
    return f"{m.hour % 12 or 12}:{m.minute:02d} {'AM' if m.hour < 12 else 'PM'}"


def rider_day(t: datetime) -> str:
    """"Fri Oct 9"."""
    m = _minute(t)
    return f"{m:%a %b} {m.day}"


def rider_date(t: datetime) -> str:
    """"Friday, October 9", for prose."""
    m = _minute(t)
    return f"{m:%A, %B} {m.day}"


def rider_time(times: Sequence[datetime]) -> str:
    """The form's Time: "Fri Oct 9, about 1:18 PM", or "Fri Oct 9, about 1:16 PM or 1:18 PM" for a tie."""
    by_day: dict[str, list[str]] = {}
    for t in sorted(times):
        clocks = by_day.setdefault(rider_day(t), [])
        if rider_clock(t) not in clocks:
            clocks.append(rider_clock(t))
    return " or ".join(f"{day}, about {join_or(clocks)}" for day, clocks in by_day.items())


def join_or(items: Sequence[str]) -> str:
    """"a", "a or b", "a, b or c" (the reviewed copy has no serial comma)."""
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} or {items[-1]}"


def _unique(values) -> list[str]:
    out: list[str] = []
    for v in values:
        if v and v not in out:
            out.append(v)
    return out


def _line_name(line: str | None) -> str | None:
    return LINE_NAMES.get(line or "", line.title() if line else None)


def _lower_first(text: str) -> str:
    """"Toward North Ave Station" -> "toward North Ave Station", for the middle of a sentence."""
    return text[:1].lower() + text[1:] if text[:1].isupper() and not text[1:2].isupper() else text


# ----------------------------------------------------------------------------- the report


def _primary_anchor(intake: Mapping[str, Any]) -> Mapping[str, Any]:
    """The anchor the match is told around: the stop where the rider got off, else where they got on."""
    anchors = intake.get("anchors") or []
    if not anchors:
        raise ReportError("the session has no anchor")
    return sorted(anchors, key=lambda a: a.get("role") != "alight")[0]


def _evidence_times(cands: Sequence[Mapping[str, Any]], role: str) -> list[datetime]:
    times = []
    for c in cands:
        ev = c.get("evidence") or []
        pick = next((e for e in ev if e.get("role") == role), ev[0] if ev else None)
        if pick and pick.get("at"):
            times.append(_parse(pick["at"]))
    return times


def _note(cands: Sequence[Mapping[str, Any]]) -> str | None:
    """"possibly one of 1405, 1412 or 1433"; trains by run number, never as car numbers."""
    buses = [c for c in cands if c.get("mode") == "bus" and c.get("label")]
    trains = [c for c in cands if c.get("mode") == "rail" and c.get("vehicle_id")]
    if not buses and not trains:
        return None
    lead = "possibly" if len(buses) + len(trains) == 1 else "possibly one of"
    if buses and not trains:
        return f"{lead} {join_or([c['label'] for c in buses])}"
    if trains and not buses:
        noun = "MARTA train run" if len(trains) == 1 else "MARTA train runs"
        return f"{lead} {noun} {join_or([c['vehicle_id'] for c in trains])}, {FEEDS}"
    items = [f"bus {c['label']}" for c in buses] + [f"MARTA train run {c['vehicle_id']}" for c in trains]
    return f"{lead} {join_or(items)}, {FEEDS}"


def build_report(session: Mapping[str, Any], vehicle_keys: Sequence[str] = ()) -> ReportFacts:
    """The form fields, candidates_note, their fact sources and the wording tokens for a saved session
    ({id, created_at, as_of, record, intake, result}) and the vehicle_keys the plan was asked for (0 to 2)."""
    intake = session["intake"]
    match = session["result"]["match"]
    status = match["status"]
    cands = list(match.get("candidates") or [])
    by_key = {c["vehicle_key"]: c for c in cands}

    keys = list(vehicle_keys)
    if len(keys) > MAX_KEYS or len(set(keys)) != len(keys):
        raise ReportError(f"vehicle_keys takes 0 to {MAX_KEYS} distinct keys")
    unknown = [k for k in keys if k not in by_key]
    if unknown:
        raise ReportError(f"not one of this session's candidates: {', '.join(unknown)}")
    chosen = [by_key[k] for k in keys]

    named = chosen if status in ("matched", "ambiguous") else []
    if status == "uncertain":
        listed = cands[:NOTE_MAX]
    elif status == "ambiguous" and not chosen:
        listed = cands[:2]
    else:
        listed = []
    note = _note(listed)
    pool = named or listed               # whose shared facts (route, line, direction) may fill the form

    anchor = _primary_anchor(intake)
    role = anchor.get("role", "alight")
    modes = _unique(c["mode"] for c in pool)
    mode = modes[0] if len(modes) == 1 else ("unknown" if modes else intake.get("mode", "unknown"))
    if mode not in wording.MODES:
        mode = "unknown"

    fields: dict[str, str] = {}
    sources: dict[str, FactSource] = {}

    def put(name: str, value: str | None, source: FactSource) -> None:
        if value:
            fields[name] = value
            sources[name] = source

    location = match.get("context", {}).get("place_label") or next(
        (e.get("place_label") for c in named for e in c.get("evidence", []) if e.get("place_label")), "")
    put("location", location, "static_gtfs")

    ev_times = _evidence_times(named, role)
    if ev_times:
        put("time", rider_time(ev_times), "db")
        times = ev_times
    else:
        times = [_parse(anchor["at"])]
        put("time", rider_time(times), "rules")

    buses = [c for c in named if c["mode"] == "bus"]
    trains = [c for c in named if c["mode"] == "rail"]
    schedule = list(match.get("schedule_candidates") or [])

    # bus fields
    if buses:
        put("route_number", join_or(_unique(c.get("route") for c in buses)), "db")
        if all(c.get("label") for c in buses):
            put("bus_number", join_or(_unique(c["label"] for c in buses)), "db")
    elif mode in ("bus", "streetcar"):
        routes = _unique(c.get("route") for c in listed)
        sched_routes = _unique(s.get("route") for s in schedule)
        if len(routes) == 1:
            put("route_number", routes[0], "db")
        elif intake.get("route"):
            put("route_number", intake["route"], "static_gtfs")
        elif len(sched_routes) == 1 and not listed:
            put("route_number", sched_routes[0], "static_gtfs")

    # rail fields: run numbers, never car numbers
    if trains:
        put("rail_line", join_or(_unique(f"{_line_name(c.get('line'))} Line" for c in trains if c.get("line"))), "db")
        put("rail_station", location, "static_gtfs")
        put("train_run", f"MARTA train run {join_or(_unique(c['vehicle_id'] for c in trains))}, {FEEDS}", "db")
    elif mode == "rail":
        lines = _unique(c.get("line") for c in listed)
        if len(lines) == 1:
            put("rail_line", f"{_line_name(lines[0])} Line", "db")
        elif intake.get("line"):
            put("rail_line", f"{_line_name(intake['line'])} Line", "static_gtfs")
        put("rail_station", location, "static_gtfs")

    # direction: the vehicle's own, else what the candidates or the timetable agree on, else the rider's tap
    directions = _unique(c.get("direction_label") for c in pool if c.get("direction_label") != UNKNOWN_DIRECTION)
    direction_parts: list[str] = []
    if named and directions:
        direction_parts, direction_source = directions, ("db" if trains else "static_gtfs")
    elif listed and len(directions) == 1:
        direction_parts, direction_source = directions, ("db" if listed[0]["mode"] == "rail" else "static_gtfs")
    elif not listed and not named:
        heads = _unique(s.get("headsign") for s in schedule)
        if mode in ("bus", "streetcar") and len(heads) == 1:
            direction_parts, direction_source = [f"Toward {_pretty_headsign(heads[0])}"], "static_gtfs"
        elif intake.get("direction") in COMPASS:
            direction_parts, direction_source = [COMPASS[intake["direction"]]], "rules"
    if direction_parts:
        put("direction", join_or(direction_parts), direction_source)

    if note:
        sources["candidates_note"] = "db"

    form = ReportFormFields(location=fields.pop("location", ""), time=fields.pop("time"), **fields)
    tokens = _tokens(form, mode, trains, times, direction_parts)
    # live: named or listed vehicles from recorded positions; schedule: a timetable headsign or route filled in
    source: CardSource = "live" if pool else ("schedule" if schedule and status in ("not_reporting", "no_match")
                                              else "static")
    return ReportFacts(form_fields=form, candidates_note=note, fact_sources=sources, tokens=tokens, mode=mode,
                       role=role, status=status, vehicle_keys=tuple(c["vehicle_key"] for c in named), source=source)


def _tokens(form: ReportFormFields, mode: str, trains: Sequence[Mapping[str, Any]], times: Sequence[datetime],
            directions: Sequence[str]) -> dict[str, str]:
    """Wording token values. Only facts the form already holds; a value we lack is simply absent, and the
    template for that case doesn't mention it."""
    first = min(times)
    tokens = {"STOP": form.location, "DATE": rider_date(first),
              "TIME": "about " + join_or(_unique(rider_clock(t) for t in sorted(times)))}
    if directions:
        tokens["DIRECTION"] = join_or([_lower_first(d) for d in directions])
    if mode == "bus":
        if form.bus_number:
            tokens["VEHICLE"] = form.bus_number
        if form.route_number:
            tokens["ROUTE"] = form.route_number
    elif mode == "rail":
        if trains:
            tokens["VEHICLE"] = join_or(_unique(c["vehicle_id"] for c in trains))
        lines = _unique(_line_name(c.get("line")) for c in trains) if trains else []
        if lines:
            tokens["ROUTE"] = join_or(lines)
        elif form.rail_line:
            tokens["ROUTE"] = form.rail_line.removesuffix(" Line")
    return {k: v for k, v in tokens.items() if v}


def make_report(session: Mapping[str, Any], vehicle_keys: Sequence[str] = (), *,
                names: wording.PlaceNames | None = None, hook: wording.RephraseHook | None = None,
                budget_s: float = wording.BUDGET_S) -> tuple[Report, ReportFacts]:
    """The plan's `report` object plus the facts behind it (for the report card's facts and fact_sources)."""
    facts = build_report(session, vehicle_keys)
    words = wording.render(facts.tokens, facts.mode, facts.role, names=names, hook=hook, budget_s=budget_s)
    report = Report(form_fields=facts.form_fields, candidates_note=facts.candidates_note,
                    email=words.email, call_script=words.call_script)
    return report, facts
