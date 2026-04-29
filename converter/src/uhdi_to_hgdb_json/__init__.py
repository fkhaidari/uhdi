"""Convert uhdi JSON document to hgdb's JSON symbol-table format.

This converter mirrors hgdb-circt's JSON output (alternative to SQLite from hgdb-firrtl).

Schema (verified against hgdb's parser in src/db.cc and hgdb-circt's emitter):

    {"generator": "uhdi",
     "top": "Top" | ["Top", ...],
     "variables": [{"id": "0", "name": "r", "value": "r", "rtl": true}, ...],
     "table": [
        {"type": "module", "name": "Top",
         "variables": ["0", {"name": ..., "value": ..., "rtl": true}, ...],
         "scope": [
            {"type": "block", "filename": "Top.scala",
             "scope": [
                {"type": "decl", "variable": "0", "line": 5, "column": 3},
                {"type": "assign", "variable": "1", "line": 7, "column": 3},
                ...
             ]},
         ],
         "instances": [{"name": "u1", "module": "Sub"}, ...]
        }]}

Entry points:
    from uhdi_to_hgdb_json import convert
    doc = convert(uhdi_document)

    # Backend registry (used by uhdi_bench)
    from uhdi_common.backend import discover, get
    discover()
    get("hgdb_json").convert(uhdi_document)"""
from .convert import HGDBJsonBackend, HGDBJsonConversionError, convert

__all__ = ["convert", "HGDBJsonBackend", "HGDBJsonConversionError"]
