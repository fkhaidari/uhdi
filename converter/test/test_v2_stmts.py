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
    # A variable may carry its own class parameters, as `to_hgldd` reads them.
    params = copy.deepcopy(doc)
    var = next(v for m in params["original"].values()
               for v in (m.get("vars") or {}).values() if "x-chisel" in v)
    var["x-chisel"]["params"] = [{"name": "w", "type": "Int", "value": "8"}]
    assert ext.is_valid(params)


def _validators():
    jsonschema = pytest.importorskip("jsonschema")
    root = pathlib.Path(to_pdg.__file__).parent.parent / "schema"
    return (jsonschema.Draft202012Validator(
        json.loads((root / name).read_text()))
        for name in ("uhdi-2.1-draft.schema.json", "uhdi-x-chisel.schema.json"))


def _first_var(doc):
    mod = next(m for m in doc["original"].values() if m.get("vars"))
    key = next(iter(mod["vars"]))
    return mod, key, mod["vars"][key]


def test_schema_storage_has_no_mem(doc):
    core, _ = _validators()
    d = copy.deepcopy(doc)
    _, _, var = _first_var(d)
    var["storage"] = "reg"
    assert core.is_valid(d)
    var["storage"] = "mem"
    assert not core.is_valid(d)


def test_schema_key_alphabet(doc):
    core, _ = _validators()
    for bad in ("a.b", "a[0]", "a]"):
        d = copy.deepcopy(doc)
        mod, key, var = _first_var(d)
        mod["vars"][bad] = var
        assert not core.is_valid(d), bad
        d = copy.deepcopy(doc)
        bind = d["bindings"][0]
        bind["vars"][bad] = {"$optimized_out": True}
        assert not core.is_valid(d), bad
    d = copy.deepcopy(doc)
    mod, key, var = _first_var(d)
    mod["vars"]["a_b$1"] = var
    assert core.is_valid(d)
    d["types"].append({"kind": "struct", "fields": [{"name": "x", "type": 0}]})
    assert core.is_valid(d)
    d["types"][-1]["fields"][0]["name"] = "x.y"
    assert not core.is_valid(d)


def test_schema_path_grammar():
    core, _ = _validators()
    v = type(core)({"$defs": core.schema["$defs"], "$ref": "#/$defs/path"})
    for ok in ("a", "a.b", "a[0]", "a[i]", "a[i.j].k", "v[cnt][2]"):
        assert v.is_valid(ok), ok
    for bad in ("a.", "a[]", "a[b[0]]", "a[-1]", "a[1x]", "a..b", ".a", "a[0"):
        assert not v.is_valid(bad), bad


def test_schema_loc_column(doc):
    core, _ = _validators()
    d = copy.deepcopy(doc)
    mod = next(m for m in d["original"].values() if m.get("loc"))
    mod["loc"] = [mod["loc"][0], 3, 0]
    assert core.is_valid(d)
    mod["loc"] = [mod["loc"][0], 3, -1]
    assert not core.is_valid(d)
    mod["loc"] = [mod["loc"][0], 0, 1]
    assert not core.is_valid(d)


def test_schema_generated_module_keys(doc):
    core, _ = _validators()
    d = copy.deepcopy(doc)
    name = next(iter(d["generated"]))
    d["generated"][name]["signals"] = {"w": {"loc": [0, 1, 1]}}
    assert core.is_valid(d)
    d["generated"][name]["signals"]["w"]["dir"] = "in"
    assert not core.is_valid(d)


def test_schema_chisel_instance_var(doc):
    _, ext = _validators()
    d = copy.deepcopy(doc)
    var = next(v for m in d["original"].values()
               for v in (m.get("vars") or {}).values() if "x-chisel" in v)
    var["x-chisel"] = {"type_name": "Kid",
                       "params": [{"name": "w", "type": "Int"}]}
    assert ext.is_valid(d)
    var["x-chisel"] = {"params": [{"name": "w", "type": "Int"}]}
    assert not ext.is_valid(d)
    var["x-chisel"] = {"binding": "IO"}
    assert ext.is_valid(d)
    var["x-chisel"] = {"type_name": 1}
    assert not ext.is_valid(d)


def test_schema_text_rules():
    core, _ = _validators()
    text = json.dumps(core.schema)
    assert "unselected branch" in text
    assert "active-high" in text
    assert "subset of the original module" in text
    assert "UHDI 2" not in text and "v2" not in text


# ---- validate CLI and version check ---------------------------------------


def test_validate_current_layout(doc, tmp_path, capsys):
    from uhdi2.__main__ import main
    from uhdi2.validate import is_valid, iter_errors
    path = tmp_path / "d.json"
    path.write_text(json.dumps(doc))
    assert main(["validate", str(path)]) == 0
    assert is_valid(doc)
    # The core schema applies.
    bad = copy.deepcopy(doc)
    bad["bindings"][0]["bogus"] = 1
    assert list(iter_errors(bad))
    path.write_text(json.dumps(bad))
    assert main(["validate", str(path)]) == 2


def test_validate_x_chisel_only_when_listed(doc):
    from uhdi2.validate import is_valid
    assert "x-chisel" in doc["profiles"]
    bad = copy.deepcopy(doc)
    next(t for t in bad["types"] if t["kind"] == "uint")["x-chisel"] = {}
    assert not is_valid(bad)
    bad["profiles"].remove("x-chisel")
    bad["profiles"].append("x-other")
    assert is_valid(bad)


@pytest.mark.parametrize("version", ["1.0", "2.1", None])
def test_readers_reject_other_versions(doc, version, tmp_path):
    from uhdi2.v2 import UnsupportedVersion, to_hgldd, walk
    other = copy.deepcopy(doc)
    if version is None:
        other.pop("uhdi")
    else:
        other["uhdi"] = version
    for convert in (walk.Doc, to_hgldd.convert, to_pdg.convert,
                    to_hgdb_json.convert):
        with pytest.raises(UnsupportedVersion, match="unsupported document"):
            convert(other)
    with pytest.raises(UnsupportedVersion):
        to_hgdb.convert(other, tmp_path / "x.db")


# ---- display names, lazy ?: and flat names --------------------------------


def _named(doc):
    """`cnt` of `M` shown as `count`; the key stays what paths use."""
    doc = copy.deepcopy(doc)
    doc["original"]["M"]["vars"]["cnt"]["name"] = "count"
    return doc


def test_readers_show_var_name(doc, tmp_path):
    named = _named(doc)
    db = tmp_path / "t.db"
    to_hgdb.convert(named, db)
    gen = {n for (n,) in _rows(db, "SELECT name FROM generator_variable")}
    assert "count" in gen and "cnt" not in gen
    asg = _rows(db, "SELECT name, value FROM assignment")
    # The lowered name keeps the key, the value is what the user reads.
    assert ("cnt", "count") in asg
    out = to_hgdb_json.convert(named)
    names = {v["name"] for v in out["variables"]}
    assert "count" in names and "cnt" not in names
    pdg = {v["name"] for v in to_pdg.convert(named)["vertices"]}
    assert "defreg_count" in pdg and "connect_count" in pdg
    assert "defreg_cnt" not in pdg
    # Without `name` the key is shown, as before.
    assert "defreg_cnt" in {v["name"] for v in to_pdg.convert(doc)["vertices"]}


def test_hgdb_flat_name_clash_left_out(doc, tmp_path, capsys):
    clash = copy.deepcopy(doc)
    # `i.a` and `i_a` both lower to `i_a`.
    clash["original"]["M"]["stmts"].append(
        {"id": "s99", "kind": "connect", "lhs": "i_a", "loc": [0, 20, 1],
         "reads": ["sel"]})
    db = tmp_path / "t.db"
    to_hgdb.convert(clash, db)
    asg = _rows(db, "SELECT name, value FROM assignment")
    assert not [a for a in asg if a[0] == "i_a"]
    assert ("cnt", "cnt") in asg
    assert "i_a" in capsys.readouterr().err


def test_render_select_is_lazy():
    from uhdi2.v2.walk import render
    gone = {"$optimized_out": True}

    def sel(s, a, b):
        return {"$op": "?:", "args": [s, a, b]}
    assert render(sel("s", "a", "b")) == "s ? a : b"
    # A constant selector takes its branch; the other may be unknown.
    assert render(sel({"$const": "0b1"}, "a", gone)) == "a"
    assert render(sel({"$const": "0b0"}, gone, "b")) == "b"
    # The selected branch has no value, so the expression has none.
    assert render(sel({"$const": "0b0"}, "a", gone)) is None
    # A signal selector: hgdb cannot evaluate an unknown operand.
    assert render(sel("s", "a", gone)) is None
    assert render(sel(gone, "a", "b")) is None
    # Any other operator still needs every operand.
    assert render({"$op": "+", "args": ["a", gone]}) is None
