from backtrack_worker.migrate import MIGRATIONS_DIR, discover, split_statements


def test_splits_on_top_level_semicolons_only():
    sql = """
    -- a comment; with a semicolon
    CREATE TABLE a (x text DEFAULT 'it''s; fine');
    /* block; comment */
    DO $$
    BEGIN
        IF true THEN PERFORM 1; END IF;
    END $$;
    CREATE FUNCTION f() RETURNS int LANGUAGE sql AS $body$ SELECT 1; $body$;
    SELECT "weird;name" FROM a
    """
    parts = split_statements(sql)
    assert len(parts) == 4
    assert parts[0].endswith("DEFAULT 'it''s; fine')")
    assert "PERFORM 1; END IF;" in parts[1]
    assert "$body$ SELECT 1; $body$" in parts[2]
    assert parts[3].endswith('FROM a')


def test_comment_only_tail_is_not_a_statement():
    assert split_statements("SELECT 1;\n-- trailing note\n") == ["SELECT 1"]


def test_repo_migrations_are_numbered_and_parse():
    found = discover(MIGRATIONS_DIR)
    assert [m.version for m in found][0] == "001"
    for m in found:
        stmts = m.statements()
        assert stmts, m.name
        assert all(s.strip() for s in stmts)


def test_001_has_the_recorder_identity_and_live_gaps_view():
    found = {m.version: m for m in discover(MIGRATIONS_DIR)}
    text = " ".join(found["001"].statements())
    assert "UNIQUE (source, recorder, kind, started_at)" in text
    assert "PRIMARY KEY (source, recorder)" in text
    assert "CREATE OR REPLACE VIEW recorder_gaps_live" in text
