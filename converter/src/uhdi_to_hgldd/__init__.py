"""Convert uhdi JSON document to HGLDD 1.0 (consumed by Tywaves, Surfer, Verdi).

Two entry points:

    # Functional API
    from uhdi_to_hgldd import convert
    hgldd = convert(uhdi_document)

    # Backend registry (for harnesses)
    from uhdi_common.backend import discover, get
    discover()
    hgldd = get("hgldd").convert(uhdi_document)

Mapping follows docs/uhdi-spec.md sec.15.3 -- field-level transliteration with
struct dedup and packed/unpacked range conversion."""
from .convert import HGLDDBackend, HGLDDConversionError, convert

__all__ = ["convert", "HGLDDBackend", "HGLDDConversionError"]
