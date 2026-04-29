"""Backend registry: `name -> Backend` map with `@register` decorator.

`binary_output` selects mode: False returns a dict (CLI serialises),
True writes directly to `output` and requires `-o`."""
from __future__ import annotations

import abc
import importlib
import pathlib
from typing import Any, ClassVar, Dict, List, Optional, Type


class Backend(abc.ABC):
    name: ClassVar[str]
    description: ClassVar[str] = ""
    binary_output: ClassVar[bool] = False
    output_extension: ClassVar[str] = "json"

    @abc.abstractmethod
    def convert(self,
                uhdi: Dict[str, Any],
                output: Optional[pathlib.Path] = None
                ) -> Optional[Dict[str, Any]]:
        """text backend (binary_output=False): returns dict; CLI serialises.
        binary backend: `output` required, writes file, returns None."""
        ...

    def canonical_dump(self,
                       output: pathlib.Path) -> Optional[Dict[str, Any]]:
        """Read a binary output into a deterministic dict for `==`
        comparison. Override on binary backends."""
        return None


_REGISTRY: Dict[str, Backend] = {}

_KNOWN_PACKAGES = ("uhdi_to_hgldd", "uhdi_to_hgdb", "uhdi_to_hgdb_json")


def register(cls: Type[Backend]) -> Type[Backend]:
    """Class decorator: register backend instance by `cls.name`.
    Raises on duplicate name."""
    if not isinstance(cls, type) or not issubclass(cls, Backend):
        raise TypeError(f"@register expects a Backend subclass, got {cls!r}")
    instance = cls()
    if not instance.name:
        raise ValueError(f"{cls.__name__} must set a non-empty `name`")
    if instance.name in _REGISTRY:
        existing = _REGISTRY[instance.name].__class__.__name__
        raise ValueError(
            f"backend {instance.name!r} already registered by "
            f"{existing}; cannot re-register with {cls.__name__}")
    _REGISTRY[instance.name] = instance
    return cls


def get(name: str) -> Backend:
    if name not in _REGISTRY:
        known = ", ".join(sorted(_REGISTRY)) or "(none registered yet)"
        raise KeyError(
            f"no backend named {name!r}; known: {known}.  "
            f"Did you call discover() first?")
    return _REGISTRY[name]


def all_backends() -> List[Backend]:
    return [b for _, b in sorted(_REGISTRY.items())]


def discover() -> List[Backend]:
    """Import converter packages to fire `@register`. Idempotent."""
    for pkg in _KNOWN_PACKAGES:
        try:
            importlib.import_module(pkg)
        except ImportError:
            # Slim installs may omit a backend package; skip silently.
            continue
    return all_backends()
