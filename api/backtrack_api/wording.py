"""The report email and the call script: tokenized templates, the rephrase validator, and substitution
(ARCHITECTURE.md decision 10 "Wording" and section 8 items 4 and 6).

Why tokens: the two strings a model may reword are the only free text in a plan, and they carry the facts that
matter most (which bus, where, when). So the server builds each one as a template whose facts are opaque tokens
([[VEHICLE]] [[ROUTE]] [[STOP]] [[TIME]] [[DIRECTION]] [[DATE]] [[ITEM]]) and which holds no values at all. A
rephrase hook (Gemini, in the next feature) sees only those value-free templates and may only reword around the
tokens. Its answer must then pass every check in validate(): same tokens the same number of times, no digits,
number words, ordinals, clock or time-of-day words, durations, date, phone, email, URL or place name outside the
tokens, no relative word next to a token ("the stop after [[STOP]]", "an hour before [[TIME]]": the token is
intact but its fact has moved), the copy lint, and at most 1.5 times the template's length. Anything else, an exception, or no answer inside the budget falls back to the template, which
says the same thing. Only after that are the tokens filled, every one except [[ITEM]], which the phone fills from
the item description that never leaves it. Gemini therefore can't introduce a time, number, station, stop,
vehicle or phone, by construction.

Templates are composed from a fixed set of fragments chosen by which facts exist, so a missing vehicle number
drops its whole phrase ("a route 49 bus" instead of "bus [[VEHICLE]]") and never leaves a dangling token. They
never promise that MARTA holds a vehicle or contacts a driver.
"""

from __future__ import annotations

import itertools
import re
import weakref
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Any, Iterable, Literal, Mapping, Protocol

from backtrack_worker.places import normalize_place

from .copylint import lint
from .models import EmailWording, Wording

TOKEN_NAMES = ("VEHICLE", "ROUTE", "STOP", "TIME", "DIRECTION", "DATE", "ITEM")
TOKEN = re.compile(r"\[\[(" + "|".join(TOKEN_NAMES) + r")\]\]")
PHONE_FILLED = frozenset({"ITEM"})       # tokens the server never fills
MAX_GROWTH = 1.5
BUDGET_S = 2.5                           # decision 10: one call returns both strings within 2.5 s
REWORDED = ("email_body", "call_script")

Mode = Literal["bus", "rail", "streetcar", "unknown"]
Role = Literal["alight", "board"]
MODES: tuple[Mode, ...] = ("bus", "rail", "streetcar", "unknown")
ROLES: tuple[Role, ...] = ("alight", "board")
# Which optional facts each template family can say; the rest are left out of its templates. DATE, TIME and
# ITEM are always there. STOP is optional only for a session whose anchor has no place label.
_SAYS: dict[str, tuple[str, ...]] = {"bus": ("VEHICLE", "ROUTE", "DIRECTION", "STOP"),
                                    "rail": ("VEHICLE", "ROUTE", "DIRECTION", "STOP"),
                                    "streetcar": ("DIRECTION", "STOP"), "unknown": ("DIRECTION", "STOP")}


class RephraseHook(Protocol):
    """Plug point for the wording model. It gets the value-free templates, {"email_body": ..., "call_script": ...},
    and returns rewordings for either or both, or None. It must never see or return values; everything it returns
    is validated, and anything that fails, raises or comes back after the budget is replaced by the template."""

    def __call__(self, templates: Mapping[str, str]) -> Mapping[str, str] | None: ...


# ----------------------------------------------------------------------------- templates


@dataclass(frozen=True)
class Templates:
    key: str                 # "bus/VEHICLE+ROUTE+DIRECTION+STOP/alight": stable, also the wording cache key part
    subject: str
    email_body: str
    call_script: str

    def strings(self) -> dict[str, str]:
        return {"subject": self.subject, "email_body": self.email_body, "call_script": self.call_script}


def _vehicle_phrase(mode: str, have: frozenset[str]) -> str:
    if mode == "bus":
        if "VEHICLE" in have:
            return "bus [[VEHICLE]], route [[ROUTE]]" if "ROUTE" in have else "bus [[VEHICLE]]"
        return "a route [[ROUTE]] bus" if "ROUTE" in have else "a bus"
    if mode == "rail":
        return "a [[ROUTE]] Line train" if "ROUTE" in have else "a train"
    return "the streetcar" if mode == "streetcar" else "a bus or train"


def templates_for(mode: str, have: Iterable[str], role: str = "alight") -> Templates:
    """The template family for a report: `have` names the facts we hold values for (tokens without a value are
    never written into the template). Unknown facts for a mode are ignored."""
    mode = mode if mode in MODES else "unknown"
    role = role if role in ROLES else "alight"
    have = frozenset(t for t in have if t in _SAYS[mode])
    phrase = _vehicle_phrase(mode, have)
    heading = ", heading [[DIRECTION]]" if "DIRECTION" in have else ""
    loss = f"I left [[ITEM]] on {phrase}{heading}."
    if mode == "rail" and "VEHICLE" in have:
        loss += " MARTA's public feeds list it as train run [[VEHICLE]]."
    verb = "got off" if role == "alight" else "got on"
    where = f"I {verb} at [[STOP]] on [[DATE]], at [[TIME]]." if "STOP" in have \
        else f"I {verb} on [[DATE]], at [[TIME]]."
    subject = f"Lost item on {phrase}, [[DATE]]"          # a train's run number stays in the body
    body = (f"Hello,\n\nI'm writing to report a lost item. {loss} {where}\n\n"
            "I have also sent these details through the online lost item form. If it turns up, please let me know "
            "how to arrange a pickup. I can describe it in more detail and bring a photo ID.\n\nThank you.")
    call = (f"Hi, I'm calling to report a lost item. {loss} {where} I've also sent the online report. "
            "Has anything like that been turned in, and what should I do next?")
    key = f"{mode}/{'+'.join(t for t in _SAYS[mode] if t in have) or 'none'}/{role}"
    return Templates(key=key, subject=subject, email_body=body, call_script=call)


def all_templates() -> list[Templates]:
    """Every variant the server can produce: for the copy lint test and for warming the wording cache."""
    out = []
    for mode in MODES:
        says = _SAYS[mode]
        for n in range(len(says) + 1):
            for have in itertools.combinations(says, n):
                for role in ROLES:
                    out.append(templates_for(mode, have, role))
    return out


# ----------------------------------------------------------------------------- place names (no stop, station ...)

LINE_COLORS = ("red", "gold", "blue", "green")
# Single words that are parts of stop or route names but also plain English; a part made only of one of these
# is not treated as a place name. Whole stop, station and alias names are always checked.
_COMMON = frozenset({
    "ride", "street", "avenue", "road", "drive", "boulevard", "parkway", "highway", "place", "court", "lane", "way",
    "manor", "park", "center", "plaza", "square", "station", "terminal", "village", "line", "access", "rapid",
    "streetcar", "city", "hill", "hills", "woods", "creek", "point", "springs", "lake", "end", "north", "south",
    "east", "west", "ne", "nw", "se", "sw", "circle", "trail", "terrace", "loop", "bus", "train", "rail", "stop",
})


@dataclass(frozen=True)
class PlaceNames:
    """Every stop, station, alias, line color and route name in our tables, normalized like the resolver
    normalizes what riders type (normalize_place), so "Five Points", "five points station" and "FIVE POINTS"
    are one name."""
    phrases: frozenset[tuple[str, ...]]
    max_len: int

    @classmethod
    def of(cls, names: Iterable[str]) -> PlaceNames:
        phrases = set()
        for name in names:
            tokens = tuple(normalize_place(name).split())
            if tokens and len("".join(tokens)) >= 2:
                phrases.add(tokens)
        for color in LINE_COLORS:
            phrases.add((color,))
        return cls(frozenset(phrases), max((len(p) for p in phrases), default=1))

    @classmethod
    def from_index(cls, index: Any) -> PlaceNames:
        """Built once per PlaceIndex (about 12,000 names, tens of milliseconds), then cached."""
        cached = _NAMES_CACHE.get(index)
        if cached is None:
            cached = cls.of(_index_names(index))
            _NAMES_CACHE[index] = cached
        return cached

    def find(self, text: str) -> list[str]:
        tokens = normalize_place(text).split()
        hits = []
        for i in range(len(tokens)):
            for n in range(1, min(self.max_len, len(tokens) - i) + 1):
                if tuple(tokens[i:i + n]) in self.phrases:
                    hits.append(" ".join(tokens[i:i + n]))
        return hits


_NAMES_CACHE: "weakref.WeakKeyDictionary[Any, PlaceNames]" = weakref.WeakKeyDictionary()


def _parts(name: str, splitter: str) -> list[str]:
    out = []
    for part in re.split(splitter, name):
        norm = normalize_place(part).split()
        if not norm or all(t.isdigit() for t in norm):
            continue
        if len(norm) == 1 and (norm[0] in _COMMON or len(norm[0]) < 3):
            continue
        out.append(part)
    return out


def _index_names(index: Any) -> list[str]:
    names: list[str] = []
    for st in getattr(index, "stations", {}).values():
        names += [st.label, *_parts(st.label, r"-")]
    names += list(getattr(index, "station_map", {}))
    for s in getattr(index, "stops", {}).values():
        names += [s.label, *_parts(s.label, r"\s@\s|\s&\s|/")]
    for key, a in getattr(index, "aliases", {}).items():
        names += [key, a.get("label", ""), *_parts(a.get("label", ""), r"[()]|\bor\b")]
    for r in getattr(index, "stop_routes", []) or []:      # headsigns name places too ("MCDONOUGH BLVD")
        if r.get("headsign"):
            names.append(r["headsign"])
    feed = getattr(index, "feed", None)
    for r in getattr(feed, "routes", []) or []:
        long_name = r.get("route_long_name") or ""
        names += [long_name, *_parts(long_name, r"/")]
        short = r.get("route_short_name") or ""
        if short.isalpha() and len(short) >= 2:
            names.append(short)
    return [n for n in names if n]


# ----------------------------------------------------------------------------- the validator (section 8 item 4)

_NUMBER_WORDS = ("zero", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
                 "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty",
                 "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety", "hundred", "thousand",
                 "dozen", "half past", "quarter past", "quarter to")
_NUMBER = re.compile(r"\b(?:" + "|".join(_NUMBER_WORDS) + r")\b", re.IGNORECASE)
# "one" stays a plain word ("one of them", "has anyone turned one in") except as a clock time: "at one", "half one",
# "a quarter after one" ("one o'clock" is caught by _CLOCK).
_ONE_TIME = re.compile(r"\b(?:at|past|after|before|to|till|until|by|around|about|half|quarter)\s+one\b(?!\s+of\b)",
                       re.IGNORECASE)
# Ordinals count days, stops and rides: "the ninth", "my fifth trip". "first", "second", "last" and "next" only where
# they count one of those ("the second stop", "the next day"), since "first" and "a second" are plain words too.
_ORDINAL = re.compile(r"\b(?:third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth|\w+teenth|\w+tieth|"
                      r"hundredth|thousandth)\b|\b(?:first|second|last|next|previous|final)\s+(?:stops?|stations?|"
                      r"days?|nights?|rides?|trips?|times?|bus(?:es)?|trains?|cars?|transfers?|platforms?|of)\b"
                      r"(?!\s+all\b)", re.IGNORECASE)
_CLOCK = re.compile(r"\d{1,2}\s*[:.h]\s*\d{2}|\b(?:noon|midnight|o'clock|morning|afternoon|evening|tonight|"
                    r"overnight|today|tomorrow|yesterday|ago|midday|lunchtime|lunch|night|nights|nighttime|dawn|"
                    r"dusk|sunrise|sunset|daybreak)\b", re.IGNORECASE)
# How long or how far: "I waited an hour", "a few minutes", "for days". A lone "day" is left to _shifted, so a
# greeting ("Good day,") still passes.
_DURATION = re.compile(r"\b(?:hours?|hrs?|minutes?|mins?|seconds|weeks?|fortnights?|months?|years?|days)\b",
                       re.IGNORECASE)
# Words that move a fact when they sit next to its token: "the stop after [[STOP]]", "an hour before [[TIME]]",
# "the day after [[DATE]]", "[[TIME]] or a bit later", "a block past [[STOP]]".
_RELATIVE = frozenset({"before", "after", "earlier", "later", "next", "previous", "prior", "since", "until", "till",
                       "past", "beyond", "preceding", "ahead", "behind", "day", "block", "blocks"})
NEAR = 4                                 # words between a relative word and a token, at most, to count as a shift
_SENTENCE = re.compile(r"[.!?;\n]")
_WORD = re.compile(r"\[\[[A-Z]+\]\]|[A-Za-z']+")
# am/pm as written for a time ("PM", "pm", "p.m."), never the verb "am"
_AMPM = re.compile(r"(?<![A-Za-z])(?:[AaPp]\.\s?[Mm]\.?|AM|PM|Pm|pm)(?![A-Za-z])")
_DAYS_FULL = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "weekday", "weekdays",
              "weekend", "weekends")
_MONTHS_FULL = ("january", "february", "march", "april", "june", "july", "august", "september", "october",
                "november", "december")
_DATE_FULL = re.compile(r"\b(?:" + "|".join(_DAYS_FULL + _MONTHS_FULL) + r")\b", re.IGNORECASE)
# Abbreviations and "May" only as written for a date (capitalized), so "may", "sat" and "wed" stay plain words.
_DATE_ABBR = re.compile(r"\b(?:Mon|Tue|Tues|Wed|Thu|Thur|Thurs|Fri|Sat|Sun|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|"
                        r"Oct|Nov|Dec)\b\.?")
_MAY = re.compile(r"\bMay\b")
_EMAIL = re.compile(r"@|\b\w+\s*(?:\(at\)|\[at\])\s*\w+", re.IGNORECASE)
_URL = re.compile(r"https?://|www\.|\b[\w-]+\.(?:com|org|net|gov|edu|us|io|co|aspx|html?)\b|\bdot\s+(?:com|org|net|gov)\b"
                  r"|\b(?:tel|mailto|sms):", re.IGNORECASE)
_PHONE = re.compile(r"\(?\d{3}\)?[\s.-]*\d{3}[\s.-]*\d{4}")
_BRACKET = re.compile(r"\[|\]|\{\{|\}\}")


def _outside_tokens(text: str) -> str:
    return TOKEN.sub(" ", text)


def _shifted(candidate: str) -> list[str]:
    """Relative words within NEAR words of any token, in the same sentence. Each would move the fact the token
    carries ("I got off at the stop after [[STOP]] the day after [[DATE]]") while every other check passes, since
    the token itself is untouched. "After I got off at [[STOP]]" is five words away and passes."""
    hits = []
    for sentence in _SENTENCE.split(candidate):
        words = _WORD.findall(sentence)
        at = [i for i, w in enumerate(words) if TOKEN.fullmatch(w)]
        hits += [w for i, w in enumerate(words) if w.lower() in _RELATIVE and any(abs(i - j) <= NEAR for j in at)]
    return hits


def validate(candidate: str, template: str, names: PlaceNames) -> list[str]:
    """Why `candidate` may not replace `template`; an empty list means it may. Checked separately per string."""
    if not isinstance(candidate, str) or not candidate.strip():
        return ["empty"]
    problems: list[str] = []
    want, got = sorted(TOKEN.findall(template)), sorted(TOKEN.findall(candidate))
    if want != got:
        problems.append(f"tokens: expected {want}, got {got}")
    outside = _outside_tokens(candidate)
    if _BRACKET.search(outside):
        problems.append("a malformed or unknown token")
    if re.search(r"\d", outside):
        problems.append("a digit outside the tokens")
    if _PHONE.search(outside):
        problems.append("a phone number")
    if _NUMBER.search(outside):
        problems.append(f"a number word: {_NUMBER.search(outside).group(0)!r}")
    spelled = _ONE_TIME.search(outside) or _ORDINAL.search(outside)
    if spelled:
        problems.append(f"a spelled-out time or an ordinal: {spelled.group(0)!r}")
    if _CLOCK.search(outside) or _AMPM.search(outside):
        problems.append("a clock time or am/pm")
    if _DURATION.search(outside):
        problems.append(f"a duration: {_DURATION.search(outside).group(0)!r}")
    shifted = _shifted(candidate)
    if shifted:
        problems.append(f"a shift next to a token: {shifted}")
    if _DATE_FULL.search(outside) or _DATE_ABBR.search(outside) or _month_may(outside):
        problems.append("a weekday or month name")
    if _EMAIL.search(outside):
        problems.append("an email address")
    if _URL.search(outside):
        problems.append("a URL")
    hits = names.find(outside)
    if hits:
        problems.append(f"a place, line or route name: {sorted(set(hits))}")
    problems += [f"copy lint: {p}" for p in lint(outside)]
    if len(candidate) > MAX_GROWTH * len(template):
        problems.append(f"too long: {len(candidate)} > {MAX_GROWTH} x {len(template)}")
    return problems


def _month_may(text: str) -> bool:
    """"May" as a month: capitalized and not the first word of a sentence ("May I ask" is a verb)."""
    for m in _MAY.finditer(text):
        before = text[:m.start()].rstrip()
        if before and before[-1] not in ".!?":
            return True
    return False


# ----------------------------------------------------------------------------- substitution and rendering


def substitute(text: str, values: Mapping[str, str]) -> str:
    """Fill every token except [[ITEM]]. A token without a value is a bug in template choice, never a blank."""
    def fill(m: re.Match) -> str:
        name = m.group(1)
        if name in PHONE_FILLED:
            return m.group(0)
        value = values.get(name)
        if not value:
            raise ValueError(f"no value for [[{name}]]")
        if "[[" in value or "]]" in value:
            raise ValueError(f"[[{name}]] value looks like a token")
        return value
    return TOKEN.sub(fill, text)


@dataclass(frozen=True)
class RenderedWording:
    templates: Templates
    email: EmailWording
    call_script: Wording
    rejected: dict[str, list[str]]       # why a hook's rewording was not used, per string (for logs and tests)


_POOL: ThreadPoolExecutor | None = None


def _pool() -> ThreadPoolExecutor:
    global _POOL
    if _POOL is None:
        _POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="wording")
    return _POOL


def ask_hook(hook: RephraseHook, templates: Mapping[str, str], budget_s: float = BUDGET_S) -> dict[str, str]:
    """Run the hook under the budget. Errors, odd shapes and late answers all mean "no rewording"."""
    future = _pool().submit(hook, dict(templates))
    try:
        answer = future.result(timeout=budget_s)
    except FutureTimeout:
        future.cancel()
        return {}
    except Exception:  # noqa: BLE001  any failure of the hook falls back to the template
        return {}
    if not isinstance(answer, Mapping):
        return {}
    return {k: v for k, v in answer.items() if k in REWORDED and isinstance(v, str)}


def render(tokens: Mapping[str, str], mode: str, role: str = "alight", *, names: PlaceNames | None = None,
           hook: RephraseHook | None = None, budget_s: float = BUDGET_S) -> RenderedWording:
    """The report email (subject and body) and the call script for these token values.

    With no hook, or with no place names to check against (the index is still loading), the hook is not asked
    and both strings are the template, wording "template". The subject is never reworded."""
    t = templates_for(mode, (k for k, v in tokens.items() if v), role)
    asked = {"email_body": t.email_body, "call_script": t.call_script}
    answers = ask_hook(hook, asked, budget_s) if hook is not None and names is not None else {}
    chosen: dict[str, tuple[str, str]] = {}
    rejected: dict[str, list[str]] = {}
    for key, template in asked.items():
        candidate = answers.get(key)
        problems = validate(candidate, template, names) if candidate is not None and names is not None else None
        if candidate is not None and not problems:
            chosen[key] = (candidate, "gemini")
        else:
            chosen[key] = (template, "template")
            if problems:
                rejected[key] = problems
    body, body_from = chosen["email_body"]
    call, call_from = chosen["call_script"]
    return RenderedWording(
        templates=t,
        email=EmailWording(subject=substitute(t.subject, tokens), body=substitute(body, tokens), wording=body_from),
        call_script=Wording(text=substitute(call, tokens), wording=call_from),
        rejected=rejected)
