"""Bench manifest: per-cell expected-deltas + reasons.

`test_pipeline.py` classifies real deltas against expectations: surprises
fail with "unexpected delta", unused expectations fail with "stale --
drop the entry"."""
from __future__ import annotations

import dataclasses
import pathlib
import re
import tomllib
from typing import Any, Dict, List, Optional, Tuple

from uhdi_common.diff import Delta


@dataclasses.dataclass(frozen=True)
class Expectation:
    """One expected delta. `path` (exact) and/or `path_regex` (re.fullmatch)
    must be set; `kind` optionally restricts to one delta kind."""
    reason: str
    path: Optional[str] = None
    path_regex: Optional[str] = None
    kind: Optional[str] = None

    def matches(self, delta: Delta) -> bool:
        d_path, d_kind, _, _ = delta
        if self.kind is not None and d_kind != self.kind:
            return False
        if self.path is not None and d_path != self.path:
            return False
        if self.path_regex is not None:
            if not re.fullmatch(self.path_regex, d_path):
                return False
        # Belt-and-braces: load_manifest() rejects items with neither set.
        return self.path is not None or self.path_regex is not None


@dataclasses.dataclass
class CellExpectations:
    fixture: str
    target: str
    items: List[Expectation] = dataclasses.field(default_factory=list)

    def classify(self, deltas: List[Delta]
                 ) -> Tuple[List[Tuple[Delta, Expectation]], List[Delta], List[Expectation]]:
        """Return (matched, surprises, unused). Each delta binds to the
        first matching expectation; one expectation may match many deltas."""
        matched: List[Tuple[Delta, Expectation]] = []
        surprises: List[Delta] = []
        used_expectations: set = set()

        for delta in deltas:
            hit: Optional[Expectation] = None
            for exp in self.items:
                if exp.matches(delta):
                    hit = exp
                    break
            if hit is None:
                surprises.append(delta)
            else:
                matched.append((delta, hit))
                used_expectations.add(id(hit))

        unused = [exp for exp in self.items if id(exp) not in used_expectations]
        return matched, surprises, unused


def load_manifest(path: pathlib.Path) -> Dict[Tuple[str, str], CellExpectations]:
    """Read TOML manifest. Returns `{(fixture, target): CellExpectations}`;
    missing cells are strict (zero-delta required)."""
    if not path.is_file():
        return {}
    with path.open("rb") as f:
        data = tomllib.load(f)

    out: Dict[Tuple[str, str], CellExpectations] = {}
    for fname, fcfg in (data.get("fixtures") or {}).items():
        expected = fcfg.get("expected") or {}
        for target, raw_items in expected.items():
            items: List[Expectation] = []
            for raw in raw_items or []:
                items.append(_parse_expectation(raw, fname, target))
            out[(fname, target)] = CellExpectations(
                fixture=fname, target=target, items=items)
    return out


def _parse_expectation(raw: Dict[str, Any], fname: str, target: str
                       ) -> Expectation:
    if "reason" not in raw:
        raise ValueError(
            f"manifest fixtures.{fname}.expected.{target}: every "
            f"expected delta needs a `reason` (it shows up in the "
            f"thesis ch.5 cell-divergence note)")
    if "path" not in raw and "path_regex" not in raw:
        raise ValueError(
            f"manifest fixtures.{fname}.expected.{target}: every "
            f"expected delta needs `path` or `path_regex` (otherwise "
            f"it would match every delta)")
    return Expectation(
        reason=raw["reason"],
        path=raw.get("path"),
        path_regex=raw.get("path_regex"),
        kind=raw.get("kind"),
    )
