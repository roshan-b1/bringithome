"""The report email and call script (ARCHITECTURE.md section 8 items 4 and 6): templates hold no values and pass the
copy lint, the validator turns away every adversarial rewording (each falls back to the template), a clean
rewording is used, and the hook only ever sees value-free templates. Plus the copy lint's own rules."""

import time
from datetime import date

import pytest

from backtrack_worker.static_gtfs import DEFAULT_ZIP, build_feed
from backtrack_api import wording
from backtrack_api.copylint import lint
from backtrack_api.resolver import PlaceIndex
from backtrack_api.wording import PlaceNames, all_templates, render, substitute, templates_for, validate
from gtfs_fixture import build_zip

VALUES = {"VEHICLE": "1631", "ROUTE": "49", "STOP": "Piedmont Ave SE @ Gilmer St SE", "TIME": "about 1:18 PM",
          "DIRECTION": "toward North Ave Station", "DATE": "Friday, October 9"}
BUS = templates_for("bus", VALUES, "alight")


@pytest.fixture(scope="module")
def names(tmp_path_factory):
    feed = build_feed(build_zip(tmp_path_factory.mktemp("gtfs") / "f.zip"), window_start=date(2026, 10, 9),
                      days=4, alias_seeds=None)
    return PlaceNames.from_index(PlaceIndex.from_feed(feed))


# ----------------------------------------------------------------------------- templates


def test_every_template_string_passes_the_copy_lint():
    for t in all_templates():
        for key, text in t.strings().items():
            assert lint(text) == [], (t.key, key, lint(text))


def test_templates_hold_tokens_and_no_values():
    for t in all_templates():
        for text in t.strings().values():
            outside = wording.TOKEN.sub("", text)
            assert not any(ch.isdigit() for ch in outside), t.key
            assert "[" not in outside and "]" not in outside, t.key
            assert "[[ITEM]]" in text or text == t.subject


def test_every_template_passes_its_own_validator(names):
    for t in all_templates():
        for text in (t.email_body, t.call_script):
            assert validate(text, text, names) == [], t.key


@pytest.mark.skipif(not DEFAULT_ZIP.exists(), reason="needs the real schedule zip")
def test_every_template_passes_its_own_validator_against_the_real_index():
    """The real index has about 9,000 place names; no template word may collide with one."""
    real = PlaceNames.from_index(PlaceIndex.from_feed(build_feed(DEFAULT_ZIP, window_start=date(2026, 10, 8), days=2)))
    assert len(real.phrases) > 5000
    for t in all_templates():
        for text in (t.email_body, t.call_script):
            assert validate(text, text, real) == [], t.key
    assert real.find("we met near Five Points station") == ["five points"]


def test_a_missing_vehicle_number_drops_its_whole_phrase():
    no_vehicle = templates_for("bus", {k: v for k, v in VALUES.items() if k != "VEHICLE"})
    assert "[[VEHICLE]]" not in no_vehicle.email_body
    assert "on a route [[ROUTE]] bus, heading [[DIRECTION]]." in no_vehicle.email_body
    bare = templates_for("bus", {"STOP": "x", "TIME": "y", "DATE": "z"})
    assert "I left [[ITEM]] on a bus." in bare.call_script and "route" not in bare.call_script.lower()
    rail = templates_for("rail", {"ROUTE": "Red", "STOP": "x", "TIME": "y", "DATE": "z"})
    assert "train run" not in rail.email_body and "on a [[ROUTE]] Line train." in rail.email_body
    rail_run = templates_for("rail", {"ROUTE": "Red", "VEHICLE": "405", "STOP": "x", "TIME": "y", "DATE": "z"})
    assert "MARTA's public feeds list it as train run [[VEHICLE]]." in rail_run.email_body
    unknown = templates_for("unknown", VALUES)        # vehicle and route can't be said for a bus-or-train report
    assert "on a bus or train, heading [[DIRECTION]]." in unknown.call_script
    assert "[[VEHICLE]]" not in unknown.call_script and "[[ROUTE]]" not in unknown.call_script


def test_templates_never_promise_a_hold_or_a_driver_contact():
    for t in all_templates():
        for text in t.strings().values():
            low = text.lower()
            assert "hold" not in low and "driver" not in low and "operator" not in low, t.key


def test_no_hook_gives_the_template_with_every_token_but_item_filled():
    out = render(VALUES, "bus", "alight")
    assert out.email.wording == "template" and out.call_script.wording == "template"
    assert out.email.subject == "Lost item on bus 1631, route 49, Friday, October 9"
    assert ("I left [[ITEM]] on bus 1631, route 49, heading toward North Ave Station. I got off at "
            "Piedmont Ave SE @ Gilmer St SE on Friday, October 9, at about 1:18 PM.") in out.email.body
    assert out.call_script.text.count("[[ITEM]]") == 1
    assert "[[" not in out.call_script.text.replace("[[ITEM]]", "")


def test_substitution_refuses_a_missing_value_or_a_token_shaped_value():
    with pytest.raises(ValueError):
        substitute("on bus [[VEHICLE]]", {})
    with pytest.raises(ValueError):
        substitute("on bus [[VEHICLE]]", {"VEHICLE": "[[ROUTE]]"})
    assert substitute("[[ITEM]] on bus [[VEHICLE]]", {"VEHICLE": "1631"}) == "[[ITEM]] on bus 1631"


# ----------------------------------------------------------------------------- the validator, adversarially

GOOD_CALL = ("Hello, I'm calling about a lost item. I left [[ITEM]] on bus [[VEHICLE]], route [[ROUTE]], going "
             "[[DIRECTION]], and I got off at [[STOP]] on [[DATE]], at [[TIME]]. I've sent the online report too. "
             "Has anything like that been turned in, and what should I do next?")


def _with(template: str, old: str, new: str) -> str:
    assert old in template
    return template.replace(old, new, 1)


ADVERSARIAL = {
    "an inserted digit": lambda t: _with(t, "I left", "I left 2 things,"),
    "a clock time": lambda t: _with(t, "at [[TIME]]", "at [[TIME]], around 3:10 PM"),
    "am/pm alone": lambda t: _with(t, "at [[TIME]]", "at [[TIME]] PM"),
    "a station name": lambda t: _with(t, "I left", "Near Five Points, I left"),
    "a stop name": lambda t: _with(t, "I left", "On Piedmont Ave SE @ Gilmer St SE, I left"),
    "a line color": lambda t: _with(t, "I left", "On the Red line, I left"),
    "a route name": lambda t: _with(t, "I left", "On McDonough Blvd, I left"),
    "a phone number": lambda t: _with(t, "Hi,", "Hi, (I tried 404-848-...)"),
    "a spelled phone": lambda t: _with(t, "Hi,", "Hi, I called four oh four,"),
    "a URL": lambda t: _with(t, "Hi,", "Hi, per https://itsmarta.com/lostfound.aspx,"),
    "a bare domain": lambda t: _with(t, "Hi,", "Hi, per itsmarta.com,"),
    "an email": lambda t: _with(t, "Hi,", "Hi, I wrote to lostandfound@itsmarta.com,"),
    "a weekday": lambda t: _with(t, "on [[DATE]]", "on [[DATE]] (a Friday)"),
    "a month": lambda t: _with(t, "on [[DATE]]", "on [[DATE]] in October"),
    "a relative day": lambda t: _with(t, "on [[DATE]]", "on [[DATE]], today"),
    "a missing token": lambda t: _with(t, " at [[STOP]]", ""),
    "a doubled token": lambda t: _with(t, "bus [[VEHICLE]]", "bus [[VEHICLE]] ([[VEHICLE]])"),
    "an unknown token": lambda t: _with(t, "Hi,", "Hi, [[NAME]] here,"),
    "a broken token": lambda t: _with(t, "[[ROUTE]]", "[[ROUTE]"),
    "a banned word": lambda t: _with(t, "Has anything", "Please hurry. Has anything"),
    "a run instruction": lambda t: _with(t, "Has anything", "I will run to the stop. Has anything"),
    "a promise to hold the bus": lambda t: _with(t, "Has anything", "Can you hold the bus? Has anything"),
    "the abbreviation": lambda t: _with(t, "Hi,", "Hi, an AI assistant here,"),
    "an em dash": lambda t: _with(t, "Hi,", "Hi \u2014"),
    "an en dash": lambda t: _with(t, "Hi,", "Hi \u2013"),
    "a double hyphen": lambda t: _with(t, "Hi,", "Hi --"),
    "a 2x-length reply": lambda t: t + " " + "I really appreciate any help you can give me with this. " * 6,
    "a 1.6x-length reply": lambda t: _grow(t, 1.6),
    "an empty reply": lambda t: "   ",
    # each of these is caught by one rule alone, so dropping that rule fails the suite
    "a short weekday": lambda t: _with(t, "on [[DATE]]", "on [[DATE]] (Fri)"),
    "noon": lambda t: _with(t, "Has anything", "I called around noon. Has anything"),
    "the weekend": lambda t: _with(t, "Has anything", "I was out this weekend. Has anything"),
    "an email without a domain": lambda t: _with(t, "Hi,", "Hi, I wrote to lostandfound@itsmarta,"),
    # spelled-out times built on "one", ordinals, more times of day
    "a quarter after one": lambda t: _with(t, "I left", "At a quarter after one, I left"),
    "at one": lambda t: _with(t, "I left", "At one, I left"),
    "half one": lambda t: _with(t, "I left", "Around half one, I left"),
    "one o'clock": lambda t: _with(t, "I left", "By one o'clock I left"),
    "midday": lambda t: _with(t, "I left", "At midday I left"),
    "last night": lambda t: _with(t, "I left", "Last night I left"),
    "late at night": lambda t: _with(t, "I left", "Late at night I left"),
    "an ordinal": lambda t: _with(t, "on [[DATE]]", "on [[DATE]], the ninth"),
    "the second stop": lambda t: _with(t, "I got off at [[STOP]]", "I got off at the second stop, [[STOP]]"),
    "my fifth ride": lambda t: _with(t, "I left", "On my fifth ride, I left"),
    # a shift next to a token moves the fact it carries
    "the stop after": lambda t: _with(t, "at [[STOP]]", "at the stop after [[STOP]]"),
    "the day after": lambda t: _with(t, "on [[DATE]]", "the day after [[DATE]]"),
    "an hour before": lambda t: _with(t, "at [[TIME]]", "an hour before [[TIME]]"),
    "half an hour after": lambda t: _with(t, "at [[TIME]]", "half an hour after [[TIME]]"),
    "a week before": lambda t: _with(t, "on [[DATE]]", "a week before [[DATE]]"),
    "or a bit later": lambda t: _with(t, "at [[TIME]]", "at [[TIME]] or a bit later"),
    "a duration": lambda t: _with(t, "Has anything", "I waited for an hour. Has anything"),
    # a word that names a fact has moved or changed: the tokens are intact, the facts are not
    "swapped bus and route numbers": lambda t: _with(t, "bus [[VEHICLE]], route [[ROUTE]]",
                                                     "bus [[ROUTE]], route [[VEHICLE]]"),
    "a bus number called a route": lambda t: _with(t, "bus [[VEHICLE]]", "route [[VEHICLE]] bus"),
    "got on for got off": lambda t: _with(t, "I got off at", "I got on at"),
    "departed for got off": lambda t: _with(t, "I got off at", "I departed at"),
    # a word before a token that doesn't fit the value that replaces it
    "my before the item": lambda t: _with(t, "I left [[ITEM]]", "I left my [[ITEM]]"),
    "around before the time": lambda t: _with(t, "at [[TIME]]", "around [[TIME]]"),
    "near the stop": lambda t: _with(t, "at [[STOP]]", "near [[STOP]]"),
    # a negation, or a word that moves the fact, next to a token (the review's cases, Oct 9)
    "never got off": lambda t: _with(t, "I got off at [[STOP]]", "I never got off at [[STOP]]"),
    "a negated stop": lambda t: _with(t, "I got off at [[STOP]] on", "I got off, though not at [[STOP]], on"),
    "one stop further": lambda t: _with(t, "at [[STOP]] on", "at [[STOP]], or one stop further, on"),
    "or afterwards": lambda t: _with(t, "at [[TIME]].", "at [[TIME]] or afterwards."),
    "a day-only shift": lambda t: _with(t, "at [[TIME]].", "at [[TIME]], late in the day."),
    "got off and got on": lambda t: _with(t, "I got off at", "I got off and got on at"),
    # the direction: only after a verb of travel, and no compass word of the model's own
    "heading away from": lambda t: _with(t, "heading [[DIRECTION]]", "heading away from [[DIRECTION]]"),
    "coming from": lambda t: _with(t, "heading [[DIRECTION]]", "coming from [[DIRECTION]]"),
    "a negated direction": lambda t: _with(t, "heading [[DIRECTION]]", "heading away from the city, not [[DIRECTION]]"),
    "a compass word": lambda t: _with(t, "heading [[DIRECTION]]", "heading [[DIRECTION]], northbound"),
    "a bound word": lambda t: _with(t, "Has anything", "It was outbound. Has anything"),
    "no verb of travel": lambda t: _with(t, "heading [[DIRECTION]]", "at [[DIRECTION]]"),
    # the item stays on the vehicle, and no item of the model's own
    "left at the shelter": lambda t: _with(t, "I left [[ITEM]] on bus", "I left [[ITEM]] at the shelter by bus"),
    "near the bus": lambda t: _with(t, "on bus [[VEHICLE]]", "near bus [[VEHICLE]]"),
    "an invented item": lambda t: _with(t, "I left [[ITEM]]", "I left [[ITEM]] and my black backpack"),
    "an item of its own": lambda t: _with(t, "Has anything", "It was in my backpack. Has anything"),
    "a person's name": lambda t: _with(t, "Has anything", "The man, Marcus, saw it. Has anything"),
    # times of day, counts, places and numerals the denylist missed
    "dinnertime": lambda t: _with(t, "Has anything", "I noticed at dinnertime. Has anything"),
    "breakfast": lambda t: _with(t, "Has anything", "I noticed at breakfast. Has anything"),
    "after school": lambda t: _with(t, "Has anything", "It was after school. Has anything"),
    "the commute": lambda t: _with(t, "Has anything", "It was during the commute. Has anything"),
    "a couple of stops": lambda t: _with(t, "Has anything", "I rode a couple of stops. Has anything"),
    "several": lambda t: _with(t, "Has anything", "I asked several people. Has anything"),
    "twice": lambda t: _with(t, "Has anything", "I called twice. Has anything"),
    "both": lambda t: _with(t, "Has anything", "I had both of them. Has anything"),
    "a street name": lambda t: _with(t, "Has anything", "I was on Piedmont. Has anything"),
    "a lower-case street": lambda t: _with(t, "Has anything", "I walked down piedmont avenue. Has anything"),
    "a landmark": lambda t: _with(t, "Has anything", "I was near Ponce City Market. Has anything"),
    "the Capitol": lambda t: _with(t, "Has anything", "I was near the Capitol. Has anything"),
    "a bay letter": lambda t: _with(t, "Has anything", "I waited at bay c. Has anything"),
    "a circled digit": lambda t: _with(t, "Has anything", "I was on platform \u2461. Has anything"),
    "a roman numeral": lambda t: _with(t, "Has anything", "I was near gate \u216b. Has anything"),
    "a superscript": lambda t: _with(t, "Has anything", "I think\u00b2. Has anything"),
}


def _grow(template: str, factor: float) -> str:
    """A clean reply padded past `factor` times the template's length (and well under 2x)."""
    out = template
    while len(out) < factor * len(template):
        out += " Thank you for your help."
    assert len(out) < 1.75 * len(template)
    return out


@pytest.mark.parametrize("case", sorted(ADVERSARIAL))
def test_adversarial_rewordings_fail_validation(case, names):
    candidate = ADVERSARIAL[case](BUS.call_script)
    assert validate(candidate, BUS.call_script, names), case


@pytest.mark.parametrize("case", sorted(ADVERSARIAL))
def test_adversarial_rewordings_fall_back_to_the_template(case, names):
    def hook(templates):
        return {"email_body": templates["email_body"].replace("Hello,", "Hello 1,"),
                "call_script": ADVERSARIAL[case](templates["call_script"])}
    out = render(VALUES, "bus", "alight", names=names, hook=hook)
    template = render(VALUES, "bus", "alight")
    assert out.call_script.wording == "template" and out.call_script.text == template.call_script.text
    assert out.email.wording == "template" and out.email.body == template.email.body
    assert set(out.rejected) == {"email_body", "call_script"}


def test_a_clean_rewording_is_used_and_filled(names):
    assert validate(GOOD_CALL, BUS.call_script, names) == []
    out = render(VALUES, "bus", "alight", names=names, hook=lambda t: {"call_script": GOOD_CALL})
    assert out.call_script.wording == "gemini"
    assert "on bus 1631, route 49, going toward North Ave Station" in out.call_script.text
    assert "[[ITEM]]" in out.call_script.text
    assert out.email.wording == "template"       # each string stands or falls on its own


def test_each_string_is_validated_separately(names):
    good_body = BUS.email_body.replace("Hello,", "Good day,")
    out = render(VALUES, "bus", "alight", names=names,
                 hook=lambda t: {"email_body": good_body, "call_script": t["call_script"] + " 5"})
    assert (out.email.wording, out.call_script.wording) == ("gemini", "template")
    assert out.email.body.startswith("Good day,")


def test_the_hook_sees_only_value_free_templates(names):
    seen = {}

    def hook(templates):
        seen.update(templates)
        return None
    render(VALUES, "bus", "alight", names=names, hook=hook)
    assert set(seen) == {"email_body", "call_script"}
    for text in seen.values():
        assert not any(ch.isdigit() for ch in text)
        for value in VALUES.values():
            assert value not in text


@pytest.mark.parametrize("hook", [
    lambda t: (_ for _ in ()).throw(RuntimeError("429")),      # the model refused
    lambda t: None,                                             # no answer
    lambda t: ["not", "a", "mapping"],
    lambda t: {"call_script": 42},
])
def test_a_failing_hook_falls_back_to_the_template(hook, names):
    out = render(VALUES, "bus", "alight", names=names, hook=hook)
    assert (out.email.wording, out.call_script.wording) == ("template", "template")


def test_an_answer_after_the_budget_falls_back_to_the_template(names):
    def slow(t):
        time.sleep(0.3)
        return {"call_script": GOOD_CALL}
    started = time.monotonic()
    out = render(VALUES, "bus", "alight", names=names, hook=slow, budget_s=0.05)
    assert out.call_script.wording == "template"
    assert time.monotonic() - started < 0.25


def test_without_place_names_the_hook_is_never_asked():
    asked = []
    out = render(VALUES, "bus", "alight", names=None, hook=lambda t: asked.append(t) or {"call_script": GOOD_CALL})
    assert not asked and out.call_script.wording == "template"


def test_shifted_times_places_and_dates_never_reach_the_rider(names):
    """The review's case: every clause moves a fact its token carries, so neither string is used."""
    call = _with(BUS.call_script, "I got off at [[STOP]] on [[DATE]], at [[TIME]].",
                 "I got off at the stop after [[STOP]] the day after [[DATE]], an hour before [[TIME]].")
    body = _with(BUS.email_body, "I'm writing to report a lost item.",
                 "Last night, at a quarter after one, on my second ride, I lost something.")
    out = render(VALUES, "bus", "alight", names=names, hook=lambda t: {"email_body": body, "call_script": call})
    assert (out.email.wording, out.call_script.wording) == ("template", "template")
    assert out.call_script.text == render(VALUES, "bus", "alight").call_script.text


def test_plain_uses_of_one_after_and_day_still_pass(names):
    """The new rules are narrow: "one" as a pronoun, "after" well away from a token, and a greeting's "day"."""
    call = ("Hi, I'm calling to report a lost item. After I got off at [[STOP]] on [[DATE]], at [[TIME]], I saw I "
            "had left [[ITEM]] on bus [[VEHICLE]], route [[ROUTE]], heading [[DIRECTION]]. I've also sent the online "
            "report. Has anyone turned one in, and what should I do next?")
    assert validate(call, BUS.call_script, names) == []
    assert validate(BUS.email_body.replace("Hello,", "Good day,"), BUS.email_body, names) == []


def test_plain_words_that_look_like_dates_or_times_are_fine(names):
    template = "I left [[ITEM]] on [[DATE]] at [[TIME]]."
    assert validate("May I ask about it? I left [[ITEM]] on [[DATE]] at [[TIME]].", template, names) == []
    assert validate("I am sure I left [[ITEM]] on [[DATE]] at [[TIME]].", template, names) == []
    assert validate("I left [[ITEM]] in May, on [[DATE]] at [[TIME]].", template, names)
    assert validate("I left [[ITEM]] on [[DATE]] at [[TIME]] p.m.", template, names)


def test_place_names_come_from_stops_stations_aliases_and_routes(names):
    assert names.find("near five points station") == ["five points"]
    assert "piedmont avenue se gilmer street se" in {" ".join(p) for p in names.phrases}
    assert names.find("on the Gold line") == ["gold"]
    assert names.find("I left my bag on the bus") == []


# ----------------------------------------------------------------------------- the word before a token, and roles
# (the stricter checks added with the Gemini hook, from real answers on Oct 9)

# Sentences the real model wrote for the full train and bus templates: they passed every fact check, but "my
# [[ITEM]]" would read "my a black backpack" and "around [[TIME]]" "around about 3:34 PM".
MISFITS = [
    "I accidentally left my [[ITEM]] on a [[ROUTE]] Line train.",
    "I stepped off at [[STOP]] on [[DATE]], around [[TIME]].",
    "I exited at [[STOP]] on [[DATE]], near [[TIME]].",
    "I left [[ITEM]] on a train going toward [[DIRECTION]].",
    "I got off at the [[STOP]] on [[DATE]], at [[TIME]].",
    "I got off at [[STOP]] [[DATE]], at [[TIME]].",
    "My journey ended [[STOP]] on [[DATE]], at [[TIME]].",
    "[[TIME]] is when I got off.",
    "I left [[ITEM]] on the [[VEHICLE]].",
]
FITS = [
    "I misplaced [[ITEM]] aboard a [[ROUTE]] Line train moving [[DIRECTION]].",
    "I stepped off at [[STOP]] on [[DATE]], at [[TIME]].",
    "I left [[ITEM]] on bus [[VEHICLE]], route [[ROUTE]], traveling [[DIRECTION]].",
]


@pytest.mark.parametrize("sentence", MISFITS)
def test_a_word_before_a_token_must_fit_the_value_that_replaces_it(sentence, names):
    assert wording._misfits(sentence), sentence
    assert any("doesn't fit its value" in p for p in validate(sentence, sentence, names))


@pytest.mark.parametrize("sentence", FITS)
def test_natural_rephrasings_around_tokens_still_fit(sentence, names):
    assert wording._misfits(sentence) == []
    assert validate(sentence, sentence, names) == []


def test_every_template_fits_its_own_tokens():
    for t in all_templates():
        for text in (t.email_body, t.call_script):
            assert wording._misfits(text) == [] and wording._role_changed(text, text) == [], t.key


@pytest.mark.parametrize("role,old,new", [
    ("alight", "I got off at [[STOP]]", "I departed at [[STOP]]"),      # a real answer for a rider who got off
    ("alight", "I got off at [[STOP]]", "I got on at [[STOP]]"),
    ("alight", "I got off at [[STOP]]", "I boarded at [[STOP]]"),
    ("alight", "I got off at [[STOP]]", "I was at [[STOP]]"),
    ("board", "I got on at [[STOP]]", "I got off at [[STOP]]"),
    ("board", "I got on at [[STOP]]", "I exited at [[STOP]]"),
    ("board", "I got on at [[STOP]]", "I departed from [[STOP]]"),
])
def test_got_off_or_got_on_must_stay_as_the_template_says(role, old, new, names):
    t = templates_for("bus", VALUES, role)
    for template in (t.email_body, t.call_script):
        assert old in template
        assert any("role changed" in p for p in validate(template.replace(old, new), template, names))
        assert validate(template.replace(old, old.replace("I ", "I think I ", 1)), template, names) == []


@pytest.mark.parametrize("role,old,new", [
    ("alight", "I got off at [[STOP]]", "I stepped off at [[STOP]]"),
    ("alight", "I got off at [[STOP]]", "I exited at [[STOP]]"),
    ("alight", "I got off at [[STOP]]", "I disembarked at [[STOP]]"),
    ("board", "I got on at [[STOP]]", "I boarded at [[STOP]]"),
    ("board", "I got on at [[STOP]]", "I stepped onto it at [[STOP]]"),
])
def test_a_plain_synonym_for_got_off_or_got_on_keeps_the_role(role, old, new, names):
    t = templates_for("bus", VALUES, role)
    for template in (t.email_body, t.call_script):
        assert validate(template.replace(old, new), template, names) == []


@pytest.mark.parametrize("mode,old,new", [
    ("bus", "bus [[VEHICLE]], route [[ROUTE]]", "bus [[ROUTE]], route [[VEHICLE]]"),   # every token kept, swapped
    ("bus", "bus [[VEHICLE]], route [[ROUTE]]", "[[VEHICLE]], route [[ROUTE]]"),
    ("rail", "train run [[VEHICLE]]", "railcar [[VEHICLE]]"),          # a run number is never a car number
    ("rail", "train run [[VEHICLE]]", "train [[VEHICLE]]"),
    ("rail", "a [[ROUTE]] Line train", "a train on line [[ROUTE]]"),
    ("bus", "route [[ROUTE]]", "stop [[ROUTE]]"),                      # the route number relabeled, bus kept
])
def test_the_word_that_names_each_number_stays_next_to_it(mode, old, new, names):
    t = templates_for(mode, VALUES, "alight")
    for template in (t.email_body, t.call_script):
        problems = validate(template.replace(old, new), template, names)
        assert any("role changed" in p for p in problems), problems


@pytest.mark.parametrize("mode,old,new", [
    ("bus", "bus [[VEHICLE]], route [[ROUTE]]", "bus number [[VEHICLE]] on route [[ROUTE]]"),
    ("rail", "a [[ROUTE]] Line train", "a [[ROUTE]] line train"),
    ("rail", "MARTA's public feeds list it as train run [[VEHICLE]]", "Public feeds show it as train run [[VEHICLE]]"),
])
def test_the_number_words_may_be_rephrased_around_their_anchor(mode, old, new, names):
    t = templates_for(mode, VALUES, "alight")
    for template in (t.email_body, t.call_script):
        assert validate(template.replace(old, new), template, names) == []


@pytest.mark.parametrize("mode,have,old,new", [
    ("rail", VALUES, "a [[ROUTE]] Line train", "a [[ROUTE]] Line bus"),
    ("bus", {"ROUTE": "49", "STOP": "x"}, "a route [[ROUTE]] bus", "a route [[ROUTE]] train"),
    ("bus", {"ROUTE": "49", "STOP": "x"}, "a route [[ROUTE]] bus", "a route [[ROUTE]] streetcar"),
    ("streetcar", VALUES, "the streetcar", "the bus"),
    ("streetcar", VALUES, "the streetcar", "a train"),
    ("unknown", VALUES, "a bus or train", "a bus"),
])
def test_the_kind_of_vehicle_stays_as_the_template_says(mode, have, old, new, names):
    t = templates_for(mode, have, "alight")
    for template in (t.email_body, t.call_script):
        assert old in template
        problems = validate(template.replace(old, new), template, names)
        assert any("role changed" in p for p in problems), problems


def test_a_rewording_may_still_name_its_own_vehicle_again(names):
    t = templates_for("bus", VALUES, "alight")
    again = t.call_script.replace("I got off at [[STOP]]", "I got off the bus at [[STOP]]")
    assert validate(again, t.call_script, names) == []


def test_a_real_rail_rewording_passes_every_check(names):
    """The rail call script Gemini wrote on Oct 9 (gemini-3.5-flash-lite), as cached."""
    t = templates_for("rail", VALUES, "alight")
    real = ("Hi, I am calling about a missing item. I accidentally left [[ITEM]] aboard a [[ROUTE]] Line train "
            "traveling [[DIRECTION]]. Public feeds show it as train run [[VEHICLE]]. I got off at [[STOP]] on "
            "[[DATE]], at [[TIME]]. I also sent an online report. Could you tell me if anything matches this "
            "description, and what I ought to do?")
    assert validate(real, t.call_script, names) == []


# ----------------------------------------------------------------------------- the copy lint itself


@pytest.mark.parametrize("text", [
    "Please don't follow anyone. Staff and police can help.",              # wb_12_safe, spoken
    "Stay safe. Don't follow or confront anyone.",                         # wb_12_safe, on screen
    "If it fell onto the tracks, don't go after it. Tell station staff.",  # tracks_01
    "MARTA train run 405, from MARTA's public feeds",                      # the report's run-number line
    "possibly one of MARTA train runs 405, 310 or 402, from MARTA's public feeds",
    "Recorders running on Vultr",                                          # proof footer
    "No running for vehicles, even for the camera.",
    "When the bus pulls up, tell the driver what you lost and where you sat.",
    "Wait near the front of the platform, back from the edge.",
    "Don\u2019t follow anyone.",                                           # curly apostrophe
])
def test_the_allowlist_lets_negated_safety_lines_and_run_numbers_through(text):
    assert lint(text) == []


@pytest.mark.parametrize("text,kind", [
    ("Run to the stop!", "banned_word"), ("Hurry, it's leaving.", "banned_word"), ("Hold the doors.", "banned_word"),
    ("You can still catch it.", "banned_word"), ("Chase the bus.", "banned_word"), ("Don't wait, run.", "banned_word"),
    ("Our AI found it.", "banned_word"), ("A.I. matching", "banned_word"), ("It helps as therapy.", "banned_word"),
    ("The bus is running late.", "banned_word"), ("Cross the tracks to the other side.", "banned_word"),
    ("MARTA will hold the train for you.", "promise"), ("We'll contact the driver.", "promise"),
    ("The bus will wait for you.", "promise"), ("Wait here \u2014 slowly.", "dash"), ("8\u20139 AM", "dash"),
    ("Step one -- tell staff.", "dash"), ("No, run for it.", "banned_word"), ("Jump on.", "banned_word"),
    ("Rush over.", "banned_word"), ("It is a treatment.", "banned_word"),
])
def test_the_lint_flags_unsafe_and_off_brand_copy(text, kind):
    assert kind in {p.kind for p in lint(text)}


def test_banned_words_match_only_whole_words():
    assert lint("Brunch at the plaza, then the aisle by the outdoor Hawaii display.") == []
