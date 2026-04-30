"""Element-wise dict/list/scalar comparator.

Used by:
  * `test_golden`: strict equality vs hand-authored golden JSON
  * `uhdi_bench`: compare uhdi-derived projections to native references

Native references omit some fields (context_variable, scope rows, clock annotation).
Bench uses `mode="superset"` -- ours must be superset of native (missing=fail, extra=pass)."""
from __future__ import annotations

import json
from collections import Counter
from typing import Any, List, Tuple

# Delta: (path, kind, actual, expected). path is JSON-pointer-like;
# kind in {"type","extra","missing","length","value"}; actual is None
# for "missing", expected is None for "extra".
Delta = Tuple[str, str, Any, Any]


def diff_dicts(actual: Any, expected: Any, *, path: str = "",
               mode: str = "strict") -> List[Delta]:
    """Element-wise comparison; one delta per differing leaf.

    `mode`:
      * `"strict"`: exact equality. Lists compare index-by-index, dict keys
        must match exactly. Used by golden tests.
      * `"superset"`: `actual` may have extra items/keys. Every expected
        element must appear in actual (order-independent). Used by bench.

    In superset mode, deltas never include "extra" entries -- only "missing",
    "value", or "type". Empty list means "actual covers expected"."""
    deltas: List[Delta] = []
    _walk(actual, expected, path, mode, deltas)
    return deltas


def _walk(actual: Any, expected: Any, path: str, mode: str,
          deltas: List[Delta]) -> None:
    if type(actual) is not type(expected):
        deltas.append((path or "/", "type", actual, expected))
        return
    if isinstance(actual, dict):
        a_keys, e_keys = set(actual.keys()), set(expected.keys())
        if mode == "strict":
            for k in sorted(a_keys - e_keys):
                deltas.append((f"{path}/{k}", "extra", actual[k], None))
        for k in sorted(e_keys - a_keys):
            deltas.append((f"{path}/{k}", "missing", None, expected[k]))
        for k in sorted(a_keys & e_keys):
            _walk(actual[k], expected[k], f"{path}/{k}", mode, deltas)
        return
    if isinstance(actual, list):
        if mode == "strict":
            if len(actual) != len(expected):
                deltas.append(
                    (path or "/", "length", len(actual), len(expected)))
            for i in range(min(len(actual), len(expected))):
                _walk(actual[i], expected[i], f"{path}/{i}", mode, deltas)
            return
        # Superset mode: every expected item must appear in actual with correct
        # multiplicity ([1,1] expected vs [1] actual = missing). Compare via
        # JSON-canonical strings for unhashable dicts. Order in actual ignored.
        actual_pool: Counter = Counter(
            json.dumps(item, sort_keys=True) for item in actual)
        for i, exp_item in enumerate(expected):
            key = json.dumps(exp_item, sort_keys=True)
            if actual_pool[key] > 0:
                actual_pool[key] -= 1
            else:
                deltas.append((f"{path}/{i}", "missing", None, exp_item))
        return
    if actual != expected:
        deltas.append((path or "/", "value", actual, expected))


def format_deltas(deltas: List[Delta], *, max_rows: int = 50,
                  max_value_chars: int = 100) -> str:
    """Render deltas one per line, truncating long values.

    Skim-friendly for pytest output."""
    lines = [f"{len(deltas)} structural delta(s):"]
    for p, kind, actual, expected in deltas[:max_rows]:
        a = (json.dumps(actual, sort_keys=True)
             if actual is not None else "None")
        e = (json.dumps(expected, sort_keys=True)
             if expected is not None else "None")
        if len(a) > max_value_chars:
            a = a[: max_value_chars - 3] + "..."
        if len(e) > max_value_chars:
            e = e[: max_value_chars - 3] + "..."
        lines.append(f"  {kind:8} {p}  actual={a}  expected={e}")
    if len(deltas) > max_rows:
        lines.append(f"  ... ({len(deltas) - max_rows} more)")
    return "\n".join(lines)
