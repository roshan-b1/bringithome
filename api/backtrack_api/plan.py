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
  - the archived rail poll at t: which way a meet train is heading ("toward", and Meet.direction, the
    platform's direction, which the client sends back to /now as meet_direction);
  - contacts.py (PREP-REPORT section 6 only): every phone, email, link and opening hour;
  - timeutil (via report.py's formatters): New York clock and day text. Comparisons stay in UTC.

Card order (decision 10, changed Oct 10: the way back comes first, the rest is what to do if it isn't there):
  1 safety       taken is Maybe or Yes: wb_12_safe with MARTA Police and 911
  2 meet         a meet is possible; else the reunion planner's recommended way back when it is a wait
                 (wait_return), the end of the line (meet_end) or a ride and a platform change
                 (switch_platform), each with its facts; else too_far (only when a meet was asked for).
                 tell_staff follows whenever there is no meet: reporting the item never waits on a plan
  3 bag_staff    every transit loss: bag_01_staff; a train loss adds tracks_01 (too_far, when there is no way
                 back, comes after it)
  4 keep         the vehicle summary (plan_body_1)
  5 report       MARTA's form fields with values, an Open form link and a Copy action (plan_body_3)
  6 call         weekday hours, or after hours with the next opening and an ics reminder (plan_body_4_*)
  7 extras       wallet: freeze_cards; phone: phone_lost
  8 police_property, only once contacts.json marks Friday's call as confirming it (not yet)
When the item is a wallet or taken is Maybe or Yes, the order is safety, the way back, bag_staff,
freeze_cards, then the rest. Steps are 1..n in the final order.

Facts carry slot values that fill a copy key's {placeholders}, plus small switches named after what
they turn on: a copy line with no slots of its own (tracks_01, night, borrowed_phone_hint, valued 1 or
0), or a variant (mode, role, open_now, usually, on_train). Switches are sourced "rules", or "contacts"
when contacts hours decide them. api/README.md has the card kind to copy key table.

Which statuses get what: matched and ambiguous name the vehicles in vehicle_keys and may get a meet.
uncertain, not_reporting and no_match never name a vehicle and never get a meet (a low-confidence
guess must not send anyone across town), but still get bag_staff, tell_staff, keep, report (with the
candidates note at uncertain) and call, built from the rider's clue.

The reunion planner (reunion.py) answers alongside the meet, for the same vehicles and the same rider:
reunion.resolve_user decides once where the rider stands (a chosen place, a trusted fix, their train, or
where they got off), and both use it. walk_ahead and shared_track ARE the meet's verdict, and a possible
meet is the reunion's rank 1, so the meet card and the top option can never disagree.

The request may override three of the session's taps for this plan only (the stored session keeps its own):
taken (no, maybe, yes) turns the safety card and its order on or off, car (front, middle, back) is the
platform hint on the meet and the reunion's train options, and item_category (asked on the plan screen, not
before the match) adds the item's own steps (wallet: freeze_cards, first; phone: phone_lost). The reunion
accepts the same body and has no use for item_category. bag_staff is in every plan whatever the item.

Privacy (decision 16): the rider's location in the request's user block goes to meet.plan_meet and the
reunion planner for this one call and is never stored, logged or returned. A plan carries places, times
and seconds, never coordinates (a reunion option carries its stop's own public coordinates).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backtrack_worker.timeutil import ATL

from . import contacts, meet, reunion
from . import report as report_mod
from . import wording
from .models import CardAction, PlanRequest, PlanUser
from .railpred import RailFileCache, StationNames, rail_predictions_at
from .report import FactSource, ReportFacts, join_or, rider_clock, rider_date
from .trains import _direction_toward

UTC = timezone.utc
THEFT = ("maybe", "yes")                        # "Do you think someone took it?"
NAMED = ("matched", "ambiguous")                # statuses whose chosen vehicles the plan names (and may meet)
# The plan is a plan of action: the fastest way back to the item comes first, and everything after it is what to
# do if it isn't there. Only the theft safety card (MARTA Police, 911) goes above it.
WAY_BACK = ("meet", "wait_return", "meet_end", "switch_platform")
ORDER = ("safety",) + WAY_BACK + ("bag_staff", "too_far", "tell_staff", "keep", "report", "call", "freeze_cards",
                                  "phone_lost", "police_property")
REUNION_CARDS = {"wait_here": "wait_return", "wait_on_return": "wait_return", "meet_at_end": "meet_end",
                 "switch_platform": "switch_platform"}
FIRST = ("safety",) + WAY_BACK + ("bag_staff", "freeze_cards")  # wallet or theft: freeze cards right after the way back
LINE_NAMES = report_mod.LINE_NAMES
INFO_LINES = ("customer_service", "route_schedule_info")   # other MARTA lines worth a call while the office is shut

# Card kind -> (the copy keys the phone renders for it, every slot or switch it may carry). The copy
# keys are PREP-REPORT section 10's, except freeze_cards and phone_lost, which decision 10 describes
# but section 10 has no reviewed line for yet. api/README.md shows the same table; tests hold every
# card to it.
REPORT_SLOTS = frozenset({"route_number", "bus_number", "direction", "location", "time", "rail_line", "rail_station",
                          "train_run", "candidates_note", "form_reason"})
REUNION_SLOTS = frozenset({"mode", "vehicle", "label", "train_run", "stop", "time", "window_from", "window_to",
                           "minutes", "walk_min", "leave_by", "toward", "night", "platform_hint", "pulls_out",
                           "departs", "get_off_at", "ride_run", "ride_toward", "off_time", "board_at", "board_time",
                           "confidence", "usually", "assumed"})
COPY: dict[str, tuple[tuple[str, ...], frozenset[str]]] = {
    "safety": (("wb_12_safe",), frozenset({"police_non_emergency", "emergency_911"})),
    "bag_staff": (("bag_01_staff", "tracks_01"), frozenset({"police_non_emergency", "tracks_01"})),
    "meet": (("res_02_meet", "plan_body_2_bus", "res_03_tell_bus", "plan_body_2_train", "res_03_tell_train"),
             frozenset({"mode", "stop", "time", "minutes", "toward", "platform_hint", "night", "on_train"})),
    # the reunion planner's top option when there is no meet (copy keys for the phone's copy table)
    "wait_return": (("reunion_wait",), REUNION_SLOTS),
    "meet_end": (("reunion_end",), REUNION_SLOTS),
    "switch_platform": (("reunion_switch",), REUNION_SLOTS),
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
    """The plan's order, steps 1..n: the way back first (after the theft safety card), then the steps for when it
    isn't there. Wallet or theft moves bag_staff and freeze_cards up to right after the way back."""
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


def heading(index, data_dir: Path, keys: Sequence[str], t: datetime,
            rail_files: RailFileCache | None) -> tuple[str | None, str | None]:
    """(where the meet trains are heading, the direction of their platform), from the same archived
    poll the meet used: ("Airport", "S"). Read at t, so a train that turned since the match shows its
    new way. The direction is None unless every train agrees on it (a tie on two platforms has none)."""
    vids = [k.split(":", 1)[1] for k in keys if k.startswith("rail:")]
    names = StationNames.from_index(index)
    snap = rail_predictions_at(data_dir, t, names=names, cache=rail_files) if vids else None
    trains = [snap.trains[v] for v in vids if v in snap.trains] if snap is not None else []
    if not trains:
        return None, None
    dirs = {tr.direction or _direction_toward(index, tr.line, names.parent(tr.destination)) for tr in trains}
    label = join_or(_unique(tr.destination_label for tr in trains)) or None
    return label, (dirs.pop() if len(dirs) == 1 and len(trains) == len(vids) else None)


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


def reunion_card(option: Mapping, location: str) -> dict:
    """The reunion's recommended option when there is no meet: wait for it to come back (wait_return), meet it
    at the end of the line (meet_end), or ride on and change platforms (switch_platform). Its facts, plus the
    confidence variant and switches for "usually comes back" and an assumed location. Waits and the end of the
    line get a Directions action to the stop's own public coordinates."""
    kind = REUNION_CARDS[option["kind"]]
    f = Facts()
    for slot, value in option["facts"].items():
        f.put(slot, value, option["fact_sources"][slot])
    f.put("confidence", option["confidence"], "rules")
    f.switch("usually", "usually_comes_back" in option["note_ids"])
    f.switch("assumed", location == "assumed_alight")
    actions = []
    if kind != "switch_platform":
        actions.append(CardAction(type="directions", label_id="directions",
                                  value=f"{option['stop_lat']:.6f},{option['stop_lon']:.6f}"))
    return card(kind, f, actions=actions, source="schedule" if option["basis"] == "timetable" else "live")


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
        # open by the posted hours, but on a day the holiday check didn't cover (Columbus Day): say "usually"
        f.switch("usually", office.open_now_usually, contacts.FACT_SOURCE)
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
    that doesn't say how the rider walks keeps the session's answer). Location is never filled in here:
    the request alone says where the rider is now, and the meet runs on exactly that. Only the reunion's
    waits may assume the alight place (reunion.resolve_user)."""
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


def with_taps(session: Mapping, request: PlanRequest) -> Mapping:
    """The session as this plan sees it: the request's taken, car and item_category taps, when sent, over the
    stored ones (item_category replaces intake.item.category)."""
    over: dict = {k: v for k, v in (("taken", request.taken), ("car", request.car)) if v is not None}
    if request.item_category is not None:
        over["item"] = {**(session["intake"].get("item") or {}), "category": request.item_category}
    return {**session, "intake": {**session["intake"], **over}} if over else session


def reunion_keys(match: Mapping, keys: Sequence[str]) -> list[str]:
    """The vehicles a reunion is about: the request's, else the match's own (the top one, both on a tie)."""
    if keys:
        return list(keys)
    cands = [c["vehicle_key"] for c in match.get("candidates") or ()]
    return cands[:1] if match.get("status") == "matched" else cands[:2] if match.get("status") == "ambiguous" else []


def build_reunion(store, index, session: Mapping, request: PlanRequest | Mapping, *, t: datetime, as_of: bool,
                  data_dir: Path, tripupdates=None, rail_files: RailFileCache | None = None) -> dict:
    """POST /reunion: models.Reunion alone, for polling every 20 s with a live fix. No wording and no Gemini:
    the same meet and the same planner the plan uses, on the same rider. A session whose match names no vehicle
    gets no options (not_matched). PlanError for a bad request; database errors propagate."""
    if t.tzinfo is None:
        raise PlanError("t must be timezone-aware")
    request = request if isinstance(request, PlanRequest) else PlanRequest.model_validate(request)
    session = with_taps(session, request)
    intake, match = session["intake"], session["result"]["match"]
    user = rider_now(request.user, intake)
    assumed, location = reunion.resolve_user(index, intake, user)
    keys = reunion_keys(match, request.vehicle_keys)
    known = {c["vehicle_key"] for c in match.get("candidates") or ()}
    if any(k not in known for k in keys):
        raise PlanError(f"vehicle_keys {sorted(set(keys) - known)} are not this session's candidates")
    kinds = reunion.RAIL_KINDS if intake.get("mode") == "rail" else reunion.BUS_KINDS
    if not keys or match["status"] not in NAMED:
        return reunion.nothing(kinds, "not_matched", location, t, as_of=as_of)
    m, r = _meet_and_reunion(store, index, session, keys, user, assumed, location, t, as_of=as_of,
                             data_dir=data_dir, tripupdates=tripupdates, rail_files=rail_files)
    return r


def _meet_and_reunion(store, index, session: Mapping, keys: Sequence[str], user: PlanUser, assumed: PlanUser,
                      location: str, t: datetime, *, as_of: bool, data_dir: Path, tripupdates,
                      rail_files: RailFileCache | None) -> tuple[dict, dict]:
    """The meet (on the request's own user: a place assumed from the alight anchor never becomes a meet card)
    and the reunion (on `assumed`, resolve_user's rider). A train session asked about on a later rail service day
    gets neither: its TRAIN_ID is a run number that another physical train carries today."""
    intake, result = session["intake"], session["result"]
    if reunion.other_service_day(session, keys, t):
        return ({"possible": False, "reason": "stale", "vehicle_keys": list(keys)},
                reunion.nothing(reunion.RAIL_KINDS, "not_live", location, t, as_of=as_of))
    try:
        m = meet.plan_meet(store, index, keys, user, t, as_of=as_of, data_dir=data_dir, car=intake.get("car"),
                           block_linked=meet.block_linked_keys(result), tripupdates=None if as_of else tripupdates,
                           rail_files=rail_files)
        if m is not None and m["possible"]:
            _toward, direction = heading(index, data_dir, m["vehicle_keys"], t, rail_files)
            if direction is not None:      # the platform: "Wait at West End southbound"; /now's meet_direction
                m["direction"] = direction
        r = reunion.plan_reunion(store, index, session, keys, assumed, t, as_of=as_of, data_dir=data_dir,
                                 location=location, meet_result=m, car=intake.get("car"),
                                 tripupdates=None if as_of else tripupdates, rail_files=rail_files)
    except ValueError as e:
        raise PlanError(str(e)) from None
    return m, r


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
    session = with_taps(session, request)
    intake, result = session["intake"], session["result"]
    match = result["match"]
    keys = list(request.vehicle_keys)
    user = rider_now(request.user, intake)
    assumed, location = reunion.resolve_user(index, intake, user)

    names = wording.PlaceNames.from_index(index) if hook is not None else None
    try:
        report, rf = report_mod.make_report(session, keys, names=names, hook=hook)
    except report_mod.ReportError as e:
        raise PlanError(str(e)) from None
    cands = list(match.get("candidates") or ())
    by_key = {c["vehicle_key"]: c for c in cands}
    named = [by_key[k] for k in rf.vehicle_keys]
    # the vehicles the report card stands on: those it names, else those its candidates note lists
    listed = named or (cands[:report_mod.NOTE_MAX] if match["status"] == "uncertain"
                       else cands[:2] if match["status"] == "ambiguous" else [])

    m = r = None
    if keys and match["status"] in NAMED:
        m, r = _meet_and_reunion(store, index, session, keys, user, assumed, location, t, as_of=as_of,
                                 data_dir=data_dir, tripupdates=tripupdates, rail_files=rail_files)

    item = (intake.get("item") or {}).get("category")
    taken = intake.get("taken") in THEFT
    train_loss = rf.mode == "rail" or intake.get("mode") == "rail"
    office = contacts.lost_found_status(t)
    evidence = evidence_of(named)

    cards = [bag_staff_card(train_loss)]
    if taken:
        cards.append(safety_card())
    top = next((o for o in r["options"] if o["recommended"]), None) if r is not None else None
    if m is not None and m["possible"]:
        toward, _direction = heading(index, data_dir, m["vehicle_keys"], t, rail_files)
        cards.append(meet_card(m, t, heading=toward, on_train=user.on_vehicle is not None, evidence=evidence))
    else:
        if top is not None and top["kind"] in REUNION_CARDS:
            cards.append(reunion_card(top, location))
        elif m is not None:
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
            "lost_found": {"open_now": office.open_now, "next_open": office.next_open,
                           "usually": office.open_now_usually if office.open_now else office.next_open_usually},
            "meet": m,
            "cards": order_cards(cards, wallet_or_theft=item == "wallet" or taken),
            "report": report.model_dump(exclude_none=True),
            "source": "as_of" if as_of else "live",
            "reunion": r}
