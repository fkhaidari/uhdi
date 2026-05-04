"""Unit tests for uhdi_common.validate."""
from __future__ import annotations

import json
import pathlib

import pytest
from uhdi_common import validate


def _minimal_valid_doc():
    return json.loads(
        (pathlib.Path(__file__).parent / "fixtures" / "uhdi"
         / "counter.uhdi.json").read_text(encoding="utf-8"))


# ---- make_document_validator ---------------------------------------------


def test_make_document_validator_loads_root_schema():
    v = validate.make_document_validator()
    assert hasattr(v, "iter_errors")


def test_make_document_validator_returns_fresh_instance():
    """Independent instances so callers can use them concurrently."""
    v1 = validate.make_document_validator()
    v2 = validate.make_document_validator()
    assert v1 is not v2


def test_make_document_validator_rejects_missing_schema_dir(monkeypatch):
    monkeypatch.setattr(validate, "_SCHEMA_DIR",
                        pathlib.Path("/does/not/exist"))
    with pytest.raises(FileNotFoundError, match="schema directory"):
        validate.make_document_validator()


def test_make_document_validator_rejects_schema_without_id(monkeypatch, tmp_path):
    """Schema without $id is silent breakage ($refs never resolve); fail loudly at build."""
    bad = tmp_path / "schemas"
    bad.mkdir()
    (bad / "broken.schema.json").write_text('{"type": "object"}',
                                            encoding="utf-8")
    monkeypatch.setattr(validate, "_SCHEMA_DIR", bad)
    with pytest.raises(ValueError, match="missing required \\$id"):
        validate.make_document_validator()


def test_make_document_validator_rejects_missing_root_schema(monkeypatch, tmp_path):
    """Without root ($ROOT_SCHEMA_ID), surface FileNotFoundError, not a KeyError."""
    sib = tmp_path / "schemas"
    sib.mkdir()
    (sib / "other.schema.json").write_text(
        '{"$id": "https://uhdi/other.schema.json", "type": "object"}',
        encoding="utf-8")
    monkeypatch.setattr(validate, "_SCHEMA_DIR", sib)
    with pytest.raises(FileNotFoundError, match="root schema"):
        validate.make_document_validator()


def test_make_document_validator_rejects_duplicate_id(monkeypatch, tmp_path):
    """A copy-paste with the same $id would otherwise silently overwrite."""
    sib = tmp_path / "schemas"
    sib.mkdir()
    payload = '{"$id": "https://uhdi/dup.schema.json", "type": "object"}'
    (sib / "a.schema.json").write_text(payload, encoding="utf-8")
    (sib / "b.schema.json").write_text(payload, encoding="utf-8")
    monkeypatch.setattr(validate, "_SCHEMA_DIR", sib)
    with pytest.raises(ValueError, match="duplicate \\$id"):
        validate.make_document_validator()


# ---- iter_errors ----------------------------------------------------------


def test_iter_errors_empty_for_valid_doc():
    errs = list(validate.iter_errors(_minimal_valid_doc()))
    assert errs == []


def test_iter_errors_flags_missing_required_field():
    doc = _minimal_valid_doc()
    del doc["format"]["name"]
    errs = list(validate.iter_errors(doc))
    assert errs
    assert any("format" in str(e.absolute_path) or "name" in e.message
               for e in errs)


def test_iter_errors_returns_sorted_by_path():
    """Stable ordering matters for CI logs / golden files; jsonschema iteration is dict-ordered."""
    doc = _minimal_valid_doc()
    doc["format"]["name"] = "not-uhdi"
    doc["top"] = "should-be-array"
    errs = list(validate.iter_errors(doc))
    paths = [list(e.absolute_path) for e in errs]
    assert paths == sorted(paths)


# ---- validate_or_exit -----------------------------------------------------


def test_validate_or_exit_returns_zero_for_valid(capsys):
    rc = validate.validate_or_exit(_minimal_valid_doc(),
                                   pathlib.Path("dummy"))
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.err == ""


def test_validate_or_exit_returns_two_and_prints_violations(capsys):
    """On failure: exit 2 plus every violation on stderr (CI needs actionable reports)."""
    doc = _minimal_valid_doc()
    del doc["format"]
    rc = validate.validate_or_exit(doc, pathlib.Path("design.uhdi.json"))
    captured = capsys.readouterr()
    assert rc == 2
    assert "design.uhdi.json" in captured.err
    assert "violation" in captured.err
    assert "<root>" in captured.err or "format" in captured.err


def test_validate_or_exit_propagates_import_error(monkeypatch):
    """Re-raise ImportError so CLI can surface a tailored 'install jsonschema' message."""
    real_make = validate.make_document_validator

    def boom():
        raise ImportError("jsonschema not installed")

    monkeypatch.setattr(validate, "make_document_validator", boom)
    with pytest.raises(ImportError):
        validate.validate_or_exit({}, pathlib.Path("d"))
    assert callable(real_make)


# ---- referential_errors ---------------------------------------------------


def test_referential_errors_empty_for_clean_doc():
    assert validate.referential_errors(_minimal_valid_doc()) == []


def test_referential_errors_flags_dangling_type_ref():
    doc = _minimal_valid_doc()
    doc["variables"] = {
        "v": {"typeRef": "ghost_type", "ownerScopeRef": "Counter"},
    }
    errs = validate.referential_errors(doc)
    assert any("typeRef" in e and "ghost_type" in e for e in errs)


def test_referential_errors_flags_dangling_top_scope():
    doc = _minimal_valid_doc()
    doc["top"] = ["nope"]
    errs = validate.referential_errors(doc)
    assert any("top[0]" in e and "nope" in e for e in errs)


def test_referential_errors_finds_nested_expr_ref():
    doc = _minimal_valid_doc()
    doc["expressions"] = {
        "a": {"opcode": "+", "operands": [{"exprRef": "missing"}]},
    }
    errs = validate.referential_errors(doc)
    assert any("exprRef" in e and "missing" in e for e in errs)


def test_referential_errors_does_not_flag_attributes():
    """`attributes` is user-defined; refs there shouldn't trigger."""
    doc = _minimal_valid_doc()
    doc["attributes"] = {"varRef": "irrelevant_user_data"}
    assert validate.referential_errors(doc) == []


def test_validate_or_exit_warns_on_dangling_refs_without_failing(capsys):
    doc = _minimal_valid_doc()
    doc["top"].append("ghost_scope")
    rc = validate.validate_or_exit(doc, pathlib.Path("d.uhdi.json"))
    captured = capsys.readouterr()
    assert rc == 0
    assert "warning" in captured.err
    assert "ghost_scope" in captured.err
