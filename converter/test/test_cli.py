"""Unit tests for uhdi_common.cli.main_for_backend."""
from __future__ import annotations

import json
import pathlib
from typing import Any, Dict, Optional

import pytest
from uhdi_common.backend import Backend
from uhdi_common.cli import main_for_backend
from uhdi_common.context import ConversionError

# ---- text-output backend (HGLDD-shaped) ----------------------------------


class _TextBackend(Backend):
    name = "test-text"
    binary_output = False
    output_extension = "json"

    def convert(self,
                uhdi: Dict[str, Any],
                output: Optional[pathlib.Path] = None
                ) -> Dict[str, Any]:
        return {"echo": uhdi.get("top", [])}


class _TextBackendReturningNone(Backend):
    """Text-output backend that returns None: CLI must map to exit 1, not emit 'null'."""
    name = "test-text-none"
    binary_output = False

    def convert(self,
                uhdi: Dict[str, Any],
                output: Optional[pathlib.Path] = None
                ) -> Optional[Dict[str, Any]]:
        return None


class _BackendThatRaises(Backend):
    name = "test-raises"
    binary_output = False

    def convert(self,
                uhdi: Dict[str, Any],
                output: Optional[pathlib.Path] = None
                ) -> Dict[str, Any]:
        raise ConversionError("synthetic conversion failure")


# ---- binary-output backend (HGDB-shaped) ---------------------------------


class _BinaryBackend(Backend):
    name = "test-binary"
    binary_output = True
    output_extension = "bin"

    def convert(self,
                uhdi: Dict[str, Any],
                output: Optional[pathlib.Path] = None
                ) -> None:
        assert output is not None
        output.write_bytes(b"BINARY")
        return None


# ---- input plumbing ------------------------------------------------------


def _write_input(tmp_path: pathlib.Path, contents: str) -> pathlib.Path:
    p = tmp_path / "input.uhdi.json"
    p.write_text(contents, encoding="utf-8")
    return p


def _doc_str(top=None) -> str:
    return json.dumps({
        "format": {"name": "uhdi", "version": "1.0"},
        "top": list(top or ["X"]),
    })


# ---- text-output behaviour -----------------------------------------------


def test_text_backend_writes_to_stdout_by_default(tmp_path, capsys):
    inp = _write_input(tmp_path, _doc_str(top=["A", "B"]))
    rc = main_for_backend(_TextBackend(), [str(inp)])
    out = capsys.readouterr().out
    assert rc == 0
    parsed = json.loads(out)
    assert parsed == {"echo": ["A", "B"]}


def test_text_backend_writes_to_file_when_output_given(tmp_path, capsys):
    inp = _write_input(tmp_path, _doc_str(top=["A"]))
    out_file = tmp_path / "out.json"
    rc = main_for_backend(_TextBackend(),
                          [str(inp), "-o", str(out_file)])
    captured = capsys.readouterr()
    assert rc == 0
    assert out_file.is_file()
    assert json.loads(out_file.read_text(encoding="utf-8")) == {"echo": ["A"]}
    assert "wrote" in captured.out


def test_text_backend_quiet_suppresses_wrote_line(tmp_path, capsys):
    inp = _write_input(tmp_path, _doc_str())
    out_file = tmp_path / "out.json"
    rc = main_for_backend(_TextBackend(),
                          [str(inp), "-o", str(out_file), "--quiet"])
    assert rc == 0
    assert "wrote" not in capsys.readouterr().out


def test_text_backend_returning_none_is_a_program_error(tmp_path, capsys):
    """A text backend returning None is a converter bug; CLI must not emit 'null'."""
    inp = _write_input(tmp_path, _doc_str())
    rc = main_for_backend(_TextBackendReturningNone(), [str(inp)])
    assert rc == 1
    assert "returned None" in capsys.readouterr().err


# ---- binary-output behaviour ---------------------------------------------


def test_binary_backend_requires_output_flag(tmp_path, capsys):
    """argparse marks -o required when binary_output=True; missing it exits 2."""
    inp = _write_input(tmp_path, _doc_str())
    with pytest.raises(SystemExit) as exc:
        main_for_backend(_BinaryBackend(), [str(inp)])
    assert exc.value.code == 2
    assert "required" in capsys.readouterr().err


def test_binary_backend_writes_file_and_announces(tmp_path, capsys):
    inp = _write_input(tmp_path, _doc_str())
    out = tmp_path / "out.bin"
    rc = main_for_backend(_BinaryBackend(), [str(inp), "-o", str(out)])
    captured = capsys.readouterr()
    assert rc == 0
    assert out.read_bytes() == b"BINARY"
    assert "wrote" in captured.out


def test_binary_backend_quiet_silences_wrote_line(tmp_path, capsys):
    inp = _write_input(tmp_path, _doc_str())
    out = tmp_path / "out.bin"
    rc = main_for_backend(_BinaryBackend(),
                          [str(inp), "-o", str(out), "--quiet"])
    assert rc == 0
    assert "wrote" not in capsys.readouterr().out


# ---- error paths ---------------------------------------------------------


def test_missing_input_returns_two(tmp_path, capsys):
    """Exit 2 (input problem) is distinct from convert failure exit 1."""
    rc = main_for_backend(_TextBackend(),
                          [str(tmp_path / "nope.json")])
    assert rc == 2
    assert "cannot read" in capsys.readouterr().err


def test_invalid_json_returns_two(tmp_path, capsys):
    inp = _write_input(tmp_path, "{not json")
    rc = main_for_backend(_TextBackend(), [str(inp)])
    assert rc == 2
    assert "cannot read" in capsys.readouterr().err


def test_conversion_error_returns_one(tmp_path, capsys):
    """ConversionError -> exit 1; input-read failure -> exit 2 (shell pipelines branch on this)."""
    inp = _write_input(tmp_path, _doc_str())
    rc = main_for_backend(_BackendThatRaises(), [str(inp)])
    assert rc == 1
    assert "synthetic conversion failure" in capsys.readouterr().err


# ---- --validate ----------------------------------------------------------


def test_validate_passes_then_runs_convert(tmp_path, capsys):
    """--validate is a precondition gate: schema OK -> convert; bad schema -> exit 2."""
    fixture = (pathlib.Path(__file__).parent / "fixtures" / "uhdi"
               / "counter.uhdi.json")
    rc = main_for_backend(_TextBackend(),
                          [str(fixture), "--validate"])
    assert rc == 0
    out = capsys.readouterr().out
    assert json.loads(out) == {"echo": ["Counter"]}


def test_validate_missing_jsonschema_dep_returns_two(tmp_path, capsys, monkeypatch):
    """Missing jsonschema -> targeted "install jsonschema" message, not a stack trace."""
    from uhdi_common import cli as cli_mod

    def _boom(*args, **kwargs):
        raise ImportError("No module named 'jsonschema'")

    # Patch the binding imported into cli, not the validate module's source name.
    monkeypatch.setattr(cli_mod, "validate_or_exit", _boom)
    inp = _write_input(tmp_path, _doc_str())
    rc = main_for_backend(_TextBackend(), [str(inp), "--validate"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "jsonschema" in err
    assert "referencing" in err


def test_text_backend_handles_output_path_oserror(tmp_path, capsys, monkeypatch):
    """Write failure -> exit 2 with "cannot write" message, not a stack trace."""
    inp = _write_input(tmp_path, _doc_str())
    out = tmp_path / "out.json"

    real_write = pathlib.Path.write_text

    def _explode(self, *args, **kwargs):
        if self == out:
            raise OSError("read-only filesystem")
        return real_write(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "write_text", _explode)
    rc = main_for_backend(_TextBackend(), [str(inp), "-o", str(out)])
    err = capsys.readouterr().err
    assert rc == 2
    assert "cannot write" in err


def test_validate_failure_aborts_before_convert(tmp_path, capsys):
    """Schema-invalid doc must NOT reach convert(); exit 2 from validation itself."""
    inp = _write_input(tmp_path, json.dumps({
        "top": ["X"],
    }))
    rc = main_for_backend(_TextBackend(),
                          [str(inp), "--validate"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "violation" in captured.err
    assert captured.out == ""
