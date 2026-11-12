import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from support import m

here = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(
    description='Check typed waves through layers and memory lowering.')
parser.add_argument('--firtool', default=os.environ.get('FIRTOOL', 'firtool'))
parser.add_argument('--tydump', required=True, type=Path)
parser.add_argument('--out', required=True, type=Path)
parser.add_argument(
    '--elaborate',
    action='store_true',
    help='Regenerate FIRRTL from Chisel instead of using checked-in snapshots.')
args = parser.parse_args()
work = args.out.resolve()
if work.exists() and any(work.iterdir()):
  parser.error('--out must be empty')
work.mkdir(parents=True, exist_ok=True)
m.discover()
for name, check in [('LayerHierarchy', 'check_layer.py'),
                    ('MemoryHierarchy', 'check_memory.py')]:
  firrtl = here / 'fixtures' / (name + '.fir')
  if args.elaborate:
    from uhdi_bench import compile as compiler
    compiler._CACHE = work / 'firrtl'
    firrtl = compiler.compile_for(here / 'fixtures' / (name + '.scala'),
                                  compiler.get('uhdi'))
  output = work / name / 'ours'
  (output / 'v').mkdir(parents=True)
  (output / 'dd').mkdir()
  subprocess.run([
      args.firtool, '-g', '--emit-uhdi=' + str(output / 'u.json'),
      '--split-verilog', '-o',
      str(output / 'v'),
      str(firrtl)
  ],
                 check=True)
  document = m.backend('hgldd').convert(
      json.loads((output / 'u.json').read_text()), None)
  (output / 'dd/design.dd').write_text(json.dumps(document, indent=2) + '\n')
  svs = sorted((output / 'v').rglob('*.sv'))
  includes = [
      Path('-I' + str(directory))
      for directory in sorted({sv.parent for sv in svs})
  ]
  ports = m.ports((output / 'v' / (name + '.sv')).read_text(), name)
  vcd = m.simulate(
      svs + includes, name,
      m.testbench(name, [port for port in ports if port[0] == 'input']),
      output / 'sim')
  subprocess.run([
      str(args.tydump.resolve()),
      str(output / 'dd'),
      str(vcd), name, 'TOP,' + name,
      str(output / 'typed')
  ],
                 check=True)
  subprocess.run([sys.executable, str(here / check), str(work)], check=True)
