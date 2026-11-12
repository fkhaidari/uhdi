import json
from pathlib import Path
import sys
import strict

work = Path(sys.argv[1]).resolve()
p = work / 'MemoryHierarchy/ours'
ours = strict.read(p / 'typed')
prefix = 'TOP.MemoryHierarchy.'
expected = {
    'RW0_addr': 2,
    'RW0_en': 1,
    'RW0_clk': 1,
    'RW0_wmode': 1,
    'RW0_wdata': 19,
    'RW0_rdata': 19
}
decl, ids, changes = strict.m.read_vcd(p / 'sim/sim.vcd')
raw = {
    scope + '.' + name: changes.get(ident, [])
    for (scope, name), ident in zip(decl, ids)
}
times = sorted({t for seq in raw.values() for t, _ in seq})
errors = []
for name, width in expected.items():
  source = prefix + 'child.storage_ext.' + name
  physical = prefix + 'child_storage_ext.' + name
  if source not in ours:
    errors.append('missing source port ' + source)
    continue
  if ours[source]['kind'] != 'g' + str(width):
    errors.append('wrong width ' + source)
  if physical not in raw:
    errors.append('missing physical port ' + physical)
    continue
  a = strict.m.at(ours[source]['values'], times)
  b = strict.m.at(raw[physical], times)
  if a != b:
    errors.append('waveform differs ' + source)
actual_ports = {
    k.removeprefix(prefix + 'child.storage_ext.')
    for k in ours
    if k.startswith(prefix + 'child.storage_ext.')
}
if actual_ports != expected.keys():
  errors.append('memory interface mismatch ' + repr(sorted(actual_ports)))
for name in ['clock', 'reset', 'address', 'write', 'data', 'result']:
  for scope in ['', 'child.']:
    if prefix + scope + name not in ours:
      errors.append('missing typed source variable ' + scope + name)
if any(not k.startswith(prefix) for k in ours):
  errors.append('unexpected source root')
for aggregate in ['data', 'result']:
  packed = [0] * len(times)
  for field, width in [('tag', 3), ('bytes_0', 8), ('bytes_1', 8)]:
    for i, bits in enumerate(
        strict.m.at(raw[prefix + aggregate + '_' + field], times)):
      packed[i] = (packed[i] << width) | int(bits, 2)
  for scope in ['', 'child.']:
    entry = ours[prefix + scope + aggregate]
    if entry['type'] != 'IO[MemoryWord]':
      errors.append('lost Bundle type ' + scope + aggregate)
    actual = strict.m.at(entry['values'], times)
    if any(bits is None or int(bits, 2) != value
           for bits, value in zip(actual, packed)):
      errors.append('packed Bundle value ' + scope + aggregate)
result = dict(fixture='MemoryHierarchy',
              ports=len(expected),
              source_variables=len(ours),
              native_comparison=False,
              timestamps=len(times),
              errors=errors)
(work / 'memory-strict.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
sys.exit(bool(errors))
