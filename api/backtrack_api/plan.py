"""The No-Panic Plan (ARCHITECTURE.md decision 10, 6.2 endpoint 10): POST /api/sessions/{id}/plan.

Why the plan is facts and not text: card text is rendered on the phone from copy.ts, the reviewed
PREP-REPORT section 10 table, so safety copy is never reworded, copy edits need no API deploy, and
offline mode needs only facts. This module therefore returns ordered cards of slot values, each slot
with its one source (db, static_gtfs, contacts or rules, section 8 item 1), and each card says whether
it rests on live data, the timetable, or nothing that moves (static). The only server-worded strings
are the report email and the call script, built by report.py and wording.py around opaque tokens.

Where every fact comes from:
  - the saved session (taps and the match): vehicles, routes, lines, stops, pass times and evidence;
  - meet.plan_meet: whether the rider can still meet the bag's vehicle (decision 9, section 7.8), on
    where.vehicle_now and the archived rail poll, read at t;
  - the archived rail poll at t: which way a meet train is heading ("toward");
  - contacts.py (PREP-REPORT section 6 only): every phone, email, link and opening hour;
  - timeutil (via report.py's formatters): New York clock and day text. Comparisons stay in UTC.

Card order (decision 10):
  1 safety       taken is Maybe or Yes: wb_12_safe with MARTA Police and 911
  2 bag_staff    every transit loss: bag_01_staff; a train loss adds tracks_01
  3 meet         a meet is possible, else too_far (only when a meet was asked for) plus tell_staff
  4 keep         the vehicle summary (plan_body_1)
  5 report       MARTA's form fields with values, an Open form link and a Copy action (plan_body_3)
  6 call         weekday hours, or after hours with the next opening and an ics reminder (plan_body_4_*)
  7 extras       wallet: freeze_cards; phone: phone_lost
  8 police_property, only once contacts.json marks Friday's call as confirming it (not yet)
When the item is a wallet or taken is Maybe or Yes, the order is safety, bag_staff, freeze_cards, then
the rest. Steps are 1..n in the final order.

Facts carry slot values that fill a copy key's {placeholders}, plus small switches named after what
they turn on: a copy line with no slots of its own (tracks_01, night, borrowed_phone_hint, valued 1 or
0), or a variant (mode, role, open_now, usually, on_train). Switches are sourced "rules", or "contacts"
when contacts hours decide them. api/README.md has the card kind to copy key table.

Which statuses get what: matched and ambiguous name the vehicles in vehicle_keys and may get a meet.
uncertain, not_reporting and no_match never name a vehicle and never get a meet (a low-confidence
guess must not send anyone across town), but still get bag_staff, tell_staff, keep, report (with the
candidates note at uncertain) and call, built from the rider's clue.

Privacy (decision 16): the rider's location in the request's user block goes to meet.plan_meet for this
one call and is never stored, logged or returned. A plan carries places, times and seconds, never
coordinates.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backtrack_worker.timeutil import ATL

from . import contacts, meet
from . import report as report_mod
from . import wording
from .models import CardAction, PlanRequest, PlanUser
from .railpred import RailFileCache, StationNames, rail_predictions_at
from .report import FactSource, ReportFacts, join_or, rider_clock, rider_date

UTC = timezone.utc
THEFT = ("maybe", "yes")                        # "Do you think someone took it?"
NAMED = ("matched", "ambiguous")                # statuses whose chosen vehicles the plan names (and may meet)
ORDER = ("safety", "bag_staff", "meet", "too_far", "tell_staff", "keep", "report", "call",
         "freeze_cards", "phone_lost", "police_property")
FIRST = ("safety", "bag_staff", "freeze_cards")  # wallet or theft: "freeze cards first"
LINE_NAMES = report_mod.LINE_NAMES
INFO_LINES = ("customer_service", "route_schedule_info")   # other MARTA lines worth a call while the office is shut

# Card kind -> (the copy keys the phone renders for it, every slot or switch it may carry). The copy
# keys are PREP-REPORT section 10's, except freeze_cards and phone_lost, which decision 10 describes
# but section 10 has no reviewed line for yet. api/README.md shows the same table; tests hold every
# card to it.
REPORT_SLOTS = frozenset({"route_number", "bus_number", "direction", "location", "time", "rail_line", "rail_station",
                          "train_run", "candidates_note", "form_reason"})
COPY: dict[str, tuple[tuple[str, ...], frozenset[str]]] = {
    "safety": (("wb_12_safe",), frozenset({"police_non_emergency", "emergency_911"})),
    "bag_staff": (("bag_01_staff", "tracks_01"), frozenset({"police_non_emergency", "tracks_01"})),
    "meet": (("res_02_meet", "plan_body_2_bus", "res_03_tell_bus", "plan_body_2_train", "res_03_tell_train"),
             frozenset({"mode", "stop", "time", "minutes", "toward", "platform_hint", "night", "on_train"})),
    "too_far": (("res_04_far",), frozenset()),
    "tell_staff": (("plan_body_2_none", "res_03_tell_train"),
                   frozenset({"mode", "vehicle", "label", "train_run", "direction"})),
    "keep": (("plan_body_1",), frozenset({"mode", "vehicle", "label", "train_run", "stop", "time", "role"})),
    "report": (("plan_body_3",), REPORT_SLOTS),
    "call": (("plan_body_4_weekday", "plan_body_4_weekend"),
             frozenset({"open_now", "lost_found_phone", "lost_found_hours", "lost_found_transit_time",
                        "lost_found_hold", "next_weekday", "opens_at", "usually", "borrowed_phone_hint"})),
    "freeze_cards": (("freeze_cards",), frozenset({"lost_found_claim"})),
    "phone_lost": (("phone_lost",), frozenset()),
    "police_property": (("police_property",),
                        frozenset({"police_property_unit", "police_property_hold", "police_property_claims"})),
}


class PlanError(ValueError):
    """The request can't be answered for this session: keys that are not its candidates, a malformed
    on_vehicle, a naive time. The API answers 400 bad_request."""


class Facts:
    """A card's slot values, each with its one source. facts and fact_sources share their keys by
    construction, which is what section 8 item 6 tests."""

    def __init__(self) -> None:
        self.values: dict[str, str | int] = {}
        self.sources: dict[str, FactSource] = {}

    def put(self, slot: str, value: str | int | None, source: FactSource) -> None:
        """Set a slot; a missing value leaves the slot out (the copy drops its phrase)."""
        if value is None or value == "":
            return
        self.values[slot] = value
        self.sources[slot] = source

    def switch(self, slot: str, on: bool, source: FactSource = "rules") -> None:
        """A 1/0 switch for a copy line or variant, always present on its card."""
        self.put(slot, 1 if on else 0, source)


def card(kind: str, facts: Facts | None = None, *, actions: Iterable[CardAction] = (),
         evidence: Iterable[Mapping] = (), source: str = "static") -> dict:
    """One models.Card; `step` is set once the order is final."""
    facts = facts or Facts()
    return {"kind": kind, "step": 0, "facts": dict(facts.values), "fact_sources": dict(facts.sources),
            "actions": [a.model_dump() for a in actions], "evidence": list(evidence), "source": source}


def order_cards(cards: Sequence[dict], *, wallet_or_theft: bool) -> list[dict]:
    """Decision 10's order, steps 1..n. Wallet or theft puts safety, bag_staff and freeze_cards first."""
    order = FIRST + tuple(k for k in ORDER if k not in FIRST) if wallet_or_theft else ORDER
    rank = {kind: i for i, kind in enumerate(order)}
    out = sorted(cards, key=lambda c: rank[c["kind"]])   # stable: one card per kind
    for i, c in enumerate(out, start=1):
        c["step"] = i
    return out


# ----------------------------------------------------------------------------- reading the session

def _unique(values: Iterable[str | None]) -> list[str]:
    out: list[str] = []
    for v in values:
        if v and v not in out:
            out.append(v)
    return out


def _parse(t: Any) -> datetime:
    return report_mod._parse(t)


def session_as_of(session: Mapping) -> datetime | None:
    """The session's own as_of (a replayed session plans as_of too), from a datetime or an ISO string."""
    value = session.get("as_of")
    return _parse(value) if value else None


def evidence_of(cands: Sequence[Mapping]) -> list[dict]:
    """models.CardEvidence for the vehicles a card is about: their recorded passes."""
    return [{"role": e["role"], "place_label": e["place_label"], "at": _parse(e["at"]).astimezone(UTC),
             "distance_m": int(e["distance_m"])}
            for c in cands for e in c.get("evidence") or ()]


def vehicle_name(c: Mapping) -> str:
    """"Bus 49" or "Red Line train": the {Bus 49} slot of plan_body_1 and res_01_found."""
    if c.get("mode") == "rail":
        line = LINE_NAMES.get(c.get("line") or "")
        return f"{line} Line train" if line else "Train"
    return f"Bus {c['route']}" if c.get("route") else "Bus"


def ride_slots(f: Facts, rf: ReportFacts, named: Sequence[Mapping]) -> None:
    """vehicle, label and train_run: which bus or train, the same way keep, tell_staff and the form say it.
    A bus is named by its painted fleet number (label); a train by its run number, never a car number."""
    ff = rf.form_fields
    if named:
        f.put("vehicle", join_or(_unique(vehicle_name(c) for c in named)), "db")
    elif rf.mode == "bus" and ff.route_number:
        f.put("vehicle", f"Bus {ff.route_number}", rf.fact_sources["route_number"])
    elif rf.mode == "rail" and ff.rail_line:
        f.put("vehicle", f"{ff.rail_line} train", rf.fact_sources["rail_line"])
    elif rf.mode in ("bus", "rail", "streetcar"):     # only the tapped mode is known
        f.put("vehicle", {"bus": "Bus", "rail": "Train", "streetcar": "Streetcar"}[rf.mode], "rules")
    if ff.bus_number:
        f.put("label", ff.bus_number, rf.fact_sources["bus_number"])
    trains = [c for c in named if c.get("mode") == "rail"]
    if trains:
        f.put("train_run", join_or(_unique(c["vehicle_id"] for c in trains)), "db")


def _times(session: Mapping, rf: ReportFacts, named: Sequence[Mapping]) -> list[datetime]:
    """The same times the report's Time field uses: the named vehicles' passes, else the rider's anchor."""
    return report_mod._evidence_times(named, rf.role) or [_parse(report_mod._primary_anchor(session["intake"])["at"])]


def _clocks(times: Iterable[datetime]) -> str:
    """"1:18 PM", or "3:58 PM or 4:05 PM" for a tie: New York wall clock, cut to the minute."""
    return join_or(_unique(rider_clock(t) for t in sorted(times)))


# ----------------------------------------------------------------------------- the cards

def safety_card() -> dict:
    """wb_12_safe: "Stay safe. Don't follow or confront anyone. [Call MARTA Police] In danger? [Call 911]"."""
    f = Facts()
    for key in ("police_non_emergency", "emergency_911"):
        f.put(key, contacts.contact(key).value, contacts.FACT_SOURCE)
    return card("safety", f, actions=[contacts.action("police_non_emergency", "call_police"),
                                      contacts.action("emergency_911", "call_911")])


def bag_staff_card(train_loss: bool) -> dict:
    """bag_01_staff (with its MARTA Police button), and tracks_01 for a train loss."""
    f = Facts()
    f.put("police_non_emergency", contacts.contact("police_non_emergency").value, contacts.FACT_SOURCE)
    f.switch("tracks_01", train_loss)
    return card("bag_staff", f, actions=[contacts.action("police_non_emergency", "call_police")])


def toward(index, data_dir: Path, keys: Sequence[str], t: datetime, rail_files: RailFileCache | None) -> str | None:
    """Where a meet train is heading, from the same archived poll the meet used ("Airport"): the
    platform to wait on. Read at t, so a train that turned since the match shows its new way."""
    vids = [k.split(":", 1)[1] for k in keys if k.startswith("rail:")]
    if not vids:
        return None
    snap = rail_predictions_at(data_dir, t, names=StationNames.from_index(index), cache=rail_files)
    if snap is None:
        return None
    return join_or(_unique(snap.trains[v].destination_label for v in vids if v in snap.trains)) or None


def meet_card(m: Mapping, t: datetime, *, heading: str | None, on_train: bool, evidence: list[dict]) -> dict:
    """res_02_meet with plan_body_2_bus or plan_body_2_train (and res_03_tell_*). Train extras from
    decision 9: the platform hint from the car answer, and the night line (10 PM to 5 AM)."""
    f = Facts()
    rail = m["vehicle_keys"][0].startswith("rail:")
    f.put("mode", "rail" if rail else "bus", "rules")
    f.put("stop", m["place_label"], "static_gtfs")
    etas = [_parse(e) for e in m["eta"]]
    f.put("time", _clocks(etas), "db")
    f.put("minutes", max(0, math.floor((min(etas) - t).total_seconds() / 60)), "db")
    if rail:
        f.put("toward", heading, "db")
        f.put("platform_hint", m.get("platform_hint"), "rules")
        f.switch("night", meet.is_night(t))
        f.switch("on_train", on_train)
    return card("meet", f, evidence=evidence, source="live")


def too_far_card() -> dict:
    """res_04_far: "Too far to meet right now. That's okay. Here's your plan." No slots."""
    return card("too_far", source="live")


def tell_staff_card(rf: ReportFacts, named: Sequence[Mapping], evidence: list[dict]) -> dict:
    """plan_body_2_none (and res_03_tell_train for a train), with the facts staff need: which vehicle
    (both on a tie) and which way. Facts only: nothing promises MARTA holds or contacts anything."""
    f = Facts()
    f.put("mode", rf.mode, "rules")
    ride_slots(f, rf, named)
    if rf.form_fields.direction:
        f.put("direction", rf.form_fields.direction, rf.fact_sources["direction"])
    return card("tell_staff", f, evidence=evidence, source=rf.source)


def keep_card(session: Mapping, rf: ReportFacts, named: Sequence[Mapping], evidence: list[dict]) -> dict:
    """plan_body_1: "Keep this: {vehicle}, vehicle {label}, got off at {stop} around {time}. [Copy]"."""
    f = Facts()
    f.put("mode", rf.mode, "rules")
    ride_slots(f, rf, named)
    f.put("stop", rf.form_fields.location, rf.fact_sources.get("location", "static_gtfs"))
    times = _times(session, rf, named)
    f.put("time", _clocks(times), rf.fact_sources["time"])
    f.put("role", rf.role, "rules")
    return card("keep", f, actions=[CardAction(type="copy", label_id="keep_copy", value=keep_text(f.values, times))],
                evidence=evidence, source=rf.source)


def keep_text(v: Mapping[str, Any], times: Sequence[datetime]) -> str:
    """What the keep card's Copy puts on the clipboard: its own facts in one line, with the day."""
    parts = [v["vehicle"]] if v.get("vehicle") else []
    if v.get("label"):
        parts.append(f"vehicle {v['label']}")
    if v.get("train_run"):
        parts.append(f"MARTA train run {v['train_run']}")
    verb = "got on" if v.get("role") == "board" else "got off"
    where = f"{verb} at {v['stop']}" if v.get("stop") else verb
    parts.append(f"{where} around {v['time']} on {rider_date(min(times))}")
    text = ", ".join(parts)
    return text[:1].upper() + text[1:] + "."


def report_card(rf: ReportFacts, evidence: list[dict]) -> dict:
    """plan_body_3: the form's fields with our values (plus the candidates note at uncertain), an Open
    form link and Copy details. The form Reason is named only once Friday's call verified it."""
    f = Facts()
    facts, sources = rf.card_facts()
    for slot, value in facts.items():
        f.put(slot, value, sources[slot])
    reason = contacts.CONTACTS.get("lost_found_form_reason")
    if reason.status == "verified" and reason.value:
        f.put("form_reason", reason.value, contacts.FACT_SOURCE)
    return card("report", f, actions=rf.actions(), evidence=evidence, source=rf.source)


def call_card(t: datetime, office: contacts.OfficeStatus) -> dict:
    """plan_body_4_weekday while Lost & Found answers; otherwise plan_body_4_weekend with the next
    opening in New York time (flagged "usually" on an unverified day), an ics reminder for it, and
    "On a borrowed phone? Write it down." The office's email works at any hour."""
    f = Facts()
    f.switch("open_now", office.open_now, contacts.FACT_SOURCE)
    f.put("lost_found_phone", contacts.contact("lost_found_phone").value, contacts.FACT_SOURCE)
    actions = [contacts.action("lost_found_phone", "call_lost_found"),
               contacts.action("lost_found_email", "email_lost_found")]
    if office.open_now:
        for key in ("lost_found_hours", "lost_found_transit_time", "lost_found_hold"):
            f.put(key, contacts.contact(key).value, contacts.FACT_SOURCE)
    else:
        opens = office.next_open.astimezone(ATL)
        f.put("next_weekday", rider_date(opens), contacts.FACT_SOURCE)
        f.put("opens_at", rider_clock(opens), contacts.FACT_SOURCE)
        f.switch("usually", office.next_open_usually, contacts.FACT_SOURCE)
        f.switch("borrowed_phone_hint", True)
        actions.append(CardAction(type="ics", label_id="call_reminder", value=opens.isoformat()))
        seen = {contacts.contact("lost_found_phone").value}
        for line in contacts.line_statuses(t):
            if line.key in INFO_LINES and line.open_now and line.value not in seen:
                seen.add(line.value)
                actions.append(line.action)
    return card("call", f, actions=actions)


def freeze_cards_card() -> dict:
    """Wallet: freeze cards, and ask the office what ID it accepts (claims need a photo ID)."""
    f = Facts()
    f.put("lost_found_claim", contacts.contact("lost_found_claim").value, contacts.FACT_SOURCE)
    return card("freeze_cards", f)


def phone_lost_card() -> dict:
    """Phone: mark it lost from this device. No slots, and no links: none are on the verified list."""
    return card("phone_lost")


def police_property_card() -> dict | None:
    """police_property, only once Friday's call confirmed the unit (contacts.police_property_confirmed)."""
    if not contacts.police_property_confirmed():
        return None
    try:
        unit = contacts.contact("police_property_unit")
        claims = contacts.contact("police_property_claims")
    except KeyError:
        return None
    f = Facts()
    f.put("police_property_unit", unit.value, contacts.FACT_SOURCE)
    f.put("police_property_hold", unit.extra.get("hold"), contacts.FACT_SOURCE)
    f.put("police_property_claims", claims.value, contacts.FACT_SOURCE)
    return card("police_property", f, actions=[contacts.action("police_property_unit", "call_police_property")])


# ----------------------------------------------------------------------------- the plan

def rider_now(user: PlanUser, intake: Mapping) -> PlanUser:
    """The request's user block, with one tap from the session filled in: "I walk slowly" (a request
    that doesn't say how the rider walks keeps the session's answer). Location is never filled in:
    the request alone says where the rider is now."""
    if user.on_vehicle is not None:
        key = user.on_vehicle.vehicle_key
        try:
            mode, _vid = meet.split_key(key)
        except ValueError as e:
            raise PlanError(f"user.on_vehicle: {e}") from None
        if mode != "rail":
            raise PlanError("user.on_vehicle must be a train (rail:<id>)")
    if "walk" not in user.model_fields_set and intake.get("walk") == "slow":
        user = user.model_copy(update={"walk": "slow"})
    return user


def build_plan(store, index, session: Mapping, request: PlanRequest | Mapping, *, t: datetime, as_of: bool,
               data_dir: Path, tripupdates=None, rail_files: RailFileCache | None = None,
               hook: wording.RephraseHook | None = None) -> dict:
    """The POST /plan body (models.Plan) for a saved session at t (now, or as_of).

    session: the stored {id, created_at, as_of, record, intake, result}. request: PlanRequest (0 to 2
    vehicle_keys, the user block). Under as_of (as_of=True, t the replayed moment) nothing live-only is
    read: no TripUpdates, no recorder status; Lost & Found hours are also judged at t. hook: the wording
    model (decision 11), None for template wording. PlanError for a bad request; database errors propagate."""
    if t.tzinfo is None:
        raise PlanError("t must be timezone-aware")
    request = request if isinstance(request, PlanRequest) else PlanRequest.model_validate(request)
    intake, result = session["intake"], session["result"]
    match = result["match"]
    keys = list(request.vehicle_keys)
    user = rider_now(request.user, intake)

    names = wording.PlaceNames.from_index(index) if hook is not None else None
    try:
        report, rf = report_mod.make_report(session, keys, names=names, hook=hook)
    except report_mod.ReportError as e:
        raise PlanError(str(e)) from None
    by_key = {c["vehicle_key"]: c for c in match.get("candidates") or ()}
    named = [by_key[k] for k in rf.vehicle_keys]
    listed = named or ([c for c in match.get("candidates") or ()][:report_mod.NOTE_MAX]
                       if match["status"] == "uncertain" else [])

    m = None
    if keys and match["status"] in NAMED:
        m = meet.plan_meet(store, index, keys, user, t, as_of=as_of, data_dir=data_dir, car=intake.get("car"),
                           block_linked=meet.block_linked_keys(result), tripupdates=None if as_of else tripupdates,
                           rail_files=rail_files)

    item = (intake.get("item") or {}).get("category")
    taken = intake.get("taken") in THEFT
    train_loss = rf.mode == "rail" or intake.get("mode") == "rail"
    office = contacts.lost_found_status(t)
    evidence = evidence_of(named)

    cards = [bag_staff_card(train_loss)]
    if taken:
        cards.append(safety_card())
    if m is not None and m["possible"]:
        cards.append(meet_card(m, t, heading=toward(index, data_dir, m["vehicle_keys"], t, rail_files),
                               on_train=user.on_vehicle is not None, evidence=evidence))
    else:
        if m is not None:
            cards.append(too_far_card())
        cards.append(tell_staff_card(rf, named, evidence))
    cards += [keep_card(session, rf, named, evidence), report_card(rf, evidence_of(listed)), call_card(t, office)]
    if item == "wallet":
        cards.append(freeze_cards_card())
    if item == "phone":
        cards.append(phone_lost_card())
    police = police_property_card()
    if police is not None:
        cards.append(police)

    return {"generated_at": t.astimezone(UTC),
            "lost_found": {"open_now": office.open_now, "next_open": office.next_open},
            "meet": m,
            "cards": order_cards(cards, wallet_or_theft=item == "wallet" or taken),
            "report": report.model_dump(exclude_none=True),
            "source": "as_of" if as_of else "live"}
