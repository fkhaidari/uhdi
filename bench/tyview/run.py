"""What surfer-tywaves shows: native HGLDD vs ours (UHDI -> HGLDD).

Per fixture: build both Verilogs, simulate each under Verilator with the
same stimulus, let tywaves-rs (via tydump) build its typed view and
rewrite the VCD, then compare the typed variables by source path.
usage: run.py [<fixture-stem>...]   (default: FIXTURES below)
See README.md for the toolchain and how to read the report.
"""
import hashlib, json, os, pathlib, re, shutil, subprocess, sys

HERE = pathlib.Path(__file__).resolve().parent
BENCH = HERE.parent
sys.path[:0] = [str(BENCH / 'src')]
# The Chisel that emits the handle-tree debug intrinsics. The rest of the
# bench still runs the older `uhdi` pipeline, so the default lives here.
CHISEL = '7.16.0+37-0422e52a-SNAPSHOT'
os.environ.setdefault('UHDI_BENCH_UHDI_CHISEL', f'org.chipsalliance::chisel:{CHISEL}')
os.environ.setdefault('UHDI_BENCH_UHDI_PLUGIN', f'org.chipsalliance:::chisel-plugin:{CHISEL}')
from uhdi_bench.compile import compile_for, get
from uhdi_bench.runner import discover_toolchain
from uhdi_common.backend import discover, get as backend

FIX = BENCH / 'fixtures'
TYDUMP = pathlib.Path(os.environ.get('TYDUMP', HERE / 'tydump/target/release/tydump'))
WORK = pathlib.Path(os.environ.get('TYVIEW_WORK', BENCH / '.cache/tyview'))
FIXTURES = ['Counter', 'GCD', 'Fifo', 'HgdbTortureLite', 'TrafficLight', 'TywavesTorture', 'HierInline']
CYCLES = 40


def sh(cmd, **kw):
    p = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if p.returncode:
        raise RuntimeError(f"{cmd[0]} failed:\n{p.stderr[-2000:]}")
    return p


def ports(sv, top):
    m = re.search(r'\bmodule\s+%s\s*\((.*?)\n\);' % top, sv, re.S)
    body = re.sub(r'//[^\n]*', '', m.group(1))
    out, d, w = [], None, 1
    for item in body.split(','):
        t = re.match(r'\s*(?:(input|output)\s+)?(?:wire\s+|logic\s+)?(?:\[(\d+):0\]\s*)?(\w+)\s*$', item)
        if not t:
            continue
        if t.group(1):
            d, w = t.group(1), int(t.group(2)) + 1 if t.group(2) else 1
        elif t.group(2):
            w = int(t.group(2)) + 1
        out.append((d, w, t.group(3)))
    return out


def testbench(top, ins):
    sets = []
    for _, w, n in ins:
        if n in ('clock', 'reset') or w > 64:
            continue
        salt = int(hashlib.sha1(n.encode()).hexdigest()[:8], 16)
        mask = (1 << w) - 1
        sets.append(f'    top.{n} = (uint64_t)mix(c * 1000003ull + {salt}ull) & {mask}ull;')
    rst = '    top.reset = c < 2;' if any(n == 'reset' for _, _, n in ins) else ''
    return f'''#include "V{top}.h"
#include "verilated.h"
#include "verilated_vcd_c.h"
static uint64_t mix(uint64_t x) {{ x += 0x9e3779b97f4a7c15ull; x = (x ^ (x >> 30)) * 0xbf58476d1ce4e5b9ull;
  x = (x ^ (x >> 27)) * 0x94d049bb133111ebull; return x ^ (x >> 31); }}
int main(int argc, char** argv) {{
  VerilatedContext ctx; ctx.traceEverOn(true);
  V{top} top{{&ctx}};
  VerilatedVcdC tfp; top.trace(&tfp, 99); tfp.open(argv[1]);
  uint64_t t = 0;
  for (uint64_t c = 0; c < {CYCLES}; ++c) {{
{rst}
{chr(10).join(sets)}
    top.clock = 0; top.eval(); tfp.dump(t++);
    top.clock = 1; top.eval(); tfp.dump(t++);
  }}
  tfp.close();
}}
'''


def simulate(svs, top, tb, work):
    work.mkdir(parents=True, exist_ok=True)
    (work / 'tb.cpp').write_text(tb)
    sh(['verilator', '--cc', '--exe', '--build', '--trace', '--trace-underscore', '-Wno-fatal', '-Wno-lint',
        '-Wno-style', '--top-module', top, '-Mdir', str(work / 'obj'),
        *map(str, svs), str(work / 'tb.cpp')], cwd=work)
    sh([str(work / 'obj' / f'V{top}'), str(work / 'sim.vcd')], cwd=work)
    return work / 'sim.vcd'


def read_vcd(path):
    """[(scope path, ref)] in header order, and id -> [(t, value)]."""
    decl, ids, scope, changes, t = [], [], [], {}, 0
    text = path.read_text()
    head, _, body = text.partition('$enddefinitions')
    for tok in re.finditer(r'\$scope\s+\w+\s+(\S+)\s+\$end|\$upscope\s+\$end|\$var\s+\w+\s+\d+\s+(\S+)\s+(\S+)(?:\s+\[[^\]]*\])?\s+\$end', head):
        if tok.group(1):
            scope.append(tok.group(1))
        elif tok.group(2):
            decl.append(('.'.join(scope), tok.group(3)))
            ids.append(tok.group(2))
        else:
            scope.pop()
    for line in body.split('\n')[1:]:
        line = line.strip()
        if not line or line.startswith('$'):
            continue
        if line[0] == '#':
            t = int(line[1:])
        elif line[0] in 'bB':
            v, i = line[1:].split()
            changes.setdefault(i, []).append((t, v))
        else:
            changes.setdefault(line[1:], []).append((t, line[0]))
    return decl, ids, changes


def typed_view(dd_dir, vcd, top, out):
    p = subprocess.run([str(TYDUMP), str(dd_dir), str(vcd), top, 'TOP,' + top, str(out)],
                       capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError(p.stderr[-2000:])
    rows = [l.split('\t') for l in pathlib.Path(f'{out}.vars.tsv').read_text().splitlines()]
    decl, ids, changes = read_vcd(pathlib.Path(f'{out}.vcd'))
    assert len(rows) == len(ids), (len(rows), len(ids))
    view = {}
    for r, i in zip(rows, ids):
        path, name, tname, params, kind, enum, trace = r
        seq = changes.get(i, [])
        view[f'{path}.{name}'] = dict(type=tname, params=params, kind=kind, enum=enum,
                                     trace=trace, values=seq)
    return view, p.stderr.strip()


def at(seq, times):
    out, j, cur = [], 0, None
    for t in times:
        while j < len(seq) and seq[j][0] <= t:
            cur = seq[j][1].lstrip('0') or '0' if seq[j][1] not in 'xz' else seq[j][1]
            j += 1
        out.append(cur)
    return out


def run(stem, tc, firtool, report):
    scala = FIX / f'{stem}.scala'
    fir = compile_for(scala, get('uhdi'))
    tfir = compile_for(scala, get('tywaves'))
    work = WORK / stem
    shutil.rmtree(work, ignore_errors=True)
    (work / 'ours/dd').mkdir(parents=True)
    (work / 'native/dd').mkdir(parents=True)
    # ours: UHDI v2 -> HGLDD
    sh([firtool, '-g', f'--emit-uhdi={work}/ours/u.json', '-o', str(work / 'ours/v'), '--split-verilog', str(fir)])
    doc = json.loads((work / 'ours/u.json').read_text())
    (work / 'ours/dd/ours.dd').write_text(json.dumps(backend('hgldd').convert(doc, None)))
    # native: tywaves firtool, split so the .dd lands next to the .sv files
    sh([str(tc.tywaves_firtool), '-g', '--emit-hgldd', '-o', str(work / 'native/v'),
        '--split-verilog', str(tfir)])
    for f in (work / 'native/v').glob('*.dd'):
        shutil.move(str(f), work / 'native/dd' / f.name)
    svs = {s: sorted((work / s / 'v').glob('*.sv')) for s in ('ours', 'native')}
    top = re.search(r'circuit\s+(\w+)', fir.read_text()).group(1)
    top_sv = next(p for p in svs['ours'] if p.stem == top).read_text()
    io = ports(top_sv, top)
    io_n = ports(next(p for p in svs['native'] if p.stem == top).read_text(), top)
    assert [x for x in io if x[0] == 'input'] == [x for x in io_n if x[0] == 'input'], (io, io_n)
    tb = testbench(top, [x for x in io if x[0] == 'input'])
    views = {}
    for s in ('ours', 'native'):
        vcd = simulate(svs[s], top, tb, work / s / 'sim')
        views[s], err = typed_view(work / s / 'dd', vcd, top, work / s / 'typed')
        views[s] = (views[s], leaf_view(work / s, vcd))
        if err:
            report.append(f'  [{s}] tydump: {err[:300]}')
    compare(stem, views, report)


def leaf_view(sdir, vcd):
    """var -> [(leaf, width, status, values)] where status is sig/const/
    expr/missing/dup, as VcdRewriter would treat the leaf."""
    decl, ids, ch = read_vcd(vcd)
    where = {f'{sc}.{ref}': i for (sc, ref), i in zip(decl, ids)}
    scopes = {sc for sc, _ in decl}
    out = {}
    for l in (sdir / 'typed.leaves.tsv').read_text().splitlines():
        scope, path, w, trace = l.split('\t')
        var = f'{scope}.{path.split(".")[0]}'
        m = re.fullmatch(r'RefTraceName\("(.*)"\)', trace)
        if m:
            # An inline scope is not in the simulator's VCD: its signals
            # live in the enclosing module, where tywaves looks them up.
            home = scope
            while home not in scopes and '.' in home:
                home = home.rpartition('.')[0]
            i = where.get(f'{home}.{m.group(1)}')
            st, vals = ('sig', ch.get(i, [])) if i else ('missing', [])
        elif trace.startswith('Constant'):
            st, vals = 'const', [(0, trace)]
        else:
            st, vals = 'expr', []
        out.setdefault(var, []).append([f'{scope}.{path}', w, st, vals, trace])
    for leaves in out.values():
        seen = set()
        for lf in leaves:
            if lf[2] == 'sig':
                if lf[4] in seen:
                    lf[2] = 'dup'
                seen.add(lf[4])
    return out


def ok(leaves):
    return all(l[2] in ('sig', 'const') for l in leaves)


def compare(stem, views, report):
    (o, lo), (n, ln) = views['ours'], views['native']
    times = sorted({t for v in list(lo.values()) + list(ln.values()) for l in v for t, _ in l[3]})
    stats = dict(same=0, ours_only_ok=0, native_only_ok=0, both_bad=0)
    lines = []
    for k in sorted(set(o) | set(n)):
        if k not in o or k not in n:
            side = 'NATIVE' if k in n else 'OURS'
            v = (ln if k in n else lo)[k]
            lines.append(f'  ONLY-{side:6} {k}  {(n if k in n else o)[k]["type"]}  shown={"yes" if ok(v) else "broken"}')
            continue
        a, b = lo[k], ln[k]
        ga, gb = ok(a), ok(b)
        notes = [f'{f}: ours={o[k][f]!r} native={n[k][f]!r}' for f in ('type', 'enum') if o[k][f] != n[k][f]]
        if not ga or not gb:
            bad = lambda ls: ','.join(f'{l[0].split(".", 2)[-1]}={l[2]}' for l in ls if l[2] not in ('sig', 'const'))
            if ga:
                stats['ours_only_ok'] += 1
                lines.append(f'  NATIVE-BROKEN {k}: {bad(b)}')
            elif gb:
                stats['native_only_ok'] += 1
                lines.append(f'  OURS-BROKEN   {k}: {bad(a)}')
            else:
                stats['both_bad'] += 1
                lines.append(f'  BOTH-BROKEN   {k}: ours {bad(a)}; native {bad(b)}')
            continue
        diff = []
        for la, lb in zip(a, b):
            va, vb = at(la[3], times), at(lb[3], times)
            if va != vb:
                i = next(i for i, (x, y) in enumerate(zip(va, vb)) if x != y)
                diff.append(f'{la[0].split(".", 2)[-1]}@t={times[i]} ours={va[i]}({la[4]}) native={vb[i]}({lb[4]})')
        if len(a) != len(b):
            diff.append(f'leaf count {len(a)} vs {len(b)}')
        if diff:
            lines.append(f'  VALUE-DIFF    {k}: ' + '; '.join(diff))
        else:
            stats['same'] += 1
            if notes:
                lines.append(f'  same values   {k}  ' + ' | '.join(notes))
    report.append(f'## {stem}: native {len(n)} vars, ours {len(o)}; ' +
                  ', '.join(f'{k}={v}' for k, v in stats.items()))
    report.extend(lines)


def main():
    discover()
    tc = discover_toolchain()
    firtool = os.environ['FIRTOOL']
    report, bad = [], False
    for stem in sys.argv[1:] or FIXTURES:
        try:
            run(stem, tc, firtool, report)
        except Exception as e:
            report.append(f'## {stem}: FAILED {str(e)[:800]}')
        bad |= any(l.startswith(('  OURS-BROKEN', '  VALUE-DIFF', f'## {stem}: FAILED'))
                   for l in report)
        print('\n'.join(report)); report.clear()
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
