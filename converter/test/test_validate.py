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


def test_referential_errors_flags_dangling_cond_ref():
    doc = _minimal_valid_doc()
    doc["scopes"]["s_assert"] = {
        "name": "assert_scope",
        "kind": "inline",
        "body": [{"kind": "assert", "condRef": "ghost_expr"}],
    }
    errs = validate.referential_errors(doc)
    assert any("condRef" in e and "ghost_expr" in e for e in errs)


def test_referential_errors_flags_dangling_container_scope_ref():
    doc = _minimal_valid_doc()
    doc["scopes"]["s_inline"] = {
        "name": "inline_scope",
        "kind": "inline",
        "containerScopeRef": "ghost_scope",
    }
    errs = validate.referential_errors(doc)
    assert any("containerScopeRef" in e and "ghost_scope" in e for e in errs)


def test_referential_errors_flags_dangling_dataflow_ref():
    doc = _minimal_valid_doc()
    doc["dataflow"] = {
        "edges": [
            {"from": {"varRef": "ghost_src"},
             "to":   {"varRef": "ghost_dst"},
             "kind": "Data"}
        ]
    }
    errs = validate.referential_errors(doc)
    assert any("varRef" in e and "ghost_src" in e for e in errs)


# ---- representations_errors -----------------------------------------------


def test_representations_errors_empty_for_clean_doc():
    assert validate.representations_errors(_minimal_valid_doc()) == []


def test_representations_errors_flags_unknown_variable_key():
    doc = _minimal_valid_doc()
    first_var_id = next(iter(doc["variables"]))
    doc["variables"][first_var_id].setdefault("representations", {})
    doc["variables"][first_var_id]["representations"]["chiSel"] = {
        "name": "io_en"
    }
    errs = validate.representations_errors(doc)
    assert any("chiSel" in e and first_var_id in e for e in errs)


def test_representations_errors_flags_unknown_scope_key():
    doc = _minimal_valid_doc()
    first_scope_id = next(iter(doc["scopes"]))
    doc["scopes"][first_scope_id].setdefault("representations", {})
    doc["scopes"][first_scope_id]["representations"]["verylog"] = {
        "name": "Counter"
    }
    errs = validate.representations_errors(doc)
    assert any("verylog" in e and first_scope_id in e for e in errs)


def test_validate_or_exit_warns_on_unknown_representation(capsys):
    doc = _minimal_valid_doc()
    first_var_id = next(iter(doc["variables"]))
    doc["variables"][first_var_id].setdefault("representations", {})
    doc["variables"][first_var_id]["representations"]["chiSel"] = {
        "name": "io_en"
    }
    rc = validate.validate_or_exit(doc, pathlib.Path("d.uhdi.json"))
    captured = capsys.readouterr()
    assert rc == 0
    assert "warning" in captured.err
    assert "chiSel" in captured.err
    assert "unknown representation" in captured.err


# ---- duplicate_authoring_name_errors (FU2.8) --------------------------------


def test_duplicate_authoring_name_errors_empty_for_clean_doc():
    assert validate.duplicate_authoring_name_errors(_minimal_valid_doc()) == []


def test_duplicate_authoring_name_errors_flags_cross_scope_collision():
    """Two variables sharing the same chisel.name collapse to a first-wins
    resolution in BaseContext._var_id_by_authoring_name (FU2.8)."""
    doc = _minimal_valid_doc()
    first_var_id, first_var = next(iter(doc["variables"].items()))
    name = first_var["representations"]["chisel"]["name"]
    doc["variables"]["var_dup"] = {
        "bindKind": "wire",
        "typeRef": first_var["typeRef"],
        "ownerScopeRef": first_var["ownerScopeRef"],
        "representations": {"chisel": {"name": name}},
    }
    errs = validate.duplicate_authoring_name_errors(doc)
    assert any(name in e and first_var_id in e and "var_dup" in e
               for e in errs)


def test_duplicate_authoring_name_errors_ignores_distinct_names():
    """Different names in the same repr are not a collision."""
    doc = _minimal_valid_doc()
    doc["variables"]["var_extra"] = {
        "bindKind": "wire",
        "typeRef": next(iter(doc["variables"].values()))["typeRef"],
        "ownerScopeRef": next(iter(doc["variables"].values()))[
            "ownerScopeRef"],
        "representations": {"chisel": {"name": "totally_new_name"}},
    }
    assert validate.duplicate_authoring_name_errors(doc) == []


def test_validate_or_exit_warns_on_duplicate_authoring_name(capsys):
    doc = _minimal_valid_doc()
    first_var = next(iter(doc["variables"].values()))
    name = first_var["representations"]["chisel"]["name"]
    doc["variables"]["var_dup"] = {
        "bindKind": "wire",
        "typeRef": first_var["typeRef"],
        "ownerScopeRef": first_var["ownerScopeRef"],
        "representations": {"chisel": {"name": name}},
    }
    rc = validate.validate_or_exit(doc, pathlib.Path("d.uhdi.json"))
    captured = capsys.readouterr()
    assert rc == 0
    assert "duplicate authoring name" in captured.err
    assert name in captured.err


# ---- cross_pool_collision_errors (FU2.12) -----------------------------------


def test_cross_pool_collision_errors_empty_for_clean_doc():
    assert validate.cross_pool_collision_errors(_minimal_valid_doc()) == []


def test_cross_pool_collision_errors_flags_shared_id():
    """An id present in both expressions and variables resolves to a
    different pool per backend (FU2.12); surface the latent divergence."""
    doc = _minimal_valid_doc()
    first_var_id, first_var = next(iter(doc["variables"].items()))
    doc.setdefault("expressions", {})[first_var_id] = {
        "opcode": "neg",
        "operands": [{"varRef": first_var_id}],
    }
    errs = validate.cross_pool_collision_errors(doc)
    assert any(first_var_id in e and "expressions" in e and "variables" in e
               for e in errs)
    _ = first_var


def test_validate_or_exit_errors_on_cross_pool_collision(capsys):
    doc = _minimal_valid_doc()
    first_var_id = next(iter(doc["variables"]))
    doc.setdefault("expressions", {})[first_var_id] = {
        "opcode": "neg",
        "operands": [{"varRef": first_var_id}],
    }
    rc = validate.validate_or_exit(doc, pathlib.Path("d.uhdi.json"))
    captured = capsys.readouterr()
    assert rc == 1
    assert "error: cross-pool id collision" in captured.err
    assert first_var_id in captured.err


def test_validate_or_exit_schema_violation_dominates_collision(capsys):
    """Schema-level violations (rc=2) outrank cross-pool collisions (rc=1)."""
    doc = _minimal_valid_doc()
    first_var_id = next(iter(doc["variables"]))
    doc.setdefault("expressions", {})[first_var_id] = {
        "opcode": "neg",
        "operands": [{"varRef": first_var_id}],
    }
    doc["format"] = {"name": "uhdi"}  # missing required `version` -> schema fail
    rc = validate.validate_or_exit(doc, pathlib.Path("d.uhdi.json"))
    captured = capsys.readouterr()
    assert rc == 2
    assert "error: cross-pool id collision" in captured.err
    assert "schema violation" in captured.err


# ---- schema if/then/else (F-U1.6, F-U1.8) -----------------------------------


def test_iter_errors_flags_direction_on_non_port_variable():
    """Schema rejects `direction` when bindKind != 'port' (F-U1.6, spec §6.6 inv 2)."""
    doc = _minimal_valid_doc()
    # var_Counter_r has bindKind='reg' — add direction to make it invalid
    doc["variables"]["var_Counter_r"]["direction"] = "input"
    errs = list(validate.iter_errors(doc))
    assert any("direction" in e.message or "direction" in str(e.absolute_path)
               for e in errs)


def test_iter_errors_flags_missing_direction_on_port():
    """Schema requires `direction` when bindKind == 'port' (F-U1.6, spec §6.6 inv 2)."""
    doc = _minimal_valid_doc()
    # var_Counter_clock is a port — remove its direction
    doc["variables"]["var_Counter_clock"].pop("direction", None)
    errs = list(validate.iter_errors(doc))
    assert any("direction" in e.message for e in errs)


def test_iter_errors_flags_fieldname_on_non_dot_opcode():
    """Schema rejects `fieldName` when opcode != '.' (F-U1.8, spec §5.6 inv 5)."""
    doc = _minimal_valid_doc()
    doc["expressions"]["bad_expr"] = {
        "opcode": "+",
        "operands": [],
        "fieldName": "x",
    }
    errs = list(validate.iter_errors(doc))
    assert any("fieldName" in e.message or "fieldName" in str(e.absolute_path)
               for e in errs)


def test_iter_errors_accepts_fieldname_on_dot_opcode():
    """Schema allows `fieldName` on opcode '.' (F-U1.8, spec §5.6 inv 5)."""
    doc = _minimal_valid_doc()
    doc["expressions"]["dot_expr"] = {
        "opcode": ".",
        "operands": [{"varRef": "var_Counter_r"}],
        "fieldName": "valid",
    }
    errs = list(validate.iter_errors(doc))
    assert not any(
        ("fieldName" in e.message and "not allowed" in e.message.lower())
        for e in errs
    )


# ---- enum_width_errors ----------------------------------------------------


def test_enum_width_errors_empty_for_clean_doc():
    assert validate.enum_width_errors(_minimal_valid_doc()) == []


def test_enum_width_errors_flags_uint_overflow():
    """Variant key exceeds 2^width - 1 for uint<W> (F-U1.9, spec §4.4 inv 4)."""
    doc = _minimal_valid_doc()
    doc["types"]["uint2"] = {"kind": "uint", "width": 2}
    doc["types"]["MyEnum"] = {
        "kind": "enum",
        "underlyingTypeRef": "uint2",
        "variants": {"0": "A", "3": "OK", "99": "BIG"},
    }
    errs = validate.enum_width_errors(doc)
    assert any("99" in e and "out of range" in e for e in errs)
    assert not any("'0'" in e for e in errs)
    assert not any("'3'" in e for e in errs)


def test_enum_width_errors_flags_negative_on_uint():
    """Negative variant key invalid for uint (F-U1.9)."""
    doc = _minimal_valid_doc()
    doc["types"]["uint4"] = {"kind": "uint", "width": 4}
    doc["types"]["MyEnum"] = {
        "kind": "enum",
        "underlyingTypeRef": "uint4",
        "variants": {"-1": "NEG"},
    }
    errs = validate.enum_width_errors(doc)
    assert any("-1" in e and "out of range" in e for e in errs)


def test_enum_width_errors_flags_sint_overflow():
    """Variant key outside [-2^(W-1), 2^(W-1)-1] for sint<W> (F-U1.9)."""
    doc = _minimal_valid_doc()
    doc["types"]["sint4"] = {"kind": "sint", "width": 4}
    doc["types"]["MyEnum"] = {
        "kind": "enum",
        "underlyingTypeRef": "sint4",
        "variants": {"-8": "MIN", "7": "MAX", "8": "TOO_BIG"},
    }
    errs = validate.enum_width_errors(doc)
    assert any("'8'" in e and "out of range" in e for e in errs)
    assert not any("'-8'" in e for e in errs)
    assert not any("'7'" in e for e in errs)


def test_enum_width_errors_flags_non_ground_underlying():
    """Underlying must be uint or sint (F-U1.9 / spec §4.4 inv 3)."""
    doc = _minimal_valid_doc()
    doc["types"]["MyStruct"] = {"kind": "struct", "members": []}
    doc["types"]["MyEnum"] = {
        "kind": "enum",
        "underlyingTypeRef": "MyStruct",
        "variants": {"0": "A"},
    }
    errs = validate.enum_width_errors(doc)
    assert any("MyStruct" in e and "must be uint or sint" in e for e in errs)


def test_validate_or_exit_warns_on_enum_overflow(capsys):
    doc = _minimal_valid_doc()
    doc["types"]["uint2"] = {"kind": "uint", "width": 2}
    doc["types"]["MyEnum"] = {
        "kind": "enum",
        "underlyingTypeRef": "uint2",
        "variants": {"99": "BIG"},
    }
    rc = validate.validate_or_exit(doc, pathlib.Path("d.uhdi.json"))
    captured = capsys.readouterr()
    assert rc == 0
    assert "warning" in captured.err
    assert "enum invariant" in captured.err
    assert "99" in captured.err


def test_validate_or_exit_warns_on_dangling_refs_without_failing(capsys):
    doc = _minimal_valid_doc()
    doc["top"].append("ghost_scope")
    rc = validate.validate_or_exit(doc, pathlib.Path("d.uhdi.json"))
    captured = capsys.readouterr()
    assert rc == 0
    assert "warning" in captured.err
    assert "ghost_scope" in captured.err
