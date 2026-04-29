"""Unit tests for uhdi_common.backend -- the registry pattern."""
from __future__ import annotations

import pathlib
from typing import Any, Dict, Optional

import pytest
from uhdi_common import backend as backend_mod
from uhdi_common.backend import (
    Backend,
    all_backends,
    discover,
    get,
    register,
)


@pytest.fixture
def isolated_registry():
    """Snapshot/restore _REGISTRY so @register calls don't pollute sibling tests."""
    saved = dict(backend_mod._REGISTRY)
    try:
        yield
    finally:
        backend_mod._REGISTRY.clear()
        backend_mod._REGISTRY.update(saved)


# ---- discover() -----------------------------------------------------------


def test_discover_finds_in_tree_backends():
    # Don't clear _REGISTRY: discover() walks _KNOWN_PACKAGES via importlib, which
    # is a no-op for already-cached modules; clearing would falsely show "nothing".
    backends = discover()
    names = {b.name for b in backends}
    assert "hgldd" in names
    assert "hgdb" in names


def test_discover_is_idempotent():
    first = discover()
    second = discover()
    assert {b.name for b in first} == {b.name for b in second}


def test_get_returns_registered_backend():
    discover()
    b = get("hgldd")
    assert b.name == "hgldd"
    assert b.binary_output is False


def test_get_unknown_name_raises_with_helpful_message():
    discover()
    with pytest.raises(KeyError) as exc:
        get("nope")
    msg = str(exc.value)
    assert "nope" in msg
    assert "hgldd" in msg or "hgdb" in msg


def test_all_backends_returns_sorted():
    """Sorted iteration keeps CLI listings + report tables stable."""
    discover()
    names = [b.name for b in all_backends()]
    assert names == sorted(names)


# ---- @register ------------------------------------------------------------


def test_register_rejects_non_backend_class(isolated_registry):
    with pytest.raises(TypeError):
        register(int)  # type: ignore[arg-type]


def test_register_rejects_empty_name(isolated_registry):
    class _NoName(Backend):
        name = ""
        def convert(self, uhdi, output=None): return {}
    with pytest.raises(ValueError, match="non-empty"):
        register(_NoName)


def test_register_rejects_duplicate_name(isolated_registry):
    """Silent shadowing would mean the wrong converter runs depending on import order."""
    class _A(Backend):
        name = "duplicate-name-fixture"
        def convert(self, uhdi, output=None): return {}
    register(_A)
    class _B(Backend):
        name = "duplicate-name-fixture"
        def convert(self, uhdi, output=None): return {"alt": True}
    with pytest.raises(ValueError, match="already registered"):
        register(_B)


def test_register_returns_class_unmodified(isolated_registry):
    """Decorator returns the class itself so callers can use @register without reassign."""
    class _Sample(Backend):
        name = "decorator-return-fixture"
        def convert(self, uhdi, output=None): return {}
    returned = register(_Sample)
    assert returned is _Sample


def test_registered_backend_is_invokable(isolated_registry):
    class _Echo(Backend):
        name = "echo-fixture"
        description = "test backend"
        binary_output = False
        output_extension = "json"

        def convert(self,
                    uhdi: Dict[str, Any],
                    output: Optional[pathlib.Path] = None
                    ) -> Dict[str, Any]:
            return {"echoed": uhdi.get("top", [])}

    register(_Echo)
    out = get("echo-fixture").convert({"top": ["A", "B"]})
    assert out == {"echoed": ["A", "B"]}


# ---- canonical_dump default ----------------------------------------------


def test_canonical_dump_default_returns_none(isolated_registry, tmp_path):
    """Default returns None; golden harness raises NotImplementedError if a binary backend forgets the override."""
    class _PlainText(Backend):
        name = "plain-text-fixture"
        binary_output = False

        def convert(self, uhdi, output=None):
            return {}

    b = _PlainText()
    assert b.canonical_dump(tmp_path / "ignored") is None


# ---- discover() ImportError tolerance ------------------------------------


def test_discover_skips_packages_that_fail_to_import(isolated_registry, monkeypatch):
    """Slim install dropping a converter package: ImportError is swallowed, others still discover."""
    monkeypatch.setattr(
        backend_mod, "_KNOWN_PACKAGES",
        ("uhdi_to_hgldd", "uhdi_to_does_not_exist_xyz"))
    backends = backend_mod.discover()
    names = {b.name for b in backends}
    assert "hgldd" in names
