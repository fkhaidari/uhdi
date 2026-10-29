"""Converters that read source statements of the v2 layout: hgdb SQLite,
hgdb JSON and PDG.

The fixture is `firtool --uhdi-stmts --uhdi-format=2` on CIRCT's
`test/firtool/emit-uhdi-v2-stmts.fir`: a module `M` with a reset register,
nested `when`/`else`, a dynamic write `v[cnt]`, an inlined module `i` and
two instances of a deduplicated `Kid`.
"""
from __future__ import annotations

import copy
import json
import pathlib
import sqlite3

import pytest
from uhdi2.v2 import to_hgdb, to_hgdb_json, to_pdg

_DOC = pathlib.Path(__file__).parent / "fixtures" / "v2" / "stmts.json"


@pytest.fixture
def doc():
    return json.loads(_DOC.read_text())


def _rows(db, sql):
    with sqlite3.connect(str(db)) as conn:
        return conn.execute(sql).fetchall()


def test_hgdb_breakpoints(doc, tmp_path):
    db = tmp_path / "t.db"
    to_hgdb.convert(doc, db)
    bps = {(inst, line, col): cond for inst, line, col, cond in _rows(
        db, "SELECT instance.name, line_num, column_num, condition FROM "
            "breakpoint JOIN instance ON instance.id = instance_id")}
    # A `when` stops under the enable it sits in.
    assert bps[("M", 7, 12)] == "1"
    assert bps[("M", 9, 20)] == "!sel"
    # A connect: the reset term, then the enable, innermost first.
    assert bps[("M", 8, 9)] == "!reset && sel"
    assert bps[("M", 10, 14)] == "!0 && _GEN && !sel"
    # A named node stops every cycle.
    assert bps[("M", 6, 13)] == "1"
    # The inlined module stops in the instance that absorbed it.
    assert bps[("M", 5, 9)] == "sel"
    # Both instances of the deduplicated module.
    assert bps[("M.k0", 3, 20)] == bps[("M.k1", 3, 20)] == "!0 && en"
    # Declarations do not stop.
    assert ("M", 3, 20) not in bps
    asg = _rows(db, "SELECT name, value FROM assignment")
    assert ("v_cnt", "v[cnt]") not in asg and ("i_a", "i.a") in asg


def test_hgdb_json_scope(doc):
    out = to_hgdb_json.convert(doc)
    assert out["top"] == "M"
    m = next(t for t in out["table"] if t["name"] == "M")
    body = m["scope"][0]["scope"]
    names = {v["id"]: v["name"] for v in out["variables"]}
    # The node is hoisted out of every block.
    assert body[0] == {"type": "decl", "variable": body[0]["variable"],
                       "line": 6, "column": 13}
    assert names[body[0]["variable"]] == "t"
    when = body.index({"type": "none", "line": 7, "column": 12})
    then, other = body[when + 1], body[when + 2]
    assert then["condition"] == "sel" and other["condition"] == "!sel"
    # An assign to a register with a reset holds the reset term itself.
    assert then["scope"] == [{"type": "assign", "variable": then["scope"][0]
                              ["variable"], "line": 8, "column": 9,
                              "condition": "!reset"}]
    # The dynamic write has no static signal; only its block remains.
    assert other["scope"] == [{"type": "none", "line": 9, "column": 20}]
    # A connect to a port of the inlined module names its signal.
    assign = next(e for e in body if e.get("line") == 13)
    assert names[assign["variable"]] == "i.a"
    assert {(i["name"], i["module"]) for i in m["instances"]} == {
        ("k0", "Kid"), ("k1", "Kid_1")}


def test_pdg_graph(doc):
    out = to_pdg.convert(doc)
    verts = out["vertices"]
    preds = [p["name"] for p in out["predicates"]]
    # Predicates are guard signals, scoped by instance.
    assert preds == ["sel", "_GEN", "k0.en", "k1.en", "sel"]
    # The `when`/`else` nest becomes a cfg branch with both arms.
    node = next(c for c in out["cfg"] if c.get("predStmtRef") == 0)
    assert verts[node["stmtRef"]]["kind"] == "ControlFlow"
    assert verts[node["trueBranch"][0]["stmtRef"]]["assignsTo"] == "cnt"
    inner = node["falseBranch"][0]
    assert inner["predStmtRef"] == 1
    # A write through a dynamic index writes the whole vector.
    assert verts[inner["trueBranch"][0]["stmtRef"]]["assignsTo"] == "v"
    # Instance statements are named under the instance.
    k0 = [v for v in verts if v["modulePath"] == ["k0"]]
    assert {v["assignsTo"] for v in k0} >= {"k0.r", "k0.q"}
    # A register connect reads the register's last value.
    r = verts.index(next(v for v in k0 if v["name"] == "connect_r"))
    decl = verts.index(next(v for v in k0 if v["name"] == "defreg_r"))
    data = {(e["to"], e["clocked"]) for e in out["edges"]
            if e["from"] == r and e["kind"] == "Data"}
    assert {(r, True), (decl, True)} <= data


def test_schema(doc):
    jsonschema = pytest.importorskip("jsonschema")
    root = pathlib.Path(to_pdg.__file__).parent.parent / "schema"
    for name in ("uhdi-2.1-draft.schema.json", "uhdi-x-chisel.schema.json"):
        schema = json.loads((root / name).read_text())
        jsonschema.Draft202012Validator(schema).validate(doc)


def test_schema_extensions(doc):
    jsonschema = pytest.importorskip("jsonschema")
    root = pathlib.Path(to_pdg.__file__).parent.parent / "schema"
    core, ext = (jsonschema.Draft202012Validator(
        json.loads((root / name).read_text()))
        for name in ("uhdi-2.1-draft.schema.json", "uhdi-x-chisel.schema.json"))
    # The core takes any `x-` key and any `x-` profile without looking inside.
    other = copy.deepcopy(doc)
    other["profiles"].append("x-other")
    other["x-other"] = {"anything": 1}
    assert core.is_valid(other)
    other["profiles"].append("other")
    assert not core.is_valid(other)
    # The companion checks where `x-chisel` sits.
    misplaced = copy.deepcopy(doc)
    next(t for t in misplaced["types"] if t["kind"] == "uint")["x-chisel"] = {}
    assert core.is_valid(misplaced)
    assert not ext.is_valid(misplaced)
