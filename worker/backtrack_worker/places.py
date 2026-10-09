"""Place-name normalization, shared by the static loader and the API's resolver.

"PIEDMONT AVE SE @ GILMER ST SE", "Piedmont Ave & Gilmer St" and "piedmont
avenue at gilmer street" should all compare equal, so both stop names and what
riders type go through normalize_place().
"""

from __future__ import annotations

import re

_EXPAND = {
    "st": "street", "ave": "avenue", "av": "avenue", "rd": "road", "dr": "drive",
    "blvd": "boulevard", "pkwy": "parkway", "hwy": "highway", "ln": "lane", "ct": "court",
    "pl": "place", "sq": "square", "ctr": "center", "cntr": "center", "ft": "fort",
    "mt": "mount", "pkwy.": "parkway", "mlk": "martin luther king",
}
_FILLER = {"the", "station", "stn", "stop", "marta", "at", "and", "near", "by"}


def normalize_place(text: str) -> str:
    s = text.lower().replace("@", " at ").replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    tokens = [_EXPAND.get(t, t) for t in s.split()]
    kept = [t for t in tokens if t not in _FILLER]
    return " ".join(kept or tokens)
