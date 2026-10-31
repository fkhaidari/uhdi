"""Native v2-layout -> HGLDD converter (`uhdi2.v2.to_hgldd`).

Every tree-shaped / pool-shaped UHDI document available is re-laid into the
v2 layout by the `to_new.py` oracle (non-Dedup designs only) and converted by
the native converter. The result must equal the existing tree converter's
output (`uhdi2.to_hgldd`) on the same design, except for these deltas, which
the v2 layout drops by design:

* synthetic leaf ports (variables with no source whose name is an aggregate
  leaf) -- pruned from the tree before the reference run;
* a `loc` nested inside `target.verilog` (upgrade artifact) -- the oracle
  reads only `target.loc`, so it is dropped from the tree;
* extern modules keep no variables, instances or source -- pruned likewise;
* `hdl_loc` of module objects and of child instances -- the layout keeps
  only the target file, so the reference output is stripped of them.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import pathlib
from typing import Any, Dict, List

import pytest
from uhdi2.to_hgldd import convert as to_hgldd_tree
from uhdi2.upgrade import upgrade
from uhdi2.v2.to_hgldd import convert as _convert_v2
from uhdi_to_hgldd import HGLDDBackend

_REPO = pathlib.Path(__file__).resolve().parent.parent
_SCRATCH = pathlib.Path(os.environ.get(
    "UHDI_V2_SCRATCH",
    "/tmp/claude-1000/-home-farid-thesis-practice-circt/"
    "f530bb9d-2c21-4160-b837-09fe58cedb66/scratchpad"))
_TO_NEW = _SCRATCH / "to_new.py"


def _documents() -> List[pathlib.Path]:
    paths = sorted((_REPO / "test" / "fixtures" / "uhdi").glob("*.uhdi.json"))
    paths += sorted((_REPO / "test" / "fixtures" / "uhdi2").glob("*.json"))
    paths += sorted((_SCRATCH / "bench").glob("*/u.json"))
    return paths


def _id(p: pathlib.Path) -> str:
    return p.parent.name + "/" + p.name if p.name == "u.json" else p.name


def _to_new():
    if not _TO_NEW.is_file():
        pytest.skip("to_new.py oracle not available")
    spec = importlib.util.spec_from_file_location("to_new", _TO_NEW)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _indexed(doc: Dict[str, Any]) -> Dict[str, Any]:
    """The documents below key their types by name for readability; the
    layout lists them and refers to one by index, a type after its parts."""
    doc = copy.deepcopy(doc)
    types, index, out = doc["types"], {}, []

    def number(key: str) -> int:
        if key not in index:
            t = dict(types[key])
            if "fields" in t:
                t["fields"] = [{**f, "type": number(f["type"])} for f in t["fields"]]
            for ref in ("elem", "base"):
                if ref in t:
                    t[ref] = number(t[ref])
            index[key] = len(out)
            out.append(t)
        return index[key]

    for module in doc["original"].values():
        for var in (module.get("vars") or {}).values():
            var["type"] = number(var["type"])
    doc["types"] = out
    return doc


def to_hgldd_v2(doc: Dict[str, Any]) -> Dict[str, Any]:
    return _convert_v2(_indexed(doc))


def _tree(doc: Dict[str, Any]) -> Dict[str, Any]:
    return doc if "modules" in doc else upgrade(doc)


def _run_oracle(tree: Dict[str, Any], keep: bool) -> Dict[str, Any]:
    mod = _to_new()
    mod.pending.clear()
    mod.KEEP = keep
    return mod.convert(copy.deepcopy(tree))[0]


def _listify(v: Any, tref: str, types: Dict[str, Any]) -> Any:
    """to_new spells vector values as {"0": .., "1": ..}; the layout uses
    arrays, a hole becomes {"$none": "optimized"}."""
    t = types[tref]
    if t["kind"] == "struct" and isinstance(v, dict):
        return {f["name"]: _listify(v[f["name"]], f["type"], types)
                for f in t["fields"] if f["name"] in v}
    if t["kind"] == "vector" and isinstance(v, dict):
        return [_listify(v[str(i)], t["elem"], types) if str(i) in v
                else {"$none": "optimized"} for i in range(t["size"])]
    return v


def _relayout(doc: Dict[str, Any]) -> None:
    """The oracle writes instances as `target[T].inst` and inline sites as
    `binding.at`; turn both into instance variables of the parent."""
    types, source, bindings = doc["types"], doc["source"], doc["bindings"]

    def add(target: str, scope: str, name: str, module: str,
            loc: Any, value: Dict[str, Any]) -> None:
        tid = "module_" + module
        types.setdefault(tid, {"kind": "module", "module": module})
        var = {"type": tid, **({"loc": loc} if loc else {})}
        for b in bindings:
            if b["target"] == target and b.get("scope", "") == scope:
                source[b["source"]]["vars"][name] = dict(var)
                b["vars"][name] = value

    for tname, tmod in doc["target"].items():
        for vname, inst in (tmod.pop("inst", None) or {}).items():
            add(tname, "", inst.get("name", vname), inst["source"],
                inst.get("at"), {"$inst": vname, "module": inst["module"]})
    for b in bindings:
        if scope := b.get("scope"):
            parent, _, name = scope.rpartition(".")
            add(b["target"], parent, name, b["source"], b.pop("at", None),
                {"$inline": scope})


def v2_from_tree(tree: Dict[str, Any]) -> Dict[str, Any]:
    """Oracle output, re-laid into the layout."""
    doc = _run_oracle(tree, False)
    for b in doc["bindings"]:
        sv = doc["source"][b["source"]]["vars"]
        b["vars"] = {n: _listify(v, sv[n]["type"], doc["types"])
                     for n, v in b["vars"].items()}
    _relayout(doc)
    return _new_keys(doc)


def _new_keys(doc: Dict[str, Any]) -> Dict[str, Any]:
    """The oracle still spells the layout with `source` / `target` and
    `$none`; rename them to `original` / `generated` and `$optimized_out`."""
    names = {"source": "original", "target": "generated"}

    def value(v: Any) -> Any:
        if isinstance(v, list):
            return [value(x) for x in v]
        if not isinstance(v, dict):
            return v
        if "$none" in v:
            return {"$optimized_out": True}
        if "$inline" in v:
            return {"$inline": v["$inline"].split(".")}
        if "$op" in v:
            return {**v, "args": value(v.get("args") or [])}
        if any(k.startswith("$") for k in v):
            return v
        return {k: value(x) for k, x in v.items()}

    out = {names.get(k, k): v for k, v in doc.items()}
    for f in out["files"]:
        if "target" in f:
            f["generated"] = f.pop("target")
    names["scope"] = "inline_path"
    out["bindings"] = [
        {names.get(k, k): (value(x) if k == "vars" else
                           x.split(".") if k == "scope" else x)
         for k, x in b.items()}
        for b in out["bindings"]]
    return out


def _leaf_paths(types: Dict[str, Any], tref: str, prefix: str = ""):
    t = types[tref]
    if t["kind"] == "struct":
        for m in t["members"]:
            yield from _leaf_paths(types, m["typeRef"], prefix + m["name"] + "_")
    elif t["kind"] == "vector":
        for i in range(t["size"]):
            yield from _leaf_paths(types, t["elementRef"], prefix + str(i) + "_")
    else:
        yield prefix[:-1]


def _expected(tree: Dict[str, Any]) -> Dict[str, Any]:
    """Tree converter output with the v2 layout's deliberate deltas applied."""
    t = copy.deepcopy(tree)
    for m in t["modules"].values():
        if m.get("kind") == "extmodule":
            m.pop("variables", None)
            m.pop("instances", None)
            m.pop("source", None)
            continue
        leaves = set()
        for vn, v in m.get("variables", {}).items():
            if t["types"][v["typeRef"]]["kind"] in ("struct", "vector"):
                leaves |= {vn + "_" + p for p in _leaf_paths(t["types"], v["typeRef"])}
        for v in m.get("variables", {}).values():
            # the oracle reads only target.loc, not a loc nested in verilog
            vl = v.get("target", {}).get("verilog")
            if isinstance(vl, dict) and "loc" in vl and "loc" not in v["target"]:
                vl.pop("loc")
        m["variables"] = {vn: v for vn, v in m.get("variables", {}).items()
                          if v.get("source") or vn not in leaves}
    out = to_hgldd_tree(t)
    for o in out["objects"]:
        if o["kind"] != "module":
            continue
        o.pop("hdl_loc", None)
        for c in o["children"]:
            c.pop("hdl_loc", None)
    return out


@pytest.mark.parametrize("path", _documents(), ids=_id)
def test_matches_tree_converter(path: pathlib.Path) -> None:
    tree = _tree(json.loads(path.read_text(encoding="utf-8")))
    v2 = v2_from_tree(tree)
    assert v2["uhdi"] == "0.1"
    got = to_hgldd_v2(v2)
    want = _expected(tree)
    assert got == want


def test_backend_dispatches_on_uhdi_2() -> None:
    doc = _indexed(_dedup_doc())
    assert HGLDDBackend().convert(doc) == _convert_v2(doc)


# ---- hand-written documents ----------------------------------------------


def _dedup_doc() -> Dict[str, Any]:
    """Two source modules deduplicated into one Verilog module, one of them
    also inlined into Top as `h` (and, through `Mid`, `m.h`)."""
    u8 = {"kind": "uint", "width": 8}
    return {
        "uhdi": "0.1", "producer": "firtool", "profiles": ["x-chisel"],
        "files": [{"path": "/src/A.scala"}, {"path": "Top.sv", "generated": True}],
        "types": {"u8": u8, "b": {"kind": "uint", "width": 1},
                  "pair": {"kind": "struct", "fields": [
                      {"name": "x", "type": "u8"}, {"name": "y", "type": "u8"}]},
                  "vec2": {"kind": "vector", "elem": "u8", "size": 2},
                  "module_Left": {"kind": "module", "module": "Left"},
                  "module_Right": {"kind": "module", "module": "Right"},
                  "module_Mid": {"kind": "module", "module": "Mid"}},
        "original": {
            "Top": {"loc": [0, 1, 1], "vars": {
                "io": {"type": "u8", "loc": [0, 2, 3]},
                "l0": {"type": "module_Left", "loc": [0, 3, 5]},
                "r": {"type": "module_Right", "loc": [0, 4, 5]},
                "m": {"type": "module_Mid", "loc": [0, 7, 5]},
                "h": {"type": "module_Right", "loc": [0, 6, 5]}}},
            "Mid": {"loc": [0, 30, 1], "vars": {
                "h": {"type": "module_Left", "loc": [0, 5, 5]}}},
            "Left": {"loc": [0, 10, 1], "name": "Left",
                     "vars": {"a": {"type": "u8", "loc": [0, 11, 3]},
                              "p": {"type": "pair", "loc": [0, 12, 3]},
                              "v": {"type": "vec2", "loc": [0, 13, 3]},
                              "gone": {"type": "u8", "loc": [0, 14, 3]}}},
            "Right": {"loc": [0, 20, 1], "name": "Right",
                      "vars": {"b": {"type": "u8", "loc": [0, 21, 3]},
                               "a": {"type": "u8", "loc": [0, 22, 3]}}},
        },
        "generated": {
            "Top": {"loc": [1, 40]},
            "Shared": {"loc": [1, 2], "signals": {"a_0": {"loc": [1, 7, 3]}}},
            "Ext": {"kind": "extern"},
        },
        "bindings": [
            {"original": "Top", "generated": "Top", "vars": {
                "io": "io",
                "l0": {"$inst": "l0", "module": "Shared"},
                "r": {"$inst": "r0", "module": "Shared"},
                "m": {"$inline": ["m"]}, "h": {"$inline": ["h"]}}},
            {"original": "Left", "generated": "Shared",
             "vars": {"a": "a_0", "p": {"x": "p_x", "y": {"$const": 5}},
                      "v": ["v_0", {"$optimized_out": True}],
                      "gone": {"$optimized_out": True}}},
            {"original": "Right", "generated": "Shared", "vars": {"b": "b_0", "a": "clash"}},
            {"original": "Mid", "generated": "Top", "inline_path": ["m"],
             "vars": {"h": {"$inline": ["m", "h"]}}},
            {"original": "Left", "generated": "Top", "inline_path": ["m", "h"],
             "vars": {"a": "m_h_a"}},
            {"original": "Right", "generated": "Top", "inline_path": ["h"],
             "vars": {"b": {"$const": "0b101"}, "a": "io"}},
        ],
    }


def test_dedup_objects_and_inline_scopes() -> None:
    out = to_hgldd_v2(_dedup_doc())
    objs = {o["obj_name"]: o for o in out["objects"] if o["kind"] == "module"}
    mods = {o["module_name"]: o for o in out["objects"] if o["kind"] == "module"}

    # One object per source merged into Shared, each with its own variables.
    shared = objs["Left"]
    assert shared["module_name"] == "Shared"
    assert [p["var_name"] for p in shared["port_vars"]] == ["a", "p", "v", "gone"]
    right = objs["Right"]
    assert right["module_name"] == "Shared"
    assert [(p["var_name"], p["value"]) for p in right["port_vars"]] == [
        ("b", {"sig_name": "b_0"}), ("a", {"sig_name": "clash"})]
    assert right["source_lang_type_info"] == {"type_name": "Right"}
    # Both objects of Shared point at its one Verilog declaration.
    assert shared["hdl_loc"] == right["hdl_loc"] == {
        "file": 2, "begin_line": 2, "end_line": 2}
    a = shared["port_vars"][0]
    assert a["value"] == {"sig_name": "a_0"}
    assert a["hgl_loc"] == {"file": 1, "begin_line": 11, "end_line": 11,
                            "begin_column": 3, "end_column": 3}
    assert a["hdl_loc"]["file"] == 2 and a["hdl_loc"]["begin_line"] == 7
    assert shared["port_vars"][1]["value"] == {
        "opcode": "'{", "operands": [{"sig_name": "p_x"}, {"bit_vector": "00000101"}]}
    # An element without a value is unknown: x bits, as HGLDD has no empty operand.
    assert shared["port_vars"][2]["value"] == {
        "opcode": "'{", "operands": [{"sig_name": "v_0"}, {"bit_vector": "xxxxxxxx"}]}
    assert "value" not in shared["port_vars"][3]  # $optimized_out
    assert shared["source_lang_type_info"] == {"type_name": "Left"}

    top = mods["Top"]
    assert top["hdl_loc"]["begin_line"] == 40
    assert "hdl_loc" not in mods["Ext"]
    assert [c["name"] for c in top["children"][:2]] == ["l0", "r0"]
    assert [c["obj_name"] for c in top["children"][:2]] == ["Left", "Right"]
    assert [c["module_name"] for c in top["children"][:2]] == ["Shared", "Shared"]
    inline = {c["name"]: c for c in top["children"][2:]}
    assert set(inline) == {"m", "h"}
    assert inline["m"]["port_vars"] == []
    leaf = inline["m"]["children"][0]
    assert leaf["name"] == "h" and leaf["hgl_loc"]["begin_line"] == 5
    assert leaf["port_vars"][0]["value"] == {"sig_name": "m_h_a"}
    h = inline["h"]
    assert h["hgl_loc"]["begin_line"] == 6
    assert h["port_vars"][0]["value"] == {"bit_vector": "101"}
    assert h["port_vars"][1]["value"] == {"sig_name": "io"}

    assert mods["Ext"]["isExtModule"] == 1
    assert out["HGLDD"]["file_info"] == ["src/A.scala", "Top.sv"]
    assert out["HGLDD"]["hdl_file_index"] == 2


def test_dedup_inline_children_are_per_source() -> None:
    """A and B merge into one module; each inlines a different module as `w`."""
    u1 = {"kind": "uint", "width": 1}
    inst = lambda m: {"type": f"module_{m}", "loc": [0, 5, 5]}
    doc = {
        "uhdi": "0.1", "producer": "firtool", "profiles": ["x-chisel"],
        "files": [{"path": "/src/A.scala"}, {"path": "T.sv", "generated": True}],
        "types": {"u1": u1, "module_W1": {"kind": "module", "module": "W1"},
                  "module_W2": {"kind": "module", "module": "W2"}},
        "original": {
            "A": {"loc": [0, 1, 1], "vars": {"w": inst("W1")}},
            "B": {"loc": [0, 2, 1], "vars": {"w": inst("W2")}},
            "W1": {"loc": [0, 3, 1], "vars": {"p1": {"type": "u1", "loc": [0, 4, 3]}}},
            "W2": {"loc": [0, 5, 1], "vars": {"p2": {"type": "u1", "loc": [0, 6, 3]}}},
        },
        "generated": {"T": {"loc": [1, 1]}},
        "bindings": [
            {"original": "A", "generated": "T", "vars": {"w": {"$inline": ["w"]}}},
            {"original": "W1", "generated": "T", "inline_path": ["w"],
             "vars": {"p1": "p1"}},
            {"original": "B", "generated": "T", "vars": {"w": {"$inline": ["w"]}}},
            {"original": "W2", "generated": "T", "inline_path": ["w"],
             "vars": {"p2": "p2"}},
        ],
    }
    out = to_hgldd_v2(doc)
    objs = {o["obj_name"]: o for o in out["objects"] if o["kind"] == "module"}
    for src, var in (("A", "p1"), ("B", "p2")):
        (w,) = objs[src]["children"]
        assert [p["var_name"] for p in w["port_vars"]] == [var]
        assert w["hgl_loc"]["begin_line"] == 5


@pytest.mark.parametrize("index", [1, 5])
def test_duplicate_binding_rejected(index: int) -> None:
    doc = _dedup_doc()
    doc["bindings"].append(copy.deepcopy(doc["bindings"][index]))
    with pytest.raises(ValueError, match="more than one binding"):
        to_hgldd_v2(doc)


def test_single_class_info_and_type_on_instance() -> None:
    doc = _dedup_doc()
    doc["bindings"] = [b for b in doc["bindings"] if b["original"] != "Right"]
    doc["original"]["Top"]["vars"]["r"]["type"] = "module_Left"
    out = to_hgldd_v2(doc)
    shared = next(o for o in out["objects"] if o.get("module_name") == "Shared")
    assert shared["source_lang_type_info"] == {"type_name": "Left"}
    top = next(o for o in out["objects"] if o.get("module_name") == "Top")
    assert top["children"][1]["obj_name"] == "Left"  # the instance's type


def test_enum_values_listed_by_value() -> None:
    doc = _dedup_doc()
    doc["types"]["op"] = {"kind": "enum", "width": 2,
                          "values": {"Add": 0, "And": 2, "Sub": 1}}
    doc["original"]["Top"]["vars"]["op"] = {"type": "op", "loc": [0, 2, 5]}
    doc["bindings"][0]["vars"]["op"] = "op"
    top = next(o for o in to_hgldd_v2(doc)["objects"] if o.get("module_name") == "Top")
    assert list(top["enum_defs"]["0"].items()) == [("0", "Add"), ("1", "Sub"), ("2", "And")]


def test_expression_value() -> None:
    doc = _dedup_doc()
    doc["profiles"].append("expr")
    doc["bindings"][1]["vars"]["a"] = {"$op": "^", "args": [
        {"$op": "[]", "args": ["w", 3, 0]}, {"$const": "0b1111"}]}
    out = to_hgldd_v2(doc)
    left = next(o for o in out["objects"] if o["obj_name"] == "Left")
    assert left["port_vars"][0]["value"] == {"opcode": "^", "operands": [
        {"opcode": "[]", "operands": [{"sig_name": "w"}, {"integer_num": 3},
                                      {"integer_num": 0}]},
        {"bit_vector": "1111"}]}


def test_vector_type_info() -> None:
    doc = _dedup_doc()
    doc["types"]["pvec"] = {"kind": "vector", "elem": "pair", "size": 2}
    doc["original"]["Left"]["vars"]["pv"] = {
        "type": "pvec", "loc": [0, 15, 3], "dir": "input"}
    doc["types"]["u8"]["name"] = "UInt<8>"
    doc["types"]["pvec"]["name"] = "Pair[2]"
    doc["bindings"][1]["vars"]["pv"] = [{"x": "pv_0_x", "y": "pv_0_y"},
                                        {"x": "pv_1_x", "y": "pv_1_y"}]
    out = to_hgldd_v2(doc)
    left = next(o for o in out["objects"] if o["obj_name"] == "Left")
    pv = next(p for p in left["port_vars"] if p["var_name"].startswith("pv"))
    assert pv["source_lang_type_info"] == {"type_name": "IO[Pair[2]]", "params": [
        {"name": "gen", "type": "=> T"},
        {"name": "length", "type": "Int", "value": "2"}]}
    elems = [o for o in out["objects"] if o["obj_name"].startswith("Shared_pv")]
    assert elems
    for o in elems:
        assert [p.get("source_lang_type_info") for p in o["port_vars"]] == [
            {"type_name": "IO[UInt<8>]"}, {"type_name": "IO[UInt<8>]"}]


def test_struct_names_follow_hgldd() -> None:
    """A struct is named after the first variable reaching it, in its Verilog
    module; two types hinted to one name both append their source name."""
    from uhdi2.v2.types import named_types
    u8 = {"kind": "uint", "width": 8}
    doc = _indexed({
        "types": {"u8": u8,
                  "outer": {"kind": "struct", "name": "AnonymousBundle",
                            "fields": [{"name": "a", "type": "u8"}]},
                  "stage": {"kind": "struct", "name": "StageIO",
                            "fields": [{"name": "in", "type": "u8"},
                                       {"name": "x", "type": "inner"}]},
                  "inner": {"kind": "struct",
                            "fields": [{"name": "b", "type": "u8"}]}},
        "original": {"Cpu": {"vars": {"io": {"type": "outer"}}},
                     "Stage": {"vars": {"io": {"type": "stage"}}}},
        "bindings": [{"original": "Cpu", "generated": "Cpu", "vars": {}},
                     {"original": "Stage", "generated": "Cpu", "inline_path": ["s"],
                      "vars": {}}],
    })
    names = {k for k, t in named_types(doc)["types"].items()
             if t["kind"] == "struct"}
    assert names == {"Cpu_io_AnonymousBundle", "Cpu_io_StageIO", "Cpu_io_x"}


def test_zero_width_const() -> None:
    from uhdi2.v2.to_hgldd import _scalar
    from uhdi2.v2.walk import render
    assert _scalar({"$zero_width": True}, "", {}) == {"bit_vector": "0"}
    assert render({"$zero_width": True}) == "0"


def test_slice() -> None:
    from uhdi2.v2.to_hgldd import _scalar
    from uhdi2.v2.walk import render
    v = {"$slice": "io", "hi": 9, "lo": 6}
    assert _scalar(v, "", {}) == {"opcode": "[]", "operands": [
        {"sig_name": "io"}, {"integer_num": 9}, {"integer_num": 6}]}
    assert render(v) == "io[9:6]"


def test_unknown_reads_as_bits_or_nothing() -> None:
    """What a newer producer may write degrades instead of being guessed."""
    from uhdi2.v2.to_hgldd import _scalar, _type_description
    from uhdi2.v2.types import named_types
    from uhdi2.v2.walk import render
    types = {"f": {"kind": "float", "width": 32}}
    assert _type_description("f", types) == {
        "type_name": "logic", "packed_range": [31, 0]}
    assert _scalar({"$const": "0b1x"}, "", {}) == {}
    assert render({"$const": "0b1x"}) is None
    assert _scalar({"$weird": 1}, "", {}) == {}
    assert _scalar({"$op": "?:", "args": [
        {"$optimized_out": True}, "a", "b"]}, "", {}) == {}
    assert _scalar({"$op": "?:", "args": [
        "sel", {"$optimized_out": True}, {"$weird": 1}]}, "", {}) == {}
    assert _scalar({"$op": "+", "args": [
        "a", {"$optimized_out": True}]}, "", {}) == {}
    doc = {"types": [{"kind": "uint", "width": 1},
                     {"kind": "struct", "fields": [],
                      "variant": {"cases": [{"fields": [
                          {"name": "a", "type": 0}]}]}}]}
    case = named_types(doc)["types"]["struct_0"]["variant"]["cases"][0]
    assert case["fields"][0]["type"] == "#0"


def test_any_minor_is_v2() -> None:
    from uhdi2.v2 import is_v2
    assert is_v2({"uhdi": "0.1"}) and is_v2({"uhdi": "0.7"})
    assert not is_v2({"uhdi": "1.0"}) and not is_v2({})


# ---- names, instances, extern ports, class info ---------------------------


def _object(out: Dict[str, Any], module_name: str, obj_name: str = "") -> Dict[str, Any]:
    return next(o for o in out["objects"] if o.get("module_name") == module_name
                and (not obj_name or o["obj_name"] == obj_name))


def test_lazy_select_takes_the_known_branch() -> None:
    from uhdi2.v2.to_hgldd import _scalar
    gone = {"$optimized_out": True}
    ops = lambda *a: _scalar({"$op": "?:", "args": list(a)}, "", {})  # noqa: E731
    assert ops("s", "a", "b") == {"opcode": "?:", "operands": [
        {"sig_name": "s"}, {"sig_name": "a"}, {"sig_name": "b"}]}
    assert ops("s", "a", gone)["operands"][2] == {"sig_name": "a"}
    assert ops("s", gone, "b")["operands"][1] == {"sig_name": "b"}
    # Nothing known, or no selector: no value.
    assert ops("s", gone, gone) == {}
    assert ops(gone, "a", "b") == {}


def test_var_name_shown_and_key_binds() -> None:
    doc = _dedup_doc()
    doc["original"]["Left"]["vars"]["a"]["name"] = "alpha"
    out = to_hgldd_v2(doc)
    pv = _object(out, "Shared", "Left")["port_vars"]
    # The binding is read by key, the name is what the user sees.
    assert pv[0]["var_name"] == "alpha" and pv[0]["value"] == {"sig_name": "a_0"}


def test_top_port_var_names_are_unique() -> None:
    doc = _dedup_doc()
    left = doc["original"]["Left"]["vars"]
    left["a"]["name"] = "alpha"
    left["a2"] = {**left["a"], "name": "alpha"}
    out = to_hgldd_v2(doc)
    pv = _object(out, "Shared", "Left")["port_vars"]
    names = [v["var_name"] for v in pv]
    assert len(names) == len(set(names)) and {"alpha", "alpha_0"} <= set(names)


def test_instance_child_named_by_verilog_and_vectors() -> None:
    doc = _dedup_doc()
    doc["original"]["Top"]["vars"]["r"]["name"] = "right"
    top = _object(to_hgldd_v2(doc), "Top")
    r = top["children"][1]
    # tywaves looks the scope up by `name`, so it is the Verilog instance.
    assert r["name"] == "r0" and "hdl_obj_name" not in r
    assert r["hgl_loc"]["begin_line"] == 4

    doc = _dedup_doc()
    doc["types"]["module_Left_x2"] = {"kind": "vector", "elem": "module_Left", "size": 2}
    doc["original"]["Top"]["vars"]["ls"] = {"type": "module_Left_x2", "loc": [0, 8, 5]}
    doc["bindings"][0]["vars"]["ls"] = [{"$inst": "ls_0", "module": "Shared"},
                                        {"$inst": "ls_one", "module": "Shared"}]
    top = _object(to_hgldd_v2(doc), "Top")
    ls = [c for c in top["children"] if c["name"].startswith("ls")]
    assert [(c["name"], c["obj_name"], c["module_name"])
            for c in ls] == [("ls_0", "Left", "Shared"),
                             ("ls_one", "Left", "Shared")]
    assert "ls" not in [p["var_name"] for p in top["port_vars"]]


def test_extern_ports_come_from_the_binding() -> None:
    doc = _dedup_doc()
    doc["original"]["Black"] = {"vars": {"d": {"type": "u8", "loc": [0, 50, 3]}}}
    doc["bindings"].append({"original": "Black", "generated": "Ext",
                            "vars": {"d": "d_in"}})
    ext = _object(to_hgldd_v2(doc), "Ext")
    assert ext["isExtModule"] == 1 and ext["children"] == []
    assert ext["port_vars"][0]["var_name"] == "d"
    assert ext["port_vars"][0]["value"] == {"sig_name": "d_in"}
    assert ext["port_vars"][0]["hgl_loc"]["begin_line"] == 50
    # No binding, no ports, as before.
    assert _object(to_hgldd_v2(_dedup_doc()), "Ext")["port_vars"] == []


def test_hdl_locs_key_is_not_read() -> None:
    doc = _dedup_doc()
    doc["bindings"][1]["hdl_locs"] = {"a": [1, 99, 1]}
    out = to_hgldd_v2(doc)
    a = _object(out, "Shared", "Left")["port_vars"][0]
    assert a["hdl_loc"]["begin_line"] == 7  # the signal's loc


def test_class_info_from_the_instance_first() -> None:
    doc = _dedup_doc()
    # Producers now write the class on the instance, not on the module.
    for m in ("Left", "Right"):
        del doc["original"][m]["name"]
    params = [{"name": "w", "type": "Int", "value": 8}]
    doc["types"]["module_Left"].update(
        {"name": "LeftImpl", "x-chisel": {"params": params}})
    doc["types"]["module_Right"]["name"] = "RightImpl"
    out = to_hgldd_v2(doc)
    assert _object(out, "Shared", "Left")["source_lang_type_info"] == {
        "type_name": "LeftImpl",
        "params": [{"name": "w", "type": "Int", "value": "8"}]}
    assert _object(out, "Shared", "Right")["source_lang_type_info"] == {
        "type_name": "RightImpl"}
    # The root has no instance; its own record is the fallback.
    doc["original"]["Top"]["name"] = "TopImpl"
    assert _object(to_hgldd_v2(doc), "Top")["source_lang_type_info"] == {
        "type_name": "TopImpl"}


def test_class_info_instance_wins_over_module() -> None:
    doc = _dedup_doc()
    doc["types"]["module_Left"]["name"] = "LeftImpl"
    out = to_hgldd_v2(doc)
    assert _object(out, "Shared", "Left")["source_lang_type_info"] == {
        "type_name": "LeftImpl"}


def test_class_info_instances_that_differ_fall_back() -> None:
    doc = _dedup_doc()
    doc["types"]["module_A"] = {"kind": "module", "module": "Left", "name": "A"}
    doc["types"]["module_B"] = {"kind": "module", "module": "Left", "name": "B"}
    doc["original"]["Top"]["vars"]["l0"]["type"] = "module_A"
    doc["original"]["Top"]["vars"]["m2"] = {"type": "module_B"}
    doc["bindings"][0]["vars"]["m2"] = {"$inst": "m2", "module": "Shared"}
    out = to_hgldd_v2(doc)
    # Two classes for one module: the module's own record.
    assert _object(out, "Shared", "Left")["source_lang_type_info"] == {
        "type_name": "Left"}


def test_memory_is_an_array_of_element_traces() -> None:
    doc = _dedup_doc()
    doc["types"]["mem4"] = {"kind": "vector", "elem": "u8", "size": 4}
    doc["original"]["Left"]["vars"]["Memory"] = {
        "type": "mem4", "loc": [0, 15, 3], "storage": "mem"}
    doc["bindings"][1]["vars"]["Memory"] = "Memory"
    pv = _object(to_hgldd_v2(doc), "Shared", "Left")["port_vars"]
    mem = next(p for p in pv if p["var_name"] == "Memory")
    assert mem["value"] == {"opcode": "'{", "operands": [
        {"sig_name": f"Memory[{i}]"} for i in range(4)]}
    assert mem["unpacked_range"] == [3, 0]


def test_named_types_keeps_ext_values_opaque() -> None:
    from uhdi2.v2.types import named_types
    ext = {"base": 16, "type": 0, "n": {"elem": 1}}
    doc = {"types": [
        {"kind": "uint", "width": 1, "x-vhdl": copy.deepcopy(ext)},
        {"kind": "vector", "elem": 0, "size": 2}]}
    out = named_types(doc)["types"]
    assert out["#0"]["x-vhdl"] == ext
    assert out["array_0"]["elem"] == "#0"


def test_instance_inside_inline_is_a_child_of_the_inline_node() -> None:
    doc = _dedup_doc()
    doc["original"]["Right"]["vars"]["w"] = {"type": "module_Left", "loc": [0, 20, 5]}
    doc["bindings"][5]["vars"]["w"] = {"$inst": "h_w", "module": "Shared"}
    top = _object(to_hgldd_v2(doc), "Top")
    h = next(c for c in top["children"] if c["name"] == "h")
    w = h["children"][0]
    assert (w["name"], w["obj_name"], w["module_name"]) == ("h_w", "Left", "Shared")
    assert w["hgl_loc"]["begin_line"] == 20
    assert "w" not in [p["var_name"] for p in h["port_vars"]]
