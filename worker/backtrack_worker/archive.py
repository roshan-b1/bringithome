"""Raw payload archive: every new feed payload, gzipped, on local disk.

This is the source of truth. The database can always be rebuilt from it
(`backfill`), replay mode can be baked from it offline, and if Tiger is down
nothing is lost.

Layout: <data>/raw/<source>/<YYYY-MM-DD>/<HHMMSS>_<epoch_ms>.<ext>.gz  (UTC)
"""

from __future__ import annotations

import gzip
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

from .timeutil import UTC, epoch_ms


class RawArchive:
    def __init__(self, data_dir: Path):
        self.root = data_dir / "raw"

    def write(self, source: str, polled_at: datetime, payload: bytes, ext: str) -> Path:
        t = polled_at.astimezone(UTC)
        day = self.root / source / t.strftime("%Y-%m-%d")
        day.mkdir(parents=True, exist_ok=True)
        ms = epoch_ms(t)
        path = day / f"{t.strftime('%H%M%S')}_{ms}.{ext}.gz"
        while path.exists():  # never overwrite (a clock that stepped back could repeat a time)
            ms += 1
            path = day / f"{t.strftime('%H%M%S')}_{ms}.{ext}.gz"
        tmp = path.with_name(path.name + ".tmp")
        with gzip.open(tmp, "wb", compresslevel=6) as f:
            f.write(payload)
        os.replace(tmp, path)  # atomic: readers never see half a file
        return path

    @staticmethod
    def polled_at_of(path: Path) -> datetime:
        ms = int(path.name.split("_", 1)[1].split(".", 1)[0])
        return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=ms)

    @classmethod
    def _time_or_none(cls, path: Path) -> datetime | None:
        try:
            return cls.polled_at_of(path)
        except (IndexError, ValueError, OverflowError):
            return None  # not ours (e.g. macOS "._" files, "name 2.gz" copies)

    @staticmethod
    def read(path: Path) -> bytes:
        with gzip.open(path, "rb") as f:
            return f.read()

    def iter_files(self, source: str, since: datetime | None = None,
                   until: datetime | None = None, after: datetime | None = None) -> Iterator[Path]:
        """Archived payloads in time order. `since` is inclusive, `after` is
        exclusive (the sync bookmark), `until` is exclusive."""
        base = self.root / source
        if not base.is_dir():
            return
        lower = max((t for t in (since, after) if t is not None), default=None)
        first_day = lower.astimezone(UTC).strftime("%Y-%m-%d") if lower else ""
        last_day = until.astimezone(UTC).strftime("%Y-%m-%d") if until else "9999"
        for day in sorted(p for p in base.iterdir() if p.is_dir()):
            if not (first_day <= day.name <= last_day):
                continue
            timed = [(t, p) for p in day.glob("*.gz") if (t := self._time_or_none(p)) is not None]
            for t, path in sorted(timed):
                if since is not None and t < since:
                    continue
                if after is not None and t <= after:
                    continue
                if until is not None and t >= until:
                    continue
                yield path

    def stats(self, source: str) -> dict:
        base = self.root / source
        files = [p for p in base.rglob("*.gz") if self._time_or_none(p)] if base.is_dir() else []
        times = sorted(self.polled_at_of(p) for p in files)
        return {
            "files": len(files),
            "bytes": sum(p.stat().st_size for p in files),
            "first": times[0].isoformat() if times else None,
            "last": times[-1].isoformat() if times else None,
        }
