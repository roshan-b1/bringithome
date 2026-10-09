"""The copy lint: one check for every string a rider sees or hears (PREP-REPORT section 10, ARCHITECTURE.md
section 8 item 4 and decision 12).

Why a lint and not a style note: the product rules are about safety and trust. Telling a panicked rider to run,
hurry or hold the doors can get them hurt, the two-letter abbreviation for artificial intelligence is off-brand by
decision, and a promise that MARTA will hold a vehicle or contact a driver is one we can't keep. A lint makes those
rules mechanical, so the same function guards the server's templates, the wording validator (anything Gemini writes
goes through it) and, later, every copy.ts string.

What it flags:
  dash         em dash, en dash and their look-alikes, and "--"
  banned_word  run, hurry, rush, door, chase, jump, confront, follow, catch, therapy, treatment (with their
               inflections) and the abbreviation, matched as whole words; plus "cross the tracks"
  promise      "hold the bus", "contact the driver", "wait for you" and the like

What it allows (the allowlist from the prep report and decision 12):
  - negated safety lines: "Don't follow or confront anyone", "Never run for a bus", "No running", "Please don't
    follow anyone". A banned word is allowed when, walking back through its clause past only connectors ("or",
    "to", "the", other banned words ...), the next word is a negator ("don't", "not", "never", "no", any "n't").
  - "train run" / "train runs": the report's run-number line ("MARTA train run 405, from MARTA's public feeds").
  - "running on Vultr": the proof footer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Literal, Mapping

ProblemKind = Literal["dash", "banned_word", "promise"]

# U+2012 figure dash, U+2013 en dash, U+2014 em dash, U+2015 horizontal bar, U+2E3A/2E3B two- and three-em dash,
# U+FE31/FE32 vertical dashes, U+FE58 small em dash: every long dash a model or a word processor might emit.
_DASHES = "\u2012\u2013\u2014\u2015\u2e3a\u2e3b\ufe31\ufe32\ufe58"
_DASH = re.compile(f"[{_DASHES}]|--")

BANNED_WORDS: dict[str, tuple[str, ...]] = {
    "run": ("run", "runs", "running", "ran"),
    "hurry": ("hurry", "hurries", "hurried", "hurrying"),
    "rush": ("rush", "rushes", "rushed", "rushing"),
    "door": ("door", "doors"),
    "chase": ("chase", "chases", "chased", "chasing"),
    "jump": ("jump", "jumps", "jumped", "jumping"),
    "confront": ("confront", "confronts", "confronted", "confronting", "confrontation"),
    "follow": ("follow", "follows", "followed", "following"),
    "catch": ("catch", "catches", "catching", "caught"),
    "therapy": ("therapy", "therapies"),
    "treatment": ("treatment", "treatments"),
}
_WORD_FORMS = sorted({f for forms in BANNED_WORDS.values() for f in forms}, key=len, reverse=True)
_BANNED = re.compile(r"\b(?:" + "|".join(_WORD_FORMS) + r")\b", re.IGNORECASE)
# The abbreviation, with or without dots ("AI", "A.I.", "AI-powered"); never inside a word ("aisle", "Hawaii").
_ABBREVIATION = re.compile(r"(?<![A-Za-z0-9])A\.?I(?![A-Za-z0-9])\.?", re.IGNORECASE)
_CROSS_TRACKS = re.compile(r"\bcross(?:es|ed|ing)?\s+(?:over\s+)?(?:the\s+)?(?:train\s+)?tracks?\b", re.IGNORECASE)

_PROMISES = [
    re.compile(r"\bhold(?:s|ing)?\s+(?:the|this|that|your|a|their)\s+(?:bus|train|vehicle|car|streetcar)\b", re.I),
    re.compile(r"\b(?:contact|contacts|contacted|contacting|radio|radios|radioed|radioing|call|calls|calling|"
               r"notify|notifies|notified|notifying|alert|alerts|alerting)\s+(?:the|your|a|their)\s+"
               r"(?:bus\s+|train\s+)?(?:driver|operator)s?\b", re.I),
    re.compile(r"\bwait(?:s|ing)?\s+for\s+you\b", re.I),
]

# Phrases whose banned word is fine. Spans, so "train run" never excuses a "run" elsewhere in the string.
_ALLOW_PHRASES = [
    re.compile(r"\btrain\s+runs?\b", re.IGNORECASE),
    re.compile(r"\brunning\s+on\s+Vultr\b", re.IGNORECASE),
]

_NEGATORS = {"no", "not", "never", "nor", "cannot"}
# Words that may sit between a negator and the banned word without breaking the negation:
# "don't follow or confront", "don't try to run", "don't hold the doors", "never have to rush".
_CONNECTORS = {"or", "and", "to", "try", "the", "a", "any", "anyone", "ever", "hold", "have", "need", "go",
               "for", "after", "it", "them", "him", "her", "will", "can", "be", "able"}
# A comma ends the negation too: "Don't wait, run" is an instruction to run.
_CLAUSE_END = re.compile(r"[.!?;:,\n]")
_TOKEN = re.compile(r"[A-Za-z']+")


@dataclass(frozen=True)
class Problem:
    kind: ProblemKind
    text: str          # the offending fragment, as written
    at: int            # character offset in the linted string

    def __str__(self) -> str:
        return f"{self.kind}: {self.text!r} at {self.at}"


def _is_negator(word: str) -> bool:
    w = word.lower()
    return w in _NEGATORS or w.endswith("n't") or w == "dont"


def _negated(text: str, start: int) -> bool:
    """True when the word at `start` sits in a negated clause: walking back through its clause, past only
    connectors and other banned words, the next word is a negator."""
    clause_start = max((m.end() for m in _CLAUSE_END.finditer(text, 0, start)), default=0)
    words = _TOKEN.findall(text[clause_start:start])
    for word in reversed(words):
        w = word.lower().strip("'")
        if _is_negator(w):
            return True
        if w in _CONNECTORS or _BANNED.fullmatch(w):
            continue
        return False
    return False


def _allowed_spans(text: str) -> list[tuple[int, int]]:
    return [m.span() for rx in _ALLOW_PHRASES for m in rx.finditer(text)]


def _inside(span: tuple[int, int], spans: Iterable[tuple[int, int]]) -> bool:
    return any(a <= span[0] and span[1] <= b for a, b in spans)


def lint(text: str) -> list[Problem]:
    """Every rule `text` breaks, in order of position. An empty list means it's clean."""
    text = text.replace("\u2019", "'").replace("\u2018", "'")   # curly apostrophes: "don\u2019t" is "don't"
    problems = [Problem("dash", m.group(0), m.start()) for m in _DASH.finditer(text)]
    allowed = _allowed_spans(text)
    for m in _BANNED.finditer(text):
        if not _inside(m.span(), allowed) and not _negated(text, m.start()):
            problems.append(Problem("banned_word", m.group(0), m.start()))
    problems += [Problem("banned_word", m.group(0), m.start()) for m in _ABBREVIATION.finditer(text)]
    for m in _CROSS_TRACKS.finditer(text):
        if not _negated(text, m.start()):
            problems.append(Problem("banned_word", m.group(0), m.start()))
    for rx in _PROMISES:
        for m in rx.finditer(text):
            if not _negated(text, m.start()):
                problems.append(Problem("promise", m.group(0), m.start()))
    return sorted(problems, key=lambda p: p.at)


def is_clean(text: str) -> bool:
    return not lint(text)


def lint_all(strings: Mapping[str, str]) -> dict[str, list[Problem]]:
    """Lint a table of strings (copy id -> text). Only the ids with problems come back."""
    found = {key: lint(text) for key, text in strings.items()}
    return {key: problems for key, problems in found.items() if problems}
