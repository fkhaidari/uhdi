"""Convert uhdi JSON document into hgdb SQLite symbol table.

Two entry points:

    # Functional API
    from uhdi_to_hgdb import convert
    convert(uhdi_document, "design.db")

    # Backend registry (for harnesses)
    from uhdi_common.backend import discover, get
    discover()
    backend = get("hgdb").convert(uhdi_document, pathlib.Path("design.db"))

Mapping follows docs/uhdi-spec.md sec.15.4:
  - instance: scope tree -> hierarchy paths
  - variable + generator_variable: dbg.variables with Verilog sigName
  - breakpoint: (connect|decl, instance) pairs with loc + condition
  - context_variable: in-scope variable -> breakpoint links (locals panel)
  - annotation: clock signals (RTLSimulatorClient prefixes instance path)"""
from .convert import HGDBBackend, HGDBConversionError, convert
from .dump import canonical_dump

__all__ = ["convert", "HGDBBackend", "HGDBConversionError", "canonical_dump"]
