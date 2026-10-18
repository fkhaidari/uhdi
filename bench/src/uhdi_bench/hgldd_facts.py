"""Field-level comparison of two HGLDD documents.

A whole-object diff fails an object on any one field, so a single
expected difference hides every other one. This flattens each object
into facts keyed by (object, variable, field) and compares facts. Array
order does not matter; extra facts on our side are allowed.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

from uhdi_common.diff import Delta

Key = Tuple[str, str, str]


def _canon(field: str, v: Any) -> str:
    if field == "source_lang_type_info" and isinstance(v, dict) and v.get("params"):
        # tywaves-rs reads a param's type from `type` or `typeName`.
        v = dict(v, params=[
            {("typeName" if k == "type" else k): x for k, x in p.items()}
            for p in v["params"]])
    return json.dumps(v, sort_keys=True)


def facts(doc: Dict[str, Any]) -> Dict[Key, str]:
    f: Dict[Key, str] = {}
    for o in doc.get("objects", []):
        on = o.get("obj_name", "")
        f[(on, "", "kind")] = _canon("kind", o.get("kind"))
        for k in ("module_name", "source_lang_type_info", "hgl_loc", "isExtModule"):
            if k in o:
                f[(on, "", k)] = _canon(k, o[k])
        for c in o.get("children", []):
            for k in ("obj_name", "hdl_obj_name"):
                if k in c:
                    f[(on, "child:" + c.get("name", "?"), k)] = _canon(k, c[k])
        for pv in o.get("port_vars", []):
            vn = pv.get("var_name", "")
            for k, x in pv.items():
                if k != "var_name":
                    f[(on, vn, k)] = _canon(k, x)
    enums = doc.get("enum_defs")
    items = enums.items() if isinstance(enums, dict) else (
        (str(e.get("name")), e) for e in enums or [])
    for name, e in items:
        f[("enum:" + name, "", "def")] = _canon("def", e)
    return f


def _path(key: Key) -> str:
    return "/objects/" + "/".join(p for p in key if p)


def object_deltas(ours: Dict[str, Any], native: Dict[str, Any]) -> List[Delta]:
    """Deltas for facts native has and ours lacks or renders differently."""
    fo, fn = facts(ours), facts(native)
    out: List[Delta] = []
    for k in sorted(fn):
        if k not in fo:
            out.append((_path(k), "missing", None, json.loads(fn[k])))
        elif fo[k] != fn[k]:
            out.append((_path(k), "value", json.loads(fo[k]), json.loads(fn[k])))
    return out
