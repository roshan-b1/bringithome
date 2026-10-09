"""One connector per data source. Adding a city means adding a module here."""

from __future__ import annotations

from ..config import Settings
from .base import Connector
from .marta_bus import MartaBusConnector
from .marta_rail import MartaRailConnector


def build_connectors(settings: Settings, names: list[str]) -> list[Connector]:
    available = {
        "bus": lambda: MartaBusConnector(poll_s=settings.bus_poll_s, timeout_s=settings.http_timeout_s),
        "rail": lambda: MartaRailConnector(api_key=settings.marta_rail_api_key,
                                           poll_s=settings.rail_poll_s,
                                           timeout_s=settings.http_timeout_s),
    }
    if names == ["all"]:
        names = list(available)
    unknown = [n for n in names if n not in available]
    if unknown:
        raise SystemExit(f"unknown source(s): {', '.join(unknown)}; choose from {', '.join(available)} or all")
    return [available[n]() for n in names]


def connector_for_source_name(settings: Settings, source: str) -> Connector:
    """Map an archive/source name like 'marta-bus' back to its connector."""
    for c in build_connectors(settings, ["all"]):
        if c.name == source:
            return c
    raise SystemExit(f"no connector named {source}")
