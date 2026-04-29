"""Crude FIRRTL 4.x -> 1.x downgrader for hgdb-firrtl interop.

Handles simple single-module fixtures only; richer constructs
(intrinsics, bundles, mems with mport, define, propassign) hard-error."""
from __future__ import annotations

import argparse
import re
import sys


def _split_top_comma(s: str) -> tuple[str, str]:
    """Split on first comma at paren-depth 0."""
    depth = 0
    for i, c in enumerate(s):
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif c == "," and depth == 0:
            return s[:i].rstrip(), s[i + 1:].lstrip()
    raise ValueError(f"no top-level comma in {s!r}")


_REGRESET = re.compile(
    r'^(\s*)regreset\s+(\w+)\s*:\s*(.+?)\s*,\s*(\S+)\s*,\s*(\S+)\s*,\s*(.+?)\s*'
    r'(@\[.*\])?\s*$')
_CONNECT = re.compile(r'^(\s*)connect\s+(.+?)(\s*@\[.*\])?\s*$')
_HEX_LITERAL = re.compile(r'(\b[US]Int(?:<\d+>)?\()0h([0-9a-fA-F]+)(\))')


def _rewrite_hex(line: str) -> str:
    return _HEX_LITERAL.sub(r'\1"h\2"\3', line)


def downgrade(text: str, source_path: str | None = None) -> str:
    """Translate modern .fir to legacy 1.4-parser-compatible form."""
    del source_path
    out: list[str] = []
    for raw in text.splitlines():
        ln = raw.rstrip("\r")
        if ln.lstrip().startswith("FIRRTL version"):
            continue
        ln = re.sub(r'^(\s*)public\s+module\b', r'\1module', ln)
        ln = re.sub(r'^(\s*)public\s+extmodule\b', r'\1extmodule', ln)
        for banned in ("intrinsic", "propassign", "define ", "object "):
            if banned in ln:
                raise SystemExit(
                    f"unsupported in legacy FIRRTL: {ln.strip()!r}")
        ln = _rewrite_hex(ln)
        # regreset -> `reg ... with :` block; trailer moves to reset line.
        m = _REGRESET.match(ln)
        if m:
            indent, name, typ, clk, rst, val, ann = m.groups()
            ann = ann or ""
            out.append(f"{indent}reg {name} : {typ}, {clk} with :")
            out.append(f"{indent}  reset => ({rst}, {val}) {ann}".rstrip())
            continue
        # `connect lhs, rhs [@[..]]` -> `lhs <= rhs [@[..]]`.
        m = _CONNECT.match(ln)
        if m:
            indent, body, ann = m.groups()
            ann = ann or ""
            lhs, rhs = _split_top_comma(body)
            out.append(f"{indent}{lhs} <= {rhs}{ann}")
            continue
        out.append(ln)
    return "\n".join(out) + "\n"


def main() -> int:
    p = argparse.ArgumentParser(
        description="Crude FIRRTL 4.x -> 1.x downgrader for hgdb-firrtl interop.")
    p.add_argument("fir", help="modern .fir input")
    args = p.parse_args()
    with open(args.fir, encoding="utf-8") as f:
        text = f.read()
    sys.stdout.write(downgrade(text))
    return 0


if __name__ == "__main__":
    sys.exit(main())
