from collections import defaultdict, deque
import pathlib
import re
from support import m


def read(prefix, legacy_names=False):
  decl, ids, changes = m.read_vcd(pathlib.Path(str(prefix) + '.vcd'))
  by_name = defaultdict(deque)
  for (scope, name), ident in zip(decl, ids):
    key = scope + '.' + name
    by_name[key].append(changes.get(ident, []))
  result = {}
  for line in pathlib.Path(str(prefix) + '.vars.tsv').read_text().splitlines():
    scope, name, typename, params, kind, enum, trace = line.split('\t')
    key = scope + '.' + name
    match = re.fullmatch(r'RefTraceName\("(.*)"\)', trace)
    emitted = scope + '.' + (match.group(1) if legacy_names and match else name)
    assert by_name.get(emitted), ('metadata absent from rewritten VCD', key,
                                  emitted)
    result[key] = dict(type=typename,
                       params=params,
                       kind=kind,
                       enum=enum,
                       values=by_name[emitted].popleft())
  assert not any(by_name.values()), ('VCD variables absent from metadata',
                                     by_name.keys())
  return result
