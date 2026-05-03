"""BaseContext: shared converter state (pool lookups, repr selection)."""
from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
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
        reprs = uhdi.get("representations", {}) or {}
        authoring = roles.get("authoring", _DEFAULT_AUTHORING_REPR)
        simulation = roles.get("simulation", _DEFAULT_SIMULATION_REPR)
        # Fail fast on typo'd role keys; otherwise downstream lookups
        # silently return None and surface as missing names.
        for role, key in (("authoring", authoring), ("simulation", simulation)):
            if key not in reprs:
                raise ConversionError(
                    f"roles.{role}={key!r} not in representations keys "
                    f"{sorted(reprs)}")
        return cls(
            uhdi=uhdi,
            authoring_repr=authoring,
            simulation_repr=simulation,
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

    @cached_property
    def _var_id_by_authoring_name(self) -> Dict[str, str]:
        """Authoring-name -> variable id; first-wins on duplicates."""
        index: Dict[str, str] = {}
        for vid, v in self.variables.items():
            name = ((v.get("representations", {}) or {})
                    .get(self.authoring_repr, {}) or {}).get("name")
            if name and name not in index:
                index[name] = vid
        return index
