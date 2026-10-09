"""MARTA rail stations: the rail API's station names mapped to static GTFS parent stops.

Parent stop ids, names and coordinates come from MARTA's static GTFS
(stops.txt, location_type=1, feed effective 2026-08-22). The rail API spells a
few stations differently; those are the explicit aliases below. Unknown names
map to None and get logged by the connector, never crash it.
"""

from __future__ import annotations

import re

# gtfs parent stop_id -> (GTFS stop_name, lat, lon)
RAIL_STATIONS: dict[str, tuple[str, float, float]] = {
    "510001": ("ARTS CENTER STATION", 33.78918, -84.38728),
    "510002": ("ASHBY STATION", 33.75645, -84.41728),
    "510003": ("AVONDALE STATION", 33.77535, -84.28191),
    "510004": ("BANKHEAD STATION", 33.77231, -84.4289),
    "510005": ("BROOKHAVEN-OGLETHORPE STATION", 33.85999, -84.3394),
    "510006": ("CHAMBLEE STATION", 33.88786, -84.30566),
    "510007": ("CIVIC CENTER STATION", 33.76634, -84.38749),
    "510008": ("COLLEGE PARK STATION", 33.65159, -84.44878),
    "510009": ("DECATUR STATION", 33.77468, -84.29553),
    "510010": ("DORAVILLE STATION", 33.90296, -84.28026),
    "510011": ("DUNWOODY STATION", 33.92124, -84.34427),
    "510012": ("EAST LAKE STATION", 33.76518, -84.31315),
    "510013": ("EAST POINT STATION", 33.67753, -84.4405),
    "510014": ("EDGEWOOD-CANDLER PARK STATION", 33.76193, -84.33962),
    "510015": ("FIVE POINTS STATION", 33.75387, -84.39157),
    "510016": ("GEORGIA STATE STATION", 33.75007, -84.38587),
    "510017": ("HAMILTON E HOLMES STATION", 33.75448, -84.47003),
    "510018": ("INDIAN CREEK STATION", 33.76982, -84.22953),
    "510019": ("INMAN PARK-REYNOLDSTOWN STATION", 33.75753, -84.35265),
    "510020": ("KENSINGTON STATION", 33.77262, -84.25192),
    "510021": ("KING MEMORIAL STATION", 33.7499, -84.3758),
    "510022": ("LAKEWOOD-FT MCPHERSON STATION", 33.70042, -84.42883),
    "510023": ("LENOX STATION", 33.8454, -84.35811),
    "510024": ("LINDBERGH CENTER STATION", 33.82291, -84.36947),
    "510025": ("MEDICAL CENTER STATION", 33.91072, -84.35145),
    "510026": ("MIDTOWN STATION", 33.78107, -84.38632),
    "510027": ("NORTH AVE STATION", 33.77179, -84.38709),
    "510028": ("NORTH SPRINGS STATION", 33.94498, -84.3572),
    "510029": ("OAKLAND CITY STATION", 33.71716, -84.42507),
    "510030": ("SANDY SPRINGS STATION", 33.93174, -84.35096),
    "510032": ("VINE CITY STATION", 33.75657, -84.404),
    "510033": ("WEST END STATION", 33.73612, -84.41364),
    "510034": ("WEST LAKE STATION", 33.75327, -84.4453),
    "510036": ("BUCKHEAD STATION", 33.84773, -84.36766),
    "510037": ("AIRPORT STATION", 33.640806, -84.446214),
    "510038": ("GARNETT STATION", 33.748544, -84.395888),
    "510039": ("SEC DISTRICT STATION", 33.75618, -84.39725),
    "510040": ("PEACHTREE CENTER STATION", 33.759684, -84.387598),
}

# Rail API spelling -> GTFS parent stop_id, where normalized names differ.
API_ALIASES: dict[str, str] = {
    "OMNI DOME STATION": "510039",        # renamed SEC District
    "LINDBERGH STATION": "510024",
    "INMAN PARK STATION": "510019",
    "LAKEWOOD STATION": "510022",
    "BROOKHAVEN STATION": "510005",
    "NORTH AVENUE STATION": "510027",
    "H E HOLMES STATION": "510017",
    "CANDLER PARK STATION": "510014",     # GTFS Green headsign ("... TO CANDLER PARK STATION")
    "EDGEWOOD CANDLER PARK STATION": "510014",   # what the API's STATION field actually sends
}

# DESTINATION spellings seen from the live API that a title-cased GTFS name doesn't produce.
KNOWN_DESTINATIONS: dict[str, str] = {
    "Hamilton E. Holmes": "510017",
}


def norm_api_name(name: str) -> str:
    """The rail API's habit: hyphens and periods become spaces ('EDGEWOOD CANDLER PARK STATION')."""
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]", " ", name.upper())).strip()


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]", " ", name.upper())).strip()


_BY_NORM = {_norm(name): sid for sid, (name, _, _) in RAIL_STATIONS.items()}
_BY_NORM.update({_norm(k): v for k, v in API_ALIASES.items()})


def station_id_for(api_name: str | None) -> str | None:
    if not api_name:
        return None
    key = _norm(api_name)
    if key in _BY_NORM:
        return _BY_NORM[key]
    if not key.endswith(" STATION"):
        return _BY_NORM.get(key + " STATION")
    return None
