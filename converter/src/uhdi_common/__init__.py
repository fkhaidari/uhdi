"""Shared infrastructure for uhdi -> X converters.

Three layers:

  context.py   BaseContext + the format/roles boilerplate every backend
               otherwise re-implements.  `BaseContext.from_uhdi(doc)`
               does the format-tag check, extracts authoring/simulation
               reprs, and exposes pool accessors (types, variables,
               scopes, expressions, representations).

  refs.py      Cross-pool resolvers used by every backend: varRef ->
               sigName, file-index lookups, location helpers.  Pure
               functions taking a context, no emitter-specific state.

  backend.py   Registry pattern: backends declare a name + describe
               their output mode (text vs binary), then register via a
               class decorator.  CLIs and bench harnesses iterate the
               registry instead of hard-coding `(hgldd, hgdb)` lists.

  validate.py  JSON Schema validation against schemas/*.schema.json.
               One source of truth so the validator behaves identically
               in CLI --validate, tests, and bench.

  cli.py       Shared argparse scaffolding for `python -m uhdi_to_X`
               style CLIs (input, -o, --validate, --quiet).

Backends register themselves at import time -- each converter package's
__init__.py imports its implementation, which calls `@register`.
`uhdi_common.backend.discover()` triggers that import for the in-tree
backends, so callers only need:

    from uhdi_common.backend import discover, get
    discover()
    backend = get("hgldd")
    out = backend.convert(uhdi, None)   # returns dict
"""
from .backend import Backend, all_backends, discover, get, register
from .context import BaseContext, ConversionError
from .diff import Delta, diff_dicts, format_deltas

__all__ = [
    "BaseContext", "ConversionError",
    "Backend", "register", "get", "all_backends", "discover",
    "Delta", "diff_dicts", "format_deltas",
]
