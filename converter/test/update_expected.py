#!/usr/bin/env python3
"""Re-seed golden expected files for one or more (backend, fixture).

    test/update_expected.py hgldd test/fixtures/uhdi/counter.uhdi.json
    test/update_expected.py hgldd --all
    test/update_expected.py --all
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import tempfile
from typing import List

# Make `from uhdi_common import ...` work without pyproject's pythonpath.
_REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))

from uhdi_common.backend import Backend, all_backends, discover, get  # noqa: E402

_FIXTURES = _REPO / "test" / "fixtures" / "uhdi"
_EXPECTED = _REPO / "test" / "fixtures" / "expected"


def _expected_path(backend: Backend, fixture: pathlib.Path) -> pathlib.Path:
    stem = fixture.name.replace(".uhdi.json", "")
    return _EXPECTED / backend.name / f"{stem}.{backend.output_extension}.json"


def _convert(backend: Backend, fixture: pathlib.Path):
    uhdi = json.loads(fixture.read_text(encoding="utf-8"))
    if backend.binary_output:
        with tempfile.NamedTemporaryFile(suffix=f".{backend.output_extension}",
                                         delete=False) as tmp:
            tmp_path = pathlib.Path(tmp.name)
        try:
            backend.convert(uhdi, tmp_path)
            # Inline dispatch keeps this script usable outside a pytest invocation.
            if backend.name == "hgdb":
                from uhdi_to_hgdb.dump import canonical_dump
                return canonical_dump(tmp_path)
            raise NotImplementedError(
                f"no canonical-dump helper for binary backend {backend.name!r}")
        finally:
            tmp_path.unlink(missing_ok=True)
    return backend.convert(uhdi, None)


def _seed(backend: Backend, fixture: pathlib.Path) -> pathlib.Path:
    out = _expected_path(backend, fixture)
    out.parent.mkdir(parents=True, exist_ok=True)
    actual = _convert(backend, fixture)
    out.write_text(
        json.dumps(actual, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    return out


def _resolve_fixtures(args_fixture: pathlib.Path | None,
                      all_flag: bool) -> List[pathlib.Path]:
    if args_fixture is not None:
        if not args_fixture.is_file():
            raise SystemExit(f"fixture not found: {args_fixture}")
        return [args_fixture]
    if all_flag:
        return sorted(_FIXTURES.glob("*.uhdi.json"))
    raise SystemExit("specify a fixture path or pass --all")


def _resolve_backends(args_backend: str | None) -> List[Backend]:
    if args_backend is not None:
        return [get(args_backend)]
    return all_backends()


def main(argv) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "backend", nargs="?",
        help="registered backend name; omit to seed every backend.")
    p.add_argument(
        "fixture", nargs="?", type=pathlib.Path,
        help="path to a single uhdi input; omit (with --all) to seed "
             "every fixture.")
    p.add_argument(
        "--all", action="store_true",
        help="seed every fixture (combined with `backend` -> all "
             "fixtures for that backend; without `backend` -> every "
             "(backend, fixture) cell).")
    args = p.parse_args(argv)

    discover()
    backends = _resolve_backends(args.backend)
    fixtures = _resolve_fixtures(args.fixture, args.all)

    seeded = 0
    for backend in backends:
        for fixture in fixtures:
            out = _seed(backend, fixture)
            print(f"  seeded {out.relative_to(_REPO)}")
            seeded += 1
    print(f"seeded {seeded} expected file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
