"""Unit tests for uhdi_to_hgdb.dump (canonical SQLite -> dict)."""
from __future__ import annotations

import sqlite3

import pytest
from uhdi_to_hgdb.dump import _check_table_name, canonical_dump


def _build_min_db(path):
    """Minimal hgdb schema with one row per non-empty table; isolates dump from converter."""
    conn = sqlite3.connect(str(path))
    conn.executescript("""
    CREATE TABLE 'instance' ( 'id' INTEGER PRIMARY KEY NOT NULL ,
        'name' TEXT NOT NULL , 'annotation' TEXT NOT NULL );
    CREATE TABLE 'variable' ( 'id' INTEGER PRIMARY KEY NOT NULL ,
        'value' TEXT NOT NULL , 'is_rtl' INTEGER NOT NULL );
    CREATE TABLE 'breakpoint' ( 'id' INTEGER PRIMARY KEY NOT NULL ,
        'instance_id' INTEGER , 'filename' TEXT NOT NULL ,
        'line_num' INTEGER NOT NULL , 'column_num' INTEGER NOT NULL ,
        'condition' TEXT NOT NULL , 'trigger' TEXT NOT NULL,
        FOREIGN KEY('instance_id') REFERENCES 'instance'('id'));
    CREATE TABLE 'scope' ( 'scope' INTEGER PRIMARY KEY NOT NULL ,
        'breakpoints' TEXT NOT NULL );
    CREATE TABLE 'context_variable' ( 'name' TEXT NOT NULL ,
        'breakpoint_id' INTEGER , 'variable_id' INTEGER ,
        'type' INTEGER NOT NULL,
        FOREIGN KEY('breakpoint_id') REFERENCES 'breakpoint'('id'),
        FOREIGN KEY('variable_id') REFERENCES 'variable'('id'));
    CREATE TABLE 'generator_variable' ( 'name' TEXT NOT NULL ,
        'instance_id' INTEGER , 'variable_id' INTEGER ,
        'annotation' TEXT NOT NULL,
        FOREIGN KEY('instance_id') REFERENCES 'instance'('id'),
        FOREIGN KEY('variable_id') REFERENCES 'variable'('id'));
    CREATE TABLE 'annotation' ( 'name' TEXT NOT NULL ,
        'value' TEXT NOT NULL );
    CREATE TABLE 'event' ( 'name' TEXT NOT NULL ,
        'transaction' TEXT NOT NULL , 'action' INTEGER NOT NULL ,
        'fields' TEXT NOT NULL , 'matches' TEXT NOT NULL ,
        'breakpoint_id' INTEGER,
        FOREIGN KEY('breakpoint_id') REFERENCES 'breakpoint'('id'));
    CREATE TABLE 'assignment' ( 'name' TEXT NOT NULL ,
        'value' TEXT NOT NULL , 'breakpoint_id' INTEGER ,
        'condition' TEXT NOT NULL , 'scope_id' INTEGER,
        FOREIGN KEY('breakpoint_id') REFERENCES 'breakpoint'('id'),
        FOREIGN KEY('scope_id') REFERENCES 'scope'('scope'));
    """)
    conn.execute("INSERT INTO instance VALUES (1, 'Top', '')")
    conn.execute("INSERT INTO variable VALUES (1, 'wire_q', 1)")
    conn.execute(
        "INSERT INTO breakpoint VALUES "
        "(1, 1, 'X.scala', 5, 7, '1', '')")
    conn.execute("INSERT INTO scope VALUES (1, '1')")
    conn.execute("INSERT INTO context_variable VALUES ('q', 1, 1, 0)")
    conn.execute(
        "INSERT INTO generator_variable VALUES ('q', 1, 1, '')")
    conn.execute("INSERT INTO annotation VALUES ('clock', 'Top.clock')")
    conn.commit()
    conn.close()


# ---- _check_table_name --------------------------------------------------


def test_check_table_name_accepts_known_table():
    assert _check_table_name("instance") == "instance"


def test_check_table_name_rejects_unknown():
    """SQLite identifiers can't be parameter-bound; allow-list guards SELECT f-string injection."""
    with pytest.raises(ValueError, match="unknown table"):
        _check_table_name("DROP_TABLE_users")


# ---- canonical_dump ------------------------------------------------------


def test_canonical_dump_resolves_instance_id_to_name(tmp_path):
    db = tmp_path / "x.db"
    _build_min_db(db)
    out = canonical_dump(db)
    bp = out["breakpoint"][0]
    assert bp.get("instance_name") == "Top"
    assert "instance_id" not in bp
    assert "id" not in bp


def test_canonical_dump_resolves_variable_id_to_value(tmp_path):
    db = tmp_path / "x.db"
    _build_min_db(db)
    out = canonical_dump(db)
    cv = out["context_variable"][0]
    assert cv.get("variable_value") == "wire_q"
    assert "variable_id" not in cv


def test_canonical_dump_resolves_breakpoint_to_synth_key(tmp_path):
    """Breakpoint key (instance,file,line,col,cond) stays equal across runs despite changing auto-increment ids."""
    db = tmp_path / "x.db"
    _build_min_db(db)
    out = canonical_dump(db)
    cv = out["context_variable"][0]
    assert cv["breakpoint_key"] == "Top@X.scala:5:7|1"


def test_canonical_dump_emits_all_nine_tables(tmp_path):
    """Empty tables appear as []; callers `==`-compare without guarding for missing keys."""
    db = tmp_path / "x.db"
    _build_min_db(db)
    out = canonical_dump(db)
    expected_tables = {"instance", "variable", "breakpoint", "scope",
                       "context_variable", "generator_variable",
                       "annotation", "event", "assignment"}
    assert set(out.keys()) == expected_tables
    assert out["event"] == []


def test_canonical_dump_sort_order_is_stable_across_runs(tmp_path):
    """Dump sorts by per-table identity tuple; equal content -> equal dicts."""
    db = tmp_path / "x.db"
    _build_min_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute("INSERT INTO annotation VALUES ('reset', 'Top.reset')")
    conn.commit()
    conn.close()
    out = canonical_dump(db)
    names = [row["name"] for row in out["annotation"]]
    assert names == sorted(names)
