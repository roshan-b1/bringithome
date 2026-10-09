"""The rider's time answer -> a center time and a half-width W (ARCHITECTURE.md decision 6).

Relative buttons resolve against SERVER time (`now` is always passed in), never the phone clock.
"""

from __future__ import annotations

from datetime import datetime, timedelta

# button -> (minutes ago at the center, half-width in minutes)
BUTTONS: dict[str, tuple[int, int]] = {
    "under_10": (5, 5),
    "10_30": (20, 10),
    "30_60": (45, 15),
    "1_2h": (90, 30),
    "over_2h": (240, 120),
    "not_sure": (90, 90),
}
SURENESS_MIN: dict[str, int] = {"very": 5, "pretty": 15, "rough": 30, "not_sure": 60}


def window(now: datetime, *, button: str | None = None, picked: datetime | None = None,
           sureness: str = "pretty") -> tuple[datetime, int]:
    """(t_center, W in minutes). W = max(the button's half-width, the sureness)."""
    if sureness not in SURENESS_MIN:
        raise ValueError(f"unknown sureness {sureness!r}")
    if picked is not None:
        center, half = picked, 0
    elif button in BUTTONS:
        ago, half = BUTTONS[button]
        center = now - timedelta(minutes=ago)
    else:
        raise ValueError(f"unknown time button {button!r}")
    return center, max(half, SURENESS_MIN[sureness])


def search_window(t_center: datetime, w_min: int) -> tuple[datetime, datetime]:
    """The SQL window: t_center +/- (W + 3 min)."""
    pad = timedelta(minutes=w_min + 3)
    return t_center - pad, t_center + pad
