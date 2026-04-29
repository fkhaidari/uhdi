"""BaseContext: shared converter state (pool lookups, repr selection)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, TypeVar


class ConversionError(Exception):
    """Base for backend-specific conversion failures."""


_DEFAULT_AUTHORING_REPR = "chisel"
_DEFAULT_SIMULATION_REPR = "verilog"


T = TypeVar("T", bound="BaseContext")


@dataclass
class BaseContext:
    """uhdi document + role-resolved repr names. Pool accessors default
    to empty dict so call sites can iterate without None-guards."""
    uhdi: Dict[str, Any]
    authoring_repr: str = _DEFAULT_AUTHORING_REPR
    simulation_repr: str = _DEFAULT_SIMULATION_REPR

    @classmethod
    def from_uhdi(cls: type[T], uhdi: Dict[str, Any], **extra: Any) -> T:
        """Validate format tag, extract role reprs. `extra` forwards to
        subclass fields. Raises `ConversionError` if not a uhdi document."""
        if uhdi.get("format", {}).get("name") != "uhdi":
            raise ConversionError("input is not a uhdi document")
        roles = uhdi.get("roles", {}) or {}
        return cls(
            uhdi=uhdi,
            authoring_repr=roles.get("authoring", _DEFAULT_AUTHORING_REPR),
            simulation_repr=roles.get("simulation", _DEFAULT_SIMULATION_REPR),
            **extra,
        )

    def _pool(self, key: str) -> Dict[str, Any]:
        return self.uhdi.get(key, {}) or {}

    @property
    def types(self) -> Dict[str, Any]:        return self._pool("types")
    @property
    def variables(self) -> Dict[str, Any]:    return self._pool("variables")
    @property
    def scopes(self) -> Dict[str, Any]:       return self._pool("scopes")
    @property
    def expressions(self) -> Dict[str, Any]:  return self._pool("expressions")
    @property
    def representations(self) -> Dict[str, Any]: return self._pool("representations")
