import os
from pathlib import Path

import pytest

from backtrack_worker import config
from backtrack_worker.config import REPO_ROOT, Settings, load_dotenv
from backtrack_worker.events import EventLog, RecorderStatus
from backtrack_worker.redact import describe, redact, register_secret


@pytest.fixture
def clean_env(monkeypatch):
    for k in ("MARTA_RAIL_API_KEY", "DATABASE_URL", "BACKTRACK_DATA_DIR", "X1", "X2", "X3", "X4"):
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def test_dotenv_strips_inline_comments_and_quotes(tmp_path, clean_env):
    env = tmp_path / ".env"
    env.write_text('X1=abc123  # rail key from email\nX2="quoted # kept"\nexport X3=plain\nX4=\n')
    load_dotenv(env)
    assert os.environ["X1"] == "abc123"
    assert os.environ["X2"] == "quoted # kept"
    assert os.environ["X3"] == "plain"
    assert os.environ["X4"] == ""


def test_unreadable_env_file_is_skipped_not_fatal(tmp_path, clean_env, capsys):
    env = tmp_path / ".env"
    env.write_text("X1=secretvalue\n")
    env.chmod(0)
    try:
        load_dotenv(env)
    finally:
        env.chmod(0o600)
    assert "secretvalue" not in capsys.readouterr().err


def test_relative_data_dir_is_relative_to_the_repo(clean_env, tmp_path):
    clean_env.setenv("BACKTRACK_ENV_FILE", str(tmp_path / "none"))
    clean_env.setenv("BACKTRACK_DATA_DIR", "data")
    clean_env.chdir(tmp_path)
    assert Settings.from_env().data_dir == (REPO_ROOT / "data").resolve()


def test_secrets_are_redacted_everywhere(tmp_path, clean_env):
    clean_env.setenv("BACKTRACK_ENV_FILE", str(tmp_path / "none"))
    clean_env.setenv("MARTA_RAIL_API_KEY", "railkey-9c43")
    clean_env.setenv("DATABASE_URL", "postgres://tsdbadmin:pa%zzword@h.tsdb.cloud.timescale.com:3/tsdb")
    Settings.from_env()
    assert "railkey-9c43" not in redact("GET /traindata?apiKey=railkey-9c43 failed")
    assert "pa%zzword" not in redact('invalid percent-encoded token: "pa%zzword"')
    assert redact("postgres://u:hunter2@host/db") == "postgres://u:***@host/db"
    assert "railkey" not in describe(ValueError("bad url ?apikey=railkey-9c43&x=1"))

    events = EventLog(tmp_path, "s", echo=False)
    events.emit("fetch_error", error="URL can't contain control characters. '?apiKey=railkey-9c43 '")
    status = RecorderStatus(source="s", last_error="db: pa%zzword")
    status.save(tmp_path)
    blob = (tmp_path / "logs" / "s.jsonl").read_text() + (tmp_path / "status" / "s.json").read_text()
    assert "railkey-9c43" not in blob and "pa%zzword" not in blob


def test_malformed_database_url_message_never_contains_the_password():
    from backtrack_worker.db import fingerprint

    with pytest.raises(ValueError) as e:
        fingerprint("postgres://tsdbadmin:Fake%PassWord9@host:5432/tsdb")
    assert "Fake" not in str(e.value)
    assert fingerprint("postgres://u:p@h:5432/db") == fingerprint("postgres://u:other@h:5432/db")
