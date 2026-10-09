"""Settings from the environment, with a tiny .env loader (no extra dependency)."""

from __future__ import annotations

import os
import re
import socket
import sys
from dataclasses import dataclass
from pathlib import Path

from .redact import register_secret

REPO_ROOT = Path(__file__).resolve().parents[2]


def _parse_value(raw: str) -> str:
    raw = raw.strip()
    if raw[:1] in {'"', "'"}:
        end = raw.find(raw[0], 1)
        return raw[1:end] if end != -1 else raw[1:]
    # unquoted: a " #" starts a comment, like most dotenv parsers
    for marker in (" #", "\t#"):
        if marker in raw:
            raw = raw.split(marker, 1)[0]
    return raw.strip()


def load_dotenv(path: Path) -> None:
    """KEY=VALUE lines, '#' comments. Real environment variables always win.

    An unreadable file is reported (by path, never contents) and skipped, so a
    permissions mistake on a server degrades instead of crash-looping.
    """
    try:
        if not path.is_file():
            return
        text = path.read_text()
    except OSError as e:
        print(f"warning: cannot read env file {path}: {type(e).__name__}", file=sys.stderr)
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip().startswith("export "):
            key = key.strip()[len("export "):]
        os.environ.setdefault(key.strip(), _parse_value(value))


def _data_dir() -> Path:
    raw = os.environ.get("BACKTRACK_DATA_DIR")
    path = Path(raw).expanduser() if raw else REPO_ROOT / "data"
    if not path.is_absolute():  # relative to the repo, never to wherever we were started
        path = REPO_ROOT / path
    return path.resolve()


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    database_url: str | None
    marta_rail_api_key: str | None
    bus_poll_s: float
    rail_poll_s: float
    http_timeout_s: float
    recorder_id: str = "local"

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv(Path(os.environ.get("BACKTRACK_ENV_FILE", REPO_ROOT / ".env")))
        key = (os.environ.get("MARTA_RAIL_API_KEY") or "").strip() or None
        db_url = (os.environ.get("DATABASE_URL") or "").strip() or None
        register_secret(key)
        _register_db_password(db_url)
        if key and re.search(r"\s", key):
            print("warning: MARTA_RAIL_API_KEY contains whitespace (an inline comment?); "
                  "the keyed rail URL will reject it", file=sys.stderr)
        return cls(
            data_dir=_data_dir(),
            database_url=db_url,
            marta_rail_api_key=key,
            bus_poll_s=_seconds("BUS_POLL_SECONDS", 15),
            rail_poll_s=_seconds("RAIL_POLL_SECONDS", 20),
            http_timeout_s=_seconds("HTTP_TIMEOUT_SECONDS", 15),
            recorder_id=(os.environ.get("RECORDER_ID") or socket.gethostname() or "local").strip()[:64],
        )


def _seconds(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return float(default)
    try:
        value = float(raw)
    except ValueError:
        raise SystemExit(f"{name} must be a number of seconds") from None
    if not 1 <= value <= 3600:
        raise SystemExit(f"{name} must be between 1 and 3600 seconds")
    return value


def _register_db_password(url: str | None) -> None:
    """Register every form of the password we might see in an error."""
    if not url or "://" not in url:
        return
    rest = url.split("://", 1)[1]
    for userinfo in {rest.rsplit("@", 1)[0], rest.split("@", 1)[0]}:
        if ":" in userinfo:
            register_secret(userinfo.split(":", 1)[1])
    try:
        from psycopg.conninfo import conninfo_to_dict

        register_secret(conninfo_to_dict(url).get("password"))
    except Exception:  # noqa: BLE001  malformed URLs are rejected later, without echoing them
        pass
