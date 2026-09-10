"""CLI tests for `uhdi2`'s `downgrade` subcommand and for the existing
`uhdi-to-hgdb`/`uhdi-to-pdg` CLIs accepting a v2 file transparently (via
`uhdi_common.cli.main_for_backend`, the same harness `test_cli.py` covers)."""
from __future__ import annotations

import json
import pathlib

from uhdi2.__main__ import main as uhdi2_main
from uhdi_common.backend import discover, get
from uhdi_common.cli import main_for_backend

_REPO = pathlib.Path(__file__).resolve().parent.parent
_FIXTURE = _REPO / "test" / "fixtures" / "uhdi" / "bundle_io.uhdi.json"

discover()


def _v2_fixture(tmp_path: pathlib.Path) -> pathlib.Path:
    """`bundle_io.uhdi.json` upgraded to v2, written to a temp file."""
    from uhdi2.upgrade import upgrade
    doc_v2 = upgrade(json.loads(_FIXTURE.read_text(encoding="utf-8")))
    out = tmp_path / "bundle_io.v2.json"
    out.write_text(json.dumps(doc_v2), encoding="utf-8")
    return out


def test_downgrade_cli_writes_a_v1_document(tmp_path, capsys):
    v2_path = _v2_fixture(tmp_path)
    out_path = tmp_path / "bundle_io.v1.json"
    rc = uhdi2_main(["downgrade", str(v2_path), "-o", str(out_path)])
    assert rc == 0
    doc_v1 = json.loads(out_path.read_text(encoding="utf-8"))
    assert doc_v1["format"] == {"name": "uhdi", "version": "1.0"}
    assert "variables" in doc_v1 and "scopes" in doc_v1


def test_downgrade_cli_writes_to_stdout_by_default(tmp_path, capsys):
    v2_path = _v2_fixture(tmp_path)
    rc = uhdi2_main(["downgrade", str(v2_path)])
    assert rc == 0
    doc_v1 = json.loads(capsys.readouterr().out)
    assert doc_v1["format"]["version"] == "1.0"


def test_hgdb_cli_accepts_a_v2_file(tmp_path, capsys):
    v2_path = _v2_fixture(tmp_path)
    out_path = tmp_path / "bundle_io.db"
    rc = main_for_backend(get("hgdb"), [str(v2_path), "-o", str(out_path)])
    assert rc == 0
    assert out_path.is_file()


def test_pdg_cli_accepts_a_v2_file(tmp_path, capsys):
    v2_path = _v2_fixture(tmp_path)
    rc = main_for_backend(get("pdg"), [str(v2_path)])
    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    assert "vertices" in result
