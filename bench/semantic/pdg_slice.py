"""Semantic check of two ChiselTrace PDGs: simulate each one's Verilog
under the same stimulus, run `chiseltrace-cli dyn-slice` for every
criterion signal on both, and compare the source statements in the
slices. Native lines are mapped onto the fixture by line text (the
native sidecar repeats the design at other line numbers).

NATIVE.sv is what the chiseltrace fork emits with `--target
systemverilog`: its `pred_*` / `probe_*` signals exist only there.

usage: slice.py TOP NATIVE.sv NATIVE.pdg.json NATIVE.scala
                OURS.sv OURS.pdg.json OURS.scala [CYCLES]"""
import difflib
import json
import os
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import hgdb_sim as sim  # noqa: E402

CLI = os.environ.get("CHISELTRACE_CLI", str(
    pathlib.Path(__file__).resolve().parents[3]
    / "chiseltrace/target/release/chiseltrace-cli"))
VERBOSE = bool(os.environ.get("V"))


def vcd(top, verilog, cycles, work):
    work.mkdir()
    sim.simulate(top, verilog, cycles, work)
    return work / "dump.vcd"


def line_map(native_scala, ours_scala):
    a = [l.strip() for l in pathlib.Path(native_scala).read_text().splitlines()]
    b = [l.strip() for l in pathlib.Path(ours_scala).read_text().splitlines()]
    out = {}
    for blk in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks():
        for k in range(blk.size):
            out[blk.a + k + 1] = blk.b + k + 1
    return out


def dyn_slice(pdg, vcd_path, signal, work):
    out = work / f"slice-{abs(hash((str(pdg), signal)))}.json"
    p = subprocess.run([CLI, "dyn-slice", str(pdg), str(vcd_path),
                        f"signal:{signal}", "--extra-scopes", "TOP", "tb",
                        "dut", "--output-path", str(out)],
                       capture_output=True, text=True)
    if not out.exists():
        return None, (p.stdout + p.stderr).strip().splitlines()[-1:]
    return json.loads(out.read_text())["statements"], None


def main():
    top, nsv, npdg, nscala, osv, opdg, oscala = sys.argv[1:8]
    cycles = int(sys.argv[8]) if len(sys.argv) > 8 else 100
    lmap = line_map(nscala, oscala)
    with tempfile.TemporaryDirectory(dir=pathlib.Path(__file__).parent) as t:
        t = pathlib.Path(t)
        nv = vcd(top, pathlib.Path(nsv).resolve(), cycles, t / "n")
        ov = vcd(top, pathlib.Path(osv).resolve(), cycles, t / "o")
        nd, od = json.load(open(npdg)), json.load(open(opdg))
        # Criteria: what the source writes, as both graphs name it.
        osyms = {v["assignsTo"] for v in od["vertices"]
                 if v["kind"] == "Connection" and v["assignsTo"]}
        nsyms = {v["assignsTo"] for v in nd["vertices"]
                 if v["kind"] == "Connection" and v["assignsTo"]}
        same = 0
        for sym in sorted(osyms):
            if sym not in nsyms:
                print(f"  {sym}: no native statement writes it")
                continue
            ns, ne = dyn_slice(npdg, nv, sym, t)
            os_, oe = dyn_slice(opdg, ov, sym, t)
            if ne or oe:
                print(f"  {sym}: native error {ne}, ours error {oe}")
                continue
            nl = {lmap.get(s["line"], -s["line"]) for s in ns
                  if s.get("isChiselStatement", True) and s["line"]}
            ol = {s["line"] for s in os_ if s["line"]}
            if nl == ol:
                same += 1
                print(f"  {sym}: same {sorted(ol)}")
            else:
                print(f"  {sym}: DIFF only native {sorted(nl - ol)}, "
                      f"only ours {sorted(ol - nl)}")
                if VERBOSE:
                    for s in sorted(ns, key=lambda s: s["line"]):
                        print(f"      N {lmap.get(s['line'], -s['line'])} {s}")
                    for s in sorted(os_, key=lambda s: s["line"]):
                        print(f"      O {s['line']} {s}")
        print(f"slices: {same}/{len(osyms)} same")


if __name__ == "__main__":
    main()
