"""Semantic check of two hgdb symbol tables: simulate each table's
Verilog under the same random stimulus and compare, per breakpoint
(instance, line, col), the cycles in which its condition holds, and per
generator variable, the value it shows each cycle.

usage: hgdb_sim.py TOP NATIVE.v NATIVE.json OURS.sv OURS.json [CYCLES]
                   [LEGACY.fir]
NATIVE.json is a canonical dump of the hgdb-firrtl table; OURS.json is a
canonical dump of ours or an hgdb JSON table. LEGACY.fir gives the node
definitions hgdb-firrtl conditions name but its Verilog inlines."""
import json
import pathlib
import re
import subprocess
import sys
import tempfile


def ports(sv: str, top: str):
    m = re.search(rf"module\s+{top}\s*\((.*?)\);", sv, re.S)
    body = re.sub(r"//[^\n]*", "", m.group(1))
    out, d, w = [], "", ""
    for item in body.split(","):
        t = re.match(r"\s*(input|output)?\s*(?:wire|reg|logic)?\s*(\[[^\]]+\])?\s*(\w+)\s*$",
                     item)
        if not t:
            continue
        if t.group(1):
            d, w = t.group(1), t.group(2) or ""
        out.append((d, w, t.group(3)))
    return out


def testbench(top: str, ps, cycles: int) -> str:
    decl, conn, drive = [], [], []
    for d, w, n in ps:
        decl.append(f"  {'reg' if d == 'input' else 'wire'} {w} {n};")
        conn.append(f".{n}({n})")
        if d == "input" and n not in ("clock", "reset"):
            drive.append(f"      {n} = $urandom;")
    return f"""module tb;
{chr(10).join(decl)}
  {top} dut({', '.join(conn)});
  integer i;
  initial begin
    void'($urandom(42));
    $dumpfile("dump.vcd");
    $dumpvars(0, tb);
    clock = 0; reset = 1;
{chr(10).join(drive)}
    for (i = 0; i < {cycles}; i = i + 1) begin
      #5 clock = 1;
      #5 clock = 0;
      reset = (i < 2) || ($urandom % 16 == 0);
{chr(10).join(drive)}
    end
    $finish;
  end
endmodule
"""


def simulate(top, verilog: pathlib.Path, cycles, work: pathlib.Path):
    ps = ports(verilog.read_text(), top)
    tb = work / "tb.sv"
    tb.write_text(testbench(top, ps, cycles))
    p = subprocess.run(["verilator", "--binary", "-Wno-fatal", "-Wno-lint", "-DSTOP_COND=0", "-DASSERT_VERBOSE_COND=0",
                    "--trace", "--trace-underscore", "--top-module", "tb",
                    "-Mdir", str(work / "obj"), str(tb), str(verilog)],
                   capture_output=True, cwd=work, text=True)
    if not (work / "obj" / "Vtb").exists():
        sys.exit(f"verilator failed on {verilog}:\n{p.stderr[-3000:]}")
    subprocess.run([str(work / "obj" / "Vtb")], check=True,
                   capture_output=True, cwd=work)
    return parse_vcd((work / "dump.vcd").read_text())


def parse_vcd(text: str):
    """Values of every signal sampled just before each rising clock edge
    of tb.clock: [{"tb.dut.x": int, ...}, ...]."""
    ids, scope = {}, []
    lines = iter(text.splitlines())
    for line in lines:
        tok = line.split()
        if not tok:
            continue
        if tok[0] == "$scope":
            scope.append(tok[2])
        elif tok[0] == "$upscope":
            scope.pop()
        elif tok[0] == "$var":
            name = ".".join([s for s in scope if s != "TOP"] + [tok[4]])
            ids.setdefault(tok[3], []).append(name)
        elif tok[0] == "$enddefinitions":
            break
    clock_id = next(i for i, ns in ids.items() if "tb.clock" in ns)
    cur, samples, pending = {}, [], {}

    def apply(changes):
        for i, v in changes.items():
            for n in ids.get(i, []):
                cur[n] = v
    for line in lines:
        if not line or line.startswith("$"):
            continue
        if line[0] == "#":
            # Commit a time step; a rising clock samples the old values.
            if pending.get(clock_id) == 1 and cur.get("tb.clock") == 0:
                samples.append(dict(cur))
            apply(pending)
            pending = {}
            continue
        if line[0] in "01xz":
            pending[line[1:]] = 1 if line[0] == "1" else 0
        elif line[0] == "b":
            bits, i = line[1:].split()
            pending[i] = int(re.sub("[xz]", "0", bits), 2)
    return samples


_PRIMS = {
    "gt": lambda a, b: int(a > b), "lt": lambda a, b: int(a < b),
    "geq": lambda a, b: int(a >= b), "leq": lambda a, b: int(a <= b),
    "eq": lambda a, b: int(a == b), "neq": lambda a, b: int(a != b),
    "and": lambda a, b: a & b, "or": lambda a, b: a | b,
    "xor": lambda a, b: a ^ b, "not": lambda a: int(not a),
}
NODES = {}   # scope -> {node name: FIRRTL expression}


def load_nodes(fir: str, top: str):
    """Node definitions of the legacy FIRRTL, by VCD scope of each instance."""
    mods, cur = {}, None
    for line in fir.splitlines():
        if m := re.match(r"\s*module (\w+)", line):
            cur = mods.setdefault(m.group(1), {"nodes": {}, "insts": []})
        elif cur and (m := re.match(r"\s*node (\w+) = (.*?)\s*(@\[.*)?$", line)):
            cur["nodes"][m.group(1)] = m.group(2)
        elif cur and (m := re.match(r"\s*inst (\w+) of (\w+)", line)):
            cur["insts"].append((m.group(1), m.group(2)))
    def visit(scope, mod):
        NODES[scope] = mods.get(mod, {}).get("nodes", {})
        for i, m in mods.get(mod, {}).get("insts", []):
            visit(f"{scope}.{i}", m)
    visit("tb.dut", top)


def fir_eval(expr: str, scope: str, values):
    expr = expr.strip()
    if m := re.fullmatch(r'UInt<\d+>\("h([0-9a-fA-F]+)"\)', expr):
        return int(m.group(1), 16)
    if m := re.fullmatch(r"(\w+)\((.*)\)", expr):
        args, depth, start = [], 0, 0
        for i, ch in enumerate(m.group(2)):
            depth += ch == "("
            depth -= ch == ")"
            if ch == "," and depth == 0:
                args.append(m.group(2)[start:i]); start = i + 1
        args.append(m.group(2)[start:])
        return _PRIMS[m.group(1)](*(fir_eval(a, scope, values) for a in args))
    return lookup(expr.replace(".", "_"), scope, values)


def lookup(name: str, scope: str, values):
    key = f"{scope}.{name}"
    if key in values:
        return values[key]
    if name in NODES.get(scope, {}):
        return fir_eval(NODES[scope][name], scope, values)
    # FIRRTL 1.x renames a Verilog keyword (`reg` -> `reg_`) in the Verilog
    # but not in the table.
    if NODES and f"{key}_" in values:
        return values[f"{key}_"]
    raise KeyError(key)


def evaluate(cond: str, scope: str, values) -> bool:
    expr = cond.replace("&&", " and ").replace("||", " or ")
    expr = re.sub(r"!(?!=)", " not ", expr)

    def sig(m):
        name = m.group(0)
        if name in ("and", "or", "not") or name[0].isdigit():
            return name
        return str(lookup(name, scope, values))
    expr = re.sub(r"[A-Za-z_][\w$]*", sig, expr)
    return bool(eval(expr))


def fires(dump, samples, top):
    out, errors = {}, set()
    for b in dump["breakpoint"]:
        key = (b["instance_name"], b["line_num"], b["column_num"])
        scope = "tb.dut" + b["instance_name"][len(top):]
        hits = set()
        for c, vals in enumerate(samples):
            try:
                if evaluate(b["condition"], scope, vals):
                    hits.add(c)
            except KeyError as e:
                errors.add(f"{key}: {e}")
                break
        out.setdefault(key, set()).update(hits)
    return out, errors


def values(dump, samples, top):
    out = {}
    for g in dump["generator_variable"]:
        scope = "tb.dut" + g["instance_name"][len(top):]
        key = f"{scope}.{g['variable_value']}"
        out[(g["instance_name"], g["name"])] = [s.get(key) for s in samples]
    return out


def from_hgdb_json(doc):
    """The breakpoints and generator variables hgdb's JSON provider
    derives from a JSON table: a decl / assign gets the conjunction of
    the conditions of its enclosing blocks, innermost first."""
    mods = {m["name"]: m for m in doc["table"]}
    bps, gens = [], []

    def scope(entries, filename, conds, inst):
        for e in entries:
            fn = e.get("filename", filename)
            if e["type"] == "block":
                c = [e["condition"]] if e.get("condition") else []
                scope(e.get("scope", []), fn, c + conds, inst)
            else:
                own = [e["condition"]] if e.get("condition") else []
                bps.append({"instance_name": inst, "filename": fn,
                            "line_num": e.get("line", 0),
                            "column_num": e.get("column", 0),
                            "condition": " && ".join(own + conds) or "1"})

    def visit(name, path):
        m = mods[name]
        for v in m["variables"]:
            if isinstance(v, dict):
                gens.append({"instance_name": path, "name": v["name"],
                             "variable_value": v["value"]})
        scope(m.get("scope", []), "", [], path)
        for i in m.get("instances", []):
            visit(i["module"], f"{path}.{i['name']}")
    tops = doc["top"] if isinstance(doc["top"], list) else [doc["top"]]
    for t in tops:
        visit(t, t)
    return {"breakpoint": bps, "generator_variable": gens}


def main():
    top, nv, nj, ov, oj = sys.argv[1:6]
    cycles = int(sys.argv[6]) if len(sys.argv) > 6 else 300
    if len(sys.argv) > 7:
        load_nodes(pathlib.Path(sys.argv[7]).read_text(), top)
    with tempfile.TemporaryDirectory(dir=pathlib.Path(__file__).parent) as t:
        t = pathlib.Path(t)
        (t / "n").mkdir(); (t / "o").mkdir()
        ns = simulate(top, pathlib.Path(nv).resolve(), cycles, t / "n")
        os_ = simulate(top, pathlib.Path(ov).resolve(), cycles, t / "o")
    nd, od = json.load(open(nj)), json.load(open(oj))
    if "table" in od:
        od = from_hgdb_json(od)
    nf, ne = fires(nd, ns, top)
    # Our conditions must name signals of our own Verilog.
    NODES.clear()
    of, oe = fires(od, os_, top)
    same = diff = 0
    for k in sorted(set(nf) | set(of)):
        if k not in of or k not in nf:
            print(f"  only {'native' if k in nf else 'ours'}: {k}")
        elif nf[k] == of[k]:
            same += 1
        else:
            diff += 1
            print(f"  DIFF {k}: native {len(nf[k])} hits, ours {len(of[k])}, "
                  f"first mismatch cycle {min(nf[k] ^ of[k])}")
    nvals, ovals = values(nd, ns, top), values(od, os_, top)
    vsame = vdiff = 0
    for k in sorted(set(nvals) & set(ovals)):
        if nvals[k] == ovals[k]:
            vsame += 1
        else:
            vdiff += 1
            c = next(i for i, (a, b) in enumerate(zip(nvals[k], ovals[k])) if a != b)
            print(f"  VALUE DIFF {k}: cycle {c} native {nvals[k][c]} ours {ovals[k][c]}")
    for e in sorted(ne | oe):
        print("  ERROR", e)
    print(f"breakpoints: {same} same, {diff} differ; variables: {vsame} same, "
          f"{vdiff} differ; cycles {len(ns)}/{len(os_)}")


if __name__ == "__main__":
    main()
