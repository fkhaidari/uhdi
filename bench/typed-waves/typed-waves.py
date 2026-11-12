import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'converter/src'))
parser = argparse.ArgumentParser(
    description='Compile FIRRTL with UHDI or open typed waves.')
commands = parser.add_subparsers(dest='command', required=True)
compile_parser = commands.add_parser('compile')
compile_parser.add_argument('firrtl', type=Path)
compile_parser.add_argument('--out', type=Path, required=True)
compile_parser.add_argument('--firtool',
                            default=os.environ.get('FIRTOOL', 'firtool'))
view_parser = commands.add_parser('view')
view_parser.add_argument('directory', type=Path)
view_parser.add_argument('--vcd', type=Path, required=True)
view_parser.add_argument('--top', required=True)
view_parser.add_argument('--extra-scopes', nargs='+', required=True)
view_parser.add_argument('--viewer',
                         default=os.environ.get('TYWAVES', 'surfer-tywaves'))
args = parser.parse_args()
if args.command == 'compile':
  output = args.out.resolve()
  if output.exists() and any(output.iterdir()):
    parser.error('--out must be empty')
  (output / 'verilog').mkdir(parents=True, exist_ok=True)
  (output / 'hgldd').mkdir()
  subprocess.run([
      args.firtool, '-g', '--emit-uhdi=' + str(output / 'uhdi.json'),
      '--split-verilog', '-o',
      str(output / 'verilog'),
      str(args.firrtl.resolve())
  ],
                 check=True)
  from uhdi_common.backend import discover, get
  discover()
  document = get('hgldd').convert(
      json.loads((output / 'uhdi.json').read_text()), None)
  (output / 'hgldd/design.dd').write_text(json.dumps(document, indent=2) + '\n')
  print(output)
else:
  subprocess.run([
      args.viewer,
      str(args.vcd.resolve()), '--hgldd-dir',
      str(args.directory.resolve() / 'hgldd'), '--top-module', args.top,
      '--extra-scopes', *args.extra_scopes
  ],
                 check=True)
