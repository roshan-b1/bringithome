"""Keep secrets out of logs, status files and terminal output.

Every string that leaves the process through EventLog or RecorderStatus goes
through redact(). Secrets are registered once at startup (the rail API key,
the database password); known URL shapes are scrubbed even if unregistered.
"""

from __future__ import annotations

import re

_SECRETS: set[str] = set()
_API_KEY = re.compile(r"(apikey=)[^&\s'\"]+", re.IGNORECASE)
_URL_PASSWORD = re.compile(r"(postgres(?:ql)?://[^:/@\s]+:)[^@\s]+@", re.IGNORECASE)


def register_secret(value: str | None) -> None:
    if value and len(value) >= 4:
        _SECRETS.add(value)


def redact(text: str) -> str:
    for secret in _SECRETS:
        text = text.replace(secret, "***")
    text = _API_KEY.sub(r"\1***", text)
    return _URL_PASSWORD.sub(r"\1***@", text)


def describe(error: BaseException) -> str:
    """A safe one-line description of an exception."""
    return redact(f"{type(error).__name__}: {error}")
