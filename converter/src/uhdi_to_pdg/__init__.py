"""Convert uhdi JSON document to PDG (chiseltrace format).

Mirrors `chiseltrace-rs/src/pdg_spec.rs::PDGSpec`:

    {"vertices":   [PDGSpecNode, ...],
     "edges":      [PDGSpecEdge, ...],
     "predicates": [PDGSpecNode, ...],   # probe-style nodes (when conds)
     "cfg":        [CFGSpecStatement, ...]}

The third arm of the spec's three legacy targets (HGLDD, hgdb, PDG --
see uhdi-spec.md Sec.15). Two operating modes per Sec.15.5.4:

    uhdi-to-pdg INPUT --require-dataflow   # error if Sec.10 absent
    uhdi-to-pdg INPUT --derive-dataflow    # synthesise edges from Sec.5/Sec.7

Entry points:
    from uhdi_to_pdg import convert
    doc = convert(uhdi_document)            # auto-derives if Sec.10 missing

    from uhdi_common.backend import discover, get
    discover()
    get("pdg").convert(uhdi_document)"""
from .convert import PDGBackend, PDGConversionError, convert

__all__ = ["convert", "PDGBackend", "PDGConversionError"]
