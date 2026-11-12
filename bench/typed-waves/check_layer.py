import json
import pathlib
import re
import sys
import strict

work = pathlib.Path(sys.argv[1]).resolve()
p = work / 'LayerHierarchy/ours'
prefix = 'TOP.LayerHierarchy.'
ours = strict.read(p / 'typed')
scopes = {
    row[0]: row[1] for row in (line.split('\t') for line in (
        p / 'typed.scopes.tsv').read_text().splitlines())
}
decl, ids, changes = strict.m.read_vcd(p / 'sim/sim.vcd')
raw = {
    scope + '.' + name: changes.get(ident, [])
    for (scope, name), ident in zip(decl, ids)
}
times = sorted({t for seq in raw.values() for t, _ in seq})
values = lambda name: [
    int(v, 2) for v in strict.m.at(raw[prefix + name], times)
]
clock, reset, inputs = values('clock'), values('reset'), values('in')
left = right = child = mixed = 0
expected = {
    key: [] for key in [
        'out', 'left.r', 'right.r', 'inline.child.r', 'inline.mixed',
        'inline.in', 'inline.out', 'observed'
    ]
}
for i, (clk, rst, x) in enumerate(zip(clock, reset, inputs)):
  if clk == 1 and (i == 0 or clock[i - 1] == 0):
    left, right, child, mixed = (0, 0, 0,
                                 0) if rst else (x, (left + 1) & 255,
                                                 (left + 1) & 255,
                                                 ((left + 1) & 255) ^ 0x5a)
  observed = mixed ^ ((child + 1) & 255)
  sample = [((right + 1) & 255), left, right, child, mixed, ((left + 1) & 255),
            observed, observed]
  for key, value in zip(expected, sample):
    expected[key].append(value)
errors = [
    'variable outside requested top hierarchy ' + key
    for key in ours
    if not key.startswith(prefix)
]
required = {'clock', 'reset', 'in', 'out', 'observed'}
for child_scope in ['left', 'right', 'inline.child']:
  required.update(child_scope + '.' + field
                  for field in ['clock', 'reset', 'in', 'out', 'r'])
required.update(
    'inline.' + field for field in ['clock', 'reset', 'in', 'out', 'mixed'])
required.update('dead.' + field for field in ['clock', 'reset'])
for field in required:
  if prefix + field not in ours:
    errors.append('missing layer source variable ' + prefix + field)
for key, value in ours.items():
  if not any(bits is not None and all(bit in '01'
                                      for bit in bits)
             for _, bits in value['values']):
    errors.append('no known typed value ' + key)
for field, wanted in expected.items():
  key = prefix + field
  if key not in ours:
    errors.append('missing source variable ' + key)
    continue
  if ours[key]['kind'] != 'g8':
    errors.append('incorrect source width ' + key)
  actual = strict.m.at(ours[key]['values'], times)
  bad = [(t, a, e) for t, a, e in zip(times, actual, wanted) if t >= 3 and
         (a is None or any(c not in '01' for c in a) or int(a, 2) != e)]
  if bad:
    errors.append(f'values {key}: {bad[:4]} ({len(bad)} timestamps)')
for suffix, typename in [('left', 'LayerLeaf'), ('right', 'LayerLeaf'),
                         ('inline', 'LayerInline'),
                         ('inline.child', 'LayerLeaf')]:
  if scopes.get(prefix + suffix) != typename:
    errors.append(
        f'scope type {prefix + suffix}: {scopes.get(prefix + suffix)} != {typename}'
    )
sv = (p / 'v/LayerHierarchy.sv').read_text()
if not all(
    re.search(r'\bLayerLeaf\s+' + name + r'\s*\(', sv)
    for name in ['left', 'right']):
  errors.append('left/right did not share deduplicated LayerLeaf')
if any(
    re.search(r'\bmodule\s+LayerInline\b', path.read_text())
    for path in (p / 'v').rglob('*.sv')):
  errors.append('LayerInline was not inlined')
if not any('bind LayerHierarchy ' in path.read_text()
           for path in (p / 'v').rglob('*.sv')):
  errors.append('active layer bind missing')
result = dict(fixture='LayerHierarchy',
              producer='Chisel debug-intrinsic fixture',
              timestamps=len(times),
              source_variables=len(ours),
              native_comparison=False,
              empty_instance_retained=bool(
                  re.search(r'\bLayerDead\s+dead\s*\(', sv)),
              errors=errors)
(work / 'layer-strict.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
sys.exit(bool(errors))
