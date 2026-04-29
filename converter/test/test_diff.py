"""Unit tests for uhdi_common.diff."""
from __future__ import annotations

from uhdi_common.diff import diff_dicts, format_deltas


def test_type_mismatch_at_root():
    deltas = diff_dicts({"a": 1}, [1, 2, 3])
    assert len(deltas) == 1
    p, kind, _, _ = deltas[0]
    assert kind == "type"
    assert p == "/"


def test_type_mismatch_in_subtree():
    deltas = diff_dicts({"a": 1}, {"a": "1"})
    assert any(d[1] == "type" for d in deltas)


def test_strict_flags_extra_keys_in_actual():
    deltas = diff_dicts({"a": 1, "b": 2}, {"a": 1})
    assert deltas == [("/b", "extra", 2, None)]


def test_strict_flags_missing_keys_in_actual():
    deltas = diff_dicts({"a": 1}, {"a": 1, "b": 2})
    assert deltas == [("/b", "missing", None, 2)]


def test_strict_flags_value_mismatch():
    deltas = diff_dicts({"a": 1}, {"a": 2})
    assert deltas == [("/a", "value", 1, 2)]


def test_strict_passes_for_equal_dicts():
    assert diff_dicts({"a": 1, "b": [1, 2]}, {"a": 1, "b": [1, 2]}) == []


def test_strict_flags_length_mismatch():
    deltas = diff_dicts([1, 2, 3], [1, 2])
    assert ("/", "length", 3, 2) in deltas


def test_strict_walks_overlapping_list_indices():
    deltas = diff_dicts([1, 99, 3], [1, 2, 3])
    assert ("/1", "value", 99, 2) in deltas


def test_superset_ignores_extra_keys_in_actual():
    deltas = diff_dicts({"a": 1, "b": 2}, {"a": 1}, mode="superset")
    assert deltas == []


def test_superset_still_flags_missing_keys():
    deltas = diff_dicts({"a": 1}, {"a": 1, "b": 2}, mode="superset")
    assert deltas == [("/b", "missing", None, 2)]


def test_superset_flags_value_mismatch_on_overlap():
    deltas = diff_dicts({"a": 1}, {"a": 2}, mode="superset")
    assert deltas == [("/a", "value", 1, 2)]


def test_superset_list_passes_when_actual_contains_expected():
    deltas = diff_dicts([3, 1, 2, 4], [1, 2, 3], mode="superset")
    assert deltas == []


def test_superset_list_flags_missing_element():
    deltas = diff_dicts([1, 2], [1, 2, 99], mode="superset")
    assert deltas == [("/2", "missing", None, 99)]


def test_superset_list_preserves_multiplicity():
    # [1,1] expected vs [1] actual flags the second copy missing; a
    # set-check would silently accept it.
    deltas = diff_dicts([1], [1, 1], mode="superset")
    assert deltas == [("/1", "missing", None, 1)]


def test_superset_list_consumes_each_actual_only_once():
    deltas = diff_dicts([2, 1, 2], [2, 2, 2], mode="superset")
    assert deltas == [("/2", "missing", None, 2)]


def test_superset_list_uses_value_equality_for_dicts():
    # Dicts compared via JSON-canonical (sort_keys) so insertion order
    # doesn't matter.
    actual = [{"b": 2, "a": 1}, {"x": 5}]
    expected = [{"a": 1, "b": 2}]
    assert diff_dicts(actual, expected, mode="superset") == []


def test_format_deltas_renders_count_header():
    out = format_deltas([("/a", "value", 1, 2)])
    assert out.startswith("1 structural delta(s):")


def test_format_deltas_truncates_long_values():
    big = "X" * 500
    out = format_deltas([("/a", "value", big, big)], max_value_chars=20)
    assert "..." in out
    assert "X" * 50 not in out


def test_format_deltas_caps_row_count():
    deltas = [(f"/{i}", "value", i, i + 1) for i in range(60)]
    out = format_deltas(deltas, max_rows=10)
    assert "more)" in out
    assert "60 structural" in out


def test_format_deltas_handles_none_actual():
    # `None` rendered as literal "None" (not JSON null) to distinguish
    # "absent" from "explicit null".
    out = format_deltas([("/a", "missing", None, "x")])
    assert "actual=None" in out


def test_format_deltas_handles_none_expected():
    out = format_deltas([("/a", "extra", "x", None)])
    assert "expected=None" in out
