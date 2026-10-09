"""Schema migrations: db/migrations/NNN_name.sql, applied in order, once each.

Each file is split into statements and run one at a time in autocommit, the
way ARCHITECTURE.md asks: some TimescaleDB calls (refresh_continuous_aggregate)
refuse to run inside a transaction, and one failing statement should say
exactly which one. Applied versions are recorded in schema_migrations, so
running `migrate` again only applies what is new. Every file after 001 is
additive.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "db" / "migrations"
_NAME = re.compile(r"^(\d{3})_[\w-]+\.sql$")

TRACKING = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    text PRIMARY KEY,
    name       text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


@dataclass(frozen=True)
class Migration:
    version: str
    name: str
    path: Path

    def statements(self) -> list[str]:
        return split_statements(self.path.read_text())


def discover(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    found = []
    for path in sorted(directory.glob("*.sql")):
        m = _NAME.match(path.name)
        if m:
            found.append(Migration(m.group(1), path.stem, path))
    versions = [m.version for m in found]
    if len(versions) != len(set(versions)):
        raise SystemExit(f"duplicate migration numbers in {directory}")
    return found


def split_statements(sql: str) -> list[str]:
    """Split SQL on top-level semicolons. Understands -- and /* */ comments,
    'single quoted' and "double quoted" text, and $tag$ dollar-quoted bodies,
    so DO blocks and function definitions stay whole."""
    out: list[str] = []
    buf: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        if sql.startswith("--", i):
            end = sql.find("\n", i)
            end = n if end == -1 else end
            buf.append(sql[i:end])
            i = end
        elif sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            end = n if end == -1 else end + 2
            buf.append(sql[i:end])
            i = end
        elif c in ("'", '"'):
            j = i + 1
            while j < n:
                if sql[j] == c:
                    if j + 1 < n and sql[j + 1] == c:  # doubled quote inside the string
                        j += 2
                        continue
                    break
                j += 1
            buf.append(sql[i:j + 1])
            i = j + 1
        elif c == "$":
            m = re.match(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$", sql[i:])
            if m:
                tag = m.group(0)
                end = sql.find(tag, i + len(tag))
                end = n if end == -1 else end + len(tag)
                buf.append(sql[i:end])
                i = end
            else:
                buf.append(c)
                i += 1
        elif c == ";":
            stmt = "".join(buf).strip()
            if _has_code(stmt):
                out.append(stmt)
            buf = []
            i += 1
        else:
            buf.append(c)
            i += 1
    stmt = "".join(buf).strip()
    if _has_code(stmt):
        out.append(stmt)
    return out


def _has_code(stmt: str) -> bool:
    without = re.sub(r"--[^\n]*", "", stmt)
    without = re.sub(r"/\*.*?\*/", "", without, flags=re.S)
    return bool(without.strip())


def pending(conn, migrations: list[Migration]) -> list[Migration]:
    conn.execute(TRACKING)
    done = {row[0] for row in conn.execute("SELECT version FROM schema_migrations").fetchall()}
    return [m for m in migrations if m.version not in done]


def apply(conn, migration: Migration, echo=print) -> None:
    """Run one migration statement by statement (connection must be autocommit)."""
    statements = migration.statements()
    for k, stmt in enumerate(statements, 1):
        first_line = next((ln for ln in stmt.splitlines() if ln.strip() and not ln.strip().startswith("--")), "")
        try:
            conn.execute(stmt)
        except Exception as e:  # noqa: BLE001  say exactly which statement failed, then stop
            raise SystemExit(f"{migration.name}: statement {k} of {len(statements)} failed "
                             f"({first_line.strip()[:80]}): {type(e).__name__}: {e}") from None
    conn.execute("INSERT INTO schema_migrations (version, name) VALUES (%s, %s)",
                 (migration.version, migration.name))
    echo(f"applied {migration.name} ({len(statements)} statements)")
