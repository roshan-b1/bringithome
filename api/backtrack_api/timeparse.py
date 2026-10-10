"""What a rider says about when -> a time, a half-width and a day label (feature 7, POST /api/parse).

Why rules and not the model: Gemini may only hand back the rider's own words ("around 1", "20 min ago"), never a
timestamp (decision 11 rejects Gemini timestamps, section 8 item 2). This module turns those words into a time against
the SERVER clock (`now`, which is `as_of` on a replay), on the New York wall clock (decision 6). It also finds such
phrases in free text for the rule-based fallback parser.

The table. Where a phrase means the same thing as a time button, it gets that button's window
(timewindow.BUTTONS), so a typed answer searches like the tapped one; vaguer phrases get wider windows.

| Phrase | Center | Half-width W (min) |
|---|---|---|
| "just now", "right now", "a moment ago", "a few (or a couple) minutes ago" | now - 5 | 5 (Under 10 min) |
| "N min ago" | now - N | by N: under 10: 5, 10 to 29: 10, 30 to 59: 15, 60 to 120: 30, over 120: 60 |
| "half an hour ago" | now - 30 | 15 (30 to 60 min) |
| "an hour ago", "an hour and a half ago", "2 hours ago" | now - 60, - 90, - 120 | 30 (1 to 2 h) |
| "N hours ago", N of 3 or more (up to 36 h) | now - 60 N | 60 |
| "a couple (of) hours ago" | now - 120 | 60 |
| "a few hours ago" | now - 240 | 120 (Over 2 hours) |
| "earlier", "earlier today", "a while ago" | now - 90 | 90 (Not sure) |
| a clock with a minute: "1:15", "3:40 pm" | that time | 10 |
| an hour: "at 3pm", "3 pm", "at 1", "6 o'clock", "noon", "midnight", "12 noon", "12 midnight" | that time | 15 |
| either with a hedge: "around 1", "about 1:15", "like 3pm", "1ish", "6 or so" | that time | 30 (20 with a minute) |
| "this morning" | the middle of 5 AM to noon, cut at now | half that span |
| "this afternoon" / "this evening" (also "earlier this afternoon") | noon to 5 PM / 5 PM to 9 PM, cut at now | half that span |
| "tonight", "last night" | 7 PM to 1 AM, cut at now | half that span |
| a day alone: "yesterday", "today", "Thursday" | the middle of 5 AM to 1 AM (the service day), cut at now | half that span, at most 360 |

Days and clocks:
- A clock without am/pm is the most recent past occurrence of either reading, never a future one: "around 1" said at
  1:10 PM is 1:00 PM, said at 12:20 PM it is 1:00 AM.
- An am/pm time that hasn't come yet today is yesterday's: a 24 h rollback, as decision 6 has for picked times.
- One exception, so a rider estimating "just now" is not sent 12 hours back: a clock that is still within its own
  window of now ("around 1" said at 12:50 PM, W 30; "at 3pm" said at 2:55 PM, W 15) reads as now, with that window.
  The center is never in the future. Not with a weekday or "yesterday", which name a day that is over.
- `near` (another anchor's time from the same clue) picks, among the past readings, the one closest to it, so "got on
  at 3pm, got off around 3:30" said at 1:30 PM gives yesterday 3:00 PM and yesterday 3:30 PM, not 3:30 AM today.
- With a day, the latest reading on that day that is past: "yesterday at 5" is 5 PM, "yesterday at 5 am" is 5 AM.
  "Thursday around 6" is the most recent Thursday whose 6 has passed (last week's when it is Thursday before 6 AM).
- A part of the day picks the reading: "this morning at 9" is AM, "last night at 12:30" is 12:30 AM after that evening.
  "this morning" or "tonight" said before that part of today begins means yesterday's.
- A wall time that never happened (the spring-forward hour) is skipped; a repeated one (the fall-back hour) takes the
  more recent past instant. Relative phrases ("20 min ago") are plain elapsed time, so they cross DST unchanged.
- The day label is the center's New York date: "today", "yesterday", else the weekday ("Thursday").
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from backtrack_worker.timeutil import ATL, UTC

from .timewindow import BUTTONS

MAX_WINDOW_MIN = 360                     # ParsedAnchor.window_min's ceiling
MAX_AGO_MIN = 36 * 60
SERVICE_DAY = (5, 25)                    # 5 AM to 1 AM the next day, in local hours
PART_SPANS: dict[str, tuple[int, int]] = {"morning": (5, 12), "afternoon": (12, 17), "evening": (17, 21),
                                          "night": (19, 25)}
PART_HOURS: dict[str, frozenset[int]] = {"morning": frozenset(range(0, 12)), "afternoon": frozenset(range(12, 18)),
                                         "evening": frozenset(range(16, 24)),
                                         "night": frozenset([*range(18, 24), 0, 1, 2, 3, 4])}
WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


@dataclass(frozen=True)
class TimeReading:
    at: datetime                         # UTC, the center of the window
    window_min: int                      # half-width W in minutes, 1..360
    day_label: str                       # "today" | "yesterday" | "Thursday"
    kind: str                            # "button" | "ago" | "clock" | "span"


# ----------------------------------------------------------------------------- the grammar

_NUMBER_WORDS: dict[str, float] = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "twenty five": 25, "thirty": 30,
    "forty": 40, "forty five": 45, "fifty": 50, "sixty": 60, "ninety": 90}
_VAGUE_WORDS: dict[str, str] = {"a couple of": "couple", "a couple": "couple", "couple of": "couple",
                                "couple": "couple", "a few": "few", "few": "few", "several": "few"}
_HOUR_WORDS = {w: int(n) for w, n in _NUMBER_WORDS.items() if w not in ("a", "an") and 1 <= n <= 12}
_NUM_ALT = "|".join(sorted(map(re.escape, [*_VAGUE_WORDS, *_NUMBER_WORDS]), key=len, reverse=True))
_AGO = re.compile(
    rf"(?:(?P<num>\d+(?:\.\d+)?|{_NUM_ALT})\s+(?P<half1>and a half\s+)?(?P<unit>minutes?|mins?|m|hours?|hrs?|hr|h)"
    rf"(?P<half2>\s+and a half)?|(?P<halfhour>half an hour|a half hour|half hour|half an hr))\s+ago")
# A range, "5-10 minutes ago", "5 to 10 min ago", "2 or 3 hours ago": its middle, wide enough to cover both ends.
_AGO_RANGE = re.compile(
    rf"(?P<a>\d+(?:\.\d+)?|{_NUM_ALT})\s*(?:-|to|or)\s*(?P<b>\d+(?:\.\d+)?|{_NUM_ALT})\s+"
    rf"(?P<unit>minutes?|mins?|m|hours?|hrs?|hr|h)\s+ago")
# Not a bare "now": in a clue it is mostly filler ("and now I can't find it", "now what").
_NOW = re.compile(r"just now|right now|a moment ago|moments ago|a sec(?:ond)? ago|seconds ago|"
                  r"a few seconds ago|just a (?:minute|moment|sec|second) ago")
_EARLIER = re.compile(r"earlier|earlier today|a while ago|a little while ago|a bit ago|not long ago")
_HEDGE = re.compile(r"\b(?:around|about|like|roughly|approximately|approx|abt|maybe|probably|sometime|somewhere|"
                    r"close to|nearly|almost|over|ish|or so|give or take)\b")
_HEDGE_WORDS = frozenset({"around", "about", "like", "roughly", "approximately", "approx", "abt", "maybe",
                          "probably", "sometime", "somewhere", "nearly", "almost", "after", "before", "past",
                          "til", "till", "until"})
_FILLER = frozenset({"at", "by", "on", "in", "the", "of", "last", "this", "past"})
_PARTS = {"morning": "morning", "afternoon": "afternoon", "evening": "evening", "night": "night"}
_WEEKDAYS = {name.lower(): i for i, name in enumerate(WEEKDAY_NAMES)} | {"tues": 1, "weds": 2, "thurs": 3}
_CLOCK = re.compile(r"(\d{1,2}):(\d{2})")


def _prep(phrase: str) -> str:
    """Lowercase words and clocks: "1-ish" -> "1 ish", "3p.m." -> "3 pm", "6 o'clock" -> "6 oclock"."""
    s = unicodedata.normalize("NFKC", phrase).lower().replace("\u2019", "'")
    s = re.sub(r"(?<![a-z])([ap])\.\s?m\b\.?", r"\1m", s)
    s = re.sub(r"\bo'?\s?clock\b", " oclock", s)
    s = re.sub(r"(\d|noon|oclock)\s*-?\s*ish\b", r"\1 ish", s)
    s = re.sub(r"(\d)\s*([ap])m\b|(\d)([ap])\b", lambda m: f"{m[1] or m[3]} {m[2] or m[4]}m", s)
    s = re.sub(r"(\d)(minutes?|mins?|m|hours?|hrs?|hr|h)\b", r"\1 \2", s)
    s = s.replace("~", " around ").replace("'", "")
    s = re.sub(r"(\d)\s*[-\u2013]\s*(\d)", r"\1 to \2", s)    # "5-10 minutes ago" -> "5 to 10 minutes ago"
    s = re.sub(r"[^a-z0-9:. ]+|(?<!\d)\.|\.(?!\d)", " ", s)      # keeps "1.5" (hours), drops other dots
    return " ".join(s.split())


@dataclass(frozen=True)
class _Spec:
    """What a phrase says, before it meets a clock."""
    kind: str                            # button | ago | clock | span
    button: str | None = None
    minutes_ago: float = 0.0
    window: int = 0
    hour: int | None = None              # as said: 1..12, or 0..23 with a colon
    minute: int = 0
    ampm: str | None = None              # am | pm
    minute_given: bool = False
    special: bool = False                # noon or midnight
    colon: bool = False
    oclock: bool = False
    day: int | None = None               # days back: 0 today, 1 yesterday
    weekday: int | None = None
    part: str | None = None
    hedged: bool = False
    cued: bool = False                   # a bare hour came after a hedge, "at", a day or a part (or before "ish")

    @property
    def bare(self) -> bool:
        return self.hour is not None and not (self.ampm or self.colon or self.oclock or self.special)


def _ago_window(minutes: float) -> int:
    if minutes < 10:
        return 5
    if minutes < 30:
        return 10
    if minutes < 60:
        return 15
    if minutes <= 120:
        return 30
    return 60


def _read_ago(core: str) -> _Spec | None:
    if _NOW.fullmatch(core):
        return _Spec("button", button="under_10")
    if _EARLIER.fullmatch(core):
        return _Spec("button", button="not_sure")
    r = _AGO_RANGE.fullmatch(core)
    if r:
        per = 60 if r["unit"].startswith("h") else 1
        ends = [float(x) if x[0].isdigit() else _NUMBER_WORDS.get(x) for x in (r["a"], r["b"])]
        if None in ends:
            return None
        lo, hi = sorted(e * per for e in ends)
        if lo < 0 or hi <= 0 or hi > MAX_AGO_MIN:
            return None
        mid = (lo + hi) / 2
        return _Spec("ago", minutes_ago=mid, window=max(_ago_window(mid), math.ceil((hi - lo) / 2)))
    m = _AGO.fullmatch(core)
    if not m:
        return None
    if m["halfhour"]:
        return _Spec("ago", minutes_ago=30, window=_ago_window(30))
    num, unit = m["num"], m["unit"]
    per = 60 if unit.startswith("h") else 1
    vague = _VAGUE_WORDS.get(num)
    if vague == "few" and per == 60:
        return _Spec("button", button="over_2h")
    if vague is not None and per == 1:
        return _Spec("button", button="under_10")
    if vague == "couple":
        return _Spec("ago", minutes_ago=120, window=60)
    n = float(num) if num[0].isdigit() else _NUMBER_WORDS[num]
    n += 0.5 if (m["half1"] or m["half2"]) else 0
    minutes = n * per
    if minutes <= 0 or minutes > MAX_AGO_MIN:
        return None
    return _Spec("ago", minutes_ago=minutes, window=_ago_window(minutes))


def _read(phrase: str) -> _Spec | None:
    """The grammar: a phrase is a time only if every word in it is understood."""
    s = _prep(phrase)
    if not s:
        return None
    core = " ".join(_HEDGE.sub(" ", s).split())
    if core and (spec := _read_ago(core)) is not None:
        return spec
    return _read_parts(s.split())


def _read_parts(toks: list[str]) -> _Spec | None:
    f: dict = {"hedged": False, "cued": False}
    cue_seen = False
    i = 0
    while i < len(toks):
        t, nxt = toks[i], (toks[i + 1] if i + 1 < len(toks) else None)
        if t in _HEDGE_WORDS:
            f["hedged"] = cue_seen = True
        elif t == "ish" or (t == "or" and nxt == "so"):
            if "hour" not in f:
                return None
            f["hedged"] = f["cued"] = True
            i += t == "or"
        elif t == "close" and nxt == "to":
            f["hedged"] = cue_seen = True
            i += 1
        elif t == "give" and toks[i + 1:i + 3] == ["or", "take"]:
            f["hedged"] = f["cued"] = True
            i += 2
        elif t == "last" and nxt == "night":
            f["day"], f["part"] = 1, "night"
            cue_seen = True
            i += 1
        elif t in ("yesterday", "yesterdays", "yday"):
            f["day"] = 1
            cue_seen = True
        elif t == "today":
            f["day"] = 0
            cue_seen = True
        elif t == "tonight":
            f["part"] = "night"
            cue_seen = True
        elif t in _PARTS:
            f["part"] = _PARTS[t]
            cue_seen = True
        elif t in _WEEKDAYS:
            f["weekday"] = _WEEKDAYS[t]
            cue_seen = True
        elif t in ("noon", "midday", "midnight"):
            # "12 noon", "around 12 midnight", "12:00 noon": a 12 right before it says the same thing ("bus 12 at
            # noon" does not), and any other hour contradicts it
            if "hour" in f and (f["hour"] != 12 or f.get("minute", 0) or f.get("ampm") or f.get("oclock")
                                or toks[i - 1] not in ("12", "twelve", "12:00")):
                return None
            f.update(hour=0 if t == "midnight" else 12, minute=0, ampm="am" if t == "midnight" else "pm",
                     special=True)
        elif t == "earlier" and nxt is not None:
            cue_seen = True                  # "earlier this afternoon"; "earlier" alone is _EARLIER's
        elif t in ("half", "quarter") and nxt in ("past", "after", "to") and i + 2 < len(toks):
            hour = _hour_token(toks[i + 2])
            if hour is None or "hour" in f:
                return None
            minute = 30 if t == "half" else (45 if nxt == "to" else 15)
            f.update(hour=(hour - 1 or 12) if nxt == "to" else hour, minute=minute, minute_given=True, colon=True,
                     cued=True)
            i += 2
        elif (m := _CLOCK.fullmatch(t)) is not None:
            if "hour" in f:
                return None
            f.update(hour=int(m[1]), minute=int(m[2]), minute_given=True, colon=True)
        elif (hour := _hour_token(t)) is not None:
            if "hour" in f:
                return None
            f.update(hour=hour, cued=f["cued"] or cue_seen)
        elif t in ("am", "pm"):
            if "hour" not in f or f.get("ampm"):
                return None
            f["ampm"] = t
        elif t == "oclock":
            if "hour" not in f or f.get("colon"):
                return None
            f["oclock"] = True
        elif t in _FILLER:
            if t in ("at", "by"):
                cue_seen = True
        else:
            return None
        i += 1
    if toks[-1] in _FILLER:
        return None
    if not any(k in f for k in ("hour", "day", "weekday", "part")):
        return None
    if "hour" in f and not _clock_ok(f):
        return None
    if "hour" not in f and f.get("hedged") and not (f.get("part") or "day" in f or "weekday" in f):
        return None
    return _Spec("clock" if "hour" in f else "span", **f)


def _hour_token(t: str) -> int | None:
    if t.isdigit() and len(t) <= 2:
        return int(t)
    return _HOUR_WORDS.get(t)


def _clock_ok(f: dict) -> bool:
    hour, minute = f["hour"], f.get("minute", 0)
    if minute > 59:
        return False
    if f.get("special"):
        return True
    if f.get("ampm"):
        return 1 <= hour <= 12
    if f.get("colon"):
        return 0 <= hour <= 23
    return 1 <= hour <= 12


# ----------------------------------------------------------------------------- meeting the clock


def is_time_phrase(phrase: str) -> bool:
    """Whether the grammar reads the phrase as a time at all, before it meets a clock."""
    return bool(phrase and phrase.strip()) and _read(phrase) is not None


def is_cued_time_phrase(phrase: str) -> bool:
    """Whether the phrase is a time on its own words, as find_time_phrases asks: a bare hour needs a cue ("at 5",
    "around 5", "5ish"), so "5" or "10" alone, which may be a route, is not."""
    spec = _read(phrase) if phrase and phrase.strip() else None
    return spec is not None and not (spec.bare and not spec.cued)


def read_time(phrase: str, now: datetime, *, near: datetime | None = None) -> TimeReading | None:
    """A time phrase -> its reading against `now` (aware), or None when it isn't a time we understand or would be
    in the future. `near` only steers which past reading of a clock is meant (see the module docstring)."""
    if not phrase or not phrase.strip():
        return None
    spec = _read(phrase)
    return _resolve(spec, now, near) if spec is not None else None


def _resolve(spec: _Spec, now: datetime, near: datetime | None = None) -> TimeReading | None:
    if spec.kind == "button":
        ago, half = BUTTONS[spec.button]
        return _reading(now - timedelta(minutes=ago), half, now, "button")
    if spec.kind == "ago":
        return _reading(now - timedelta(minutes=spec.minutes_ago), spec.window, now, "ago")
    if spec.kind == "clock":
        return _clock(spec, now, near)
    return _span(spec, now)


def _reading(at: datetime, window: int, now: datetime, kind: str) -> TimeReading:
    window = max(1, min(MAX_WINDOW_MIN, int(window)))
    return TimeReading(at=at.astimezone(UTC), window_min=window, day_label=day_label(at, now), kind=kind)


def day_label(at: datetime, now: datetime) -> str:
    """"today", "yesterday" or the weekday name, by New York dates."""
    d, today = at.astimezone(ATL).date(), now.astimezone(ATL).date()
    if d == today:
        return "today"
    if d == today - timedelta(days=1):
        return "yesterday"
    return WEEKDAY_NAMES[d.weekday()]


def instants(d: date, hour: int, minute: int = 0) -> list[datetime]:
    """Every UTC instant at which the New York wall clock read d hour:minute (hour may run past 24 into the next
    day). Two in the fall-back hour, none in the spring-forward hour."""
    if hour >= 24:
        d, hour = d + timedelta(days=1), hour - 24
    naive = datetime.combine(d, time(hour, minute))
    out: list[datetime] = []
    for fold in (0, 1):
        u = naive.replace(tzinfo=ATL, fold=fold).astimezone(UTC)
        if u.astimezone(ATL).replace(tzinfo=None) == naive and u not in out:
            out.append(u)
    return out


def _readings(spec: _Spec) -> list[int]:
    """The 24 h hours a clock could mean."""
    h = spec.hour
    if spec.special or (spec.colon and (h == 0 or h >= 13)):
        return [h]
    if spec.ampm:
        return [h % 12 + (12 if spec.ampm == "pm" else 0)]
    return [h % 12, h % 12 + 12]


def _last_weekday(today: date, weekday: int) -> date:
    return today - timedelta(days=(today.weekday() - weekday) % 7)


def _clock(spec: _Spec, now: datetime, near: datetime | None = None) -> TimeReading | None:
    hours = _readings(spec)
    if spec.part:
        hours = [h for h in hours if h in PART_HOURS[spec.part]]
        if not hours:
            return None
    if spec.hedged:
        window = 20 if spec.minute_given else 30
    else:
        window = 10 if spec.minute_given else 15
    today = now.astimezone(ATL).date()
    if spec.day is not None:
        tries = [[today - timedelta(days=spec.day)]]
    elif spec.weekday is not None:
        d = _last_weekday(today, spec.weekday)
        tries = [[d], [d - timedelta(days=7)]]
    else:
        tries = [[today, today - timedelta(days=1)]]
    night_next = spec.part == "night"

    def readings_on(d: date) -> list[datetime]:
        return [u for h in hours for u in instants(d + timedelta(days=1 if night_next and h < 5 else 0), h,
                                                    spec.minute)]

    # still within its own window of now: the rider means just now (never a future center)
    soon_days = ([today] if spec.day == 0 else
                 [today + timedelta(days=k) for k in (-1, 0, 1)] if spec.day is None else [])
    due = spec.weekday is None and any(now < u <= now + timedelta(minutes=window)
                                       for d in soon_days for u in readings_on(d))
    for dates in tries:
        found = [u for d in dates for u in readings_on(d) if u <= now] + ([now] if due else [])
        if found:
            pick = max(found) if near is None else min(found, key=lambda u: (abs(u - near), -u.timestamp()))
            return _reading(pick, window, now, "clock")
    return None


def _span(spec: _Spec, now: datetime) -> TimeReading | None:
    start_h, end_h = PART_SPANS[spec.part] if spec.part else SERVICE_DAY
    today = now.astimezone(ATL).date()
    if spec.day is not None:
        d = today - timedelta(days=spec.day)
    elif spec.weekday is not None:
        d = _last_weekday(today, spec.weekday)
    else:
        d = today
    start, end = min(instants(d, start_h)), max(instants(d, end_h))
    if start > now:
        if spec.day is not None:
            return None                  # "today" before 5 AM: that part of today hasn't begun
        d -= timedelta(days=7 if spec.weekday is not None else 1)
        start, end = min(instants(d, start_h)), max(instants(d, end_h))
    end = min(end, now)
    if end <= start:
        return None
    span_min = (end - start).total_seconds() / 60
    return _reading(start + (end - start) / 2, max(5, math.ceil(span_min / 2)), now, "span")


# ----------------------------------------------------------------------------- finding time phrases in free text

# A bare hour ("at 5") right before one of these is a place or a count, not a time: "at 5 points", "at one of".
_NOT_AFTER_HOUR = frozenset({
    "points", "point", "street", "st", "avenue", "ave", "road", "rd", "drive", "dr", "boulevard", "blvd", "way",
    "lane", "ln", "court", "ct", "place", "pl", "parkway", "pkwy", "highway", "hwy", "circle", "cir", "terrace",
    "trail", "square", "sq", "plaza", "center", "ctr", "station", "stn", "stop", "stops", "mall", "park", "of",
    "bus", "buses", "train", "trains", "car", "cars", "line", "route", "people", "guys", "min", "mins", "minute",
    "minutes", "hour", "hours", "hr", "hrs", "block", "blocks", "times", "th", "nd", "rd", "seconds", "sec", "secs"})
_CLAUSE = re.compile(r"[^,;!?()\[\]\"\n]+")


def _counts_or_places(word: str) -> bool:
    """A word after a bare hour that makes it a count or a place, plurals included: "after 2 stations"."""
    return word in _NOT_AFTER_HOUR or (len(word) > 3 and word.endswith("s") and word[:-1] in _NOT_AFTER_HOUR)
_SENTENCE_END = re.compile(r"\.(?=\s|$)")
_MAX_WORDS = 8


def find_time_phrases(text: str) -> list[tuple[int, int]]:
    """(start, end) spans of time phrases in `text`, in order and never overlapping: at each word the longest run
    the grammar reads wins. A bare hour must follow a cue ("at 5", "around one", "Thursday 6") or carry "ish" /
    "o'clock", and must not sit before a place word, so "the 49", "route 1" and "at 5 points" are not times."""
    spans: list[tuple[int, int]] = []
    for clause in _CLAUSE.finditer(text):
        pieces = _SENTENCE_END.split(clause.group())
        offset = clause.start()
        for piece in pieces:
            words = list(re.finditer(r"\S+", piece))
            i = 0
            while i < len(words):
                hit = None
                for j in range(min(len(words), i + _MAX_WORDS), i, -1):
                    phrase = piece[words[i].start():words[j - 1].end()]
                    spec = _read(phrase)
                    if spec is None:
                        continue
                    following = _prep(words[j].group()).split()[:1] if j < len(words) else []
                    if spec.bare and (not spec.cued or (following and (_counts_or_places(following[0])
                                                                      or following[0][0].isdigit()))):
                        continue
                    hit = (offset + words[i].start(), offset + words[j - 1].end(), j)
                    break
                if hit is None:
                    i += 1
                else:
                    spans.append(hit[:2])
                    i = hit[2]
            offset += len(piece) + 1
    return spans
