import json
from pathlib import Path
import subprocess
import sys

work = Path(sys.argv[2])
work.mkdir(parents=True, exist_ok=True)


def ref(name):
  return {'sig_name': name}


def op(name, *args):
  return {'opcode': name, 'operands': list(args)}


def bits(value):
  return {'bit_vector': value}


def signed(name):
  return op('$signed', ref(name))


cases = {
    'sum': (256, 'a + b', op('+', ref('a'), ref('b'))),
    'product': (256, 'a * b', op('*', ref('a'), ref('b'))),
    'quotient': (256, 'a / b', op('/', ref('a'), ref('b'))),
    'remainder': (256, 'a % b', op('%', ref('a'), ref('b'))),
    'greater': (1, 'a > b', op('>', ref('a'), ref('b'))),
    'parity': (1, '^a', op('^', ref('a'))),
    'shifted': (256, 'a << n', op('<<', ref('a'), ref('n'))),
    'negative': (1, '$signed(s) < $signed(n)', op('<', signed('s'),
                                                  signed('n'))),
    'signed_div': (8, '$signed(s) / $signed(n)',
                   op('/', signed('s'), signed('n'))),
    'signed_rem': (8, '$signed(s) % $signed(n)',
                   op('%', signed('s'), signed('n'))),
    'signed_shift': (8, '$signed(s) >>> n', op('>>>', signed('s'), ref('n'))),
    'case_eq': (1, "x === 4'b10x1", op('===', ref('x'), bits('10x1'))),
    'wild_eq': (1, "x ==? 4'b1xx1", op('==?', ref('x'), bits('1xx1'))),
    'known_and': (4, "x & 4'b0000", op('&', ref('x'), bits('0000'))),
    'mux': (4, 'c ? x : y', op('?:', ref('c'), ref('x'), ref('y'))),
}
ports = [
    dict(var_name=name,
         type_name='logic',
         packed_range=[width - 1, 0],
         value=expr) for name, (width, _, expr) in cases.items()
]
(work / 'design.dd').write_text(
    json.dumps({
        'HGLDD': {
            'version': '1.0',
            'file_info': []
        },
        'objects': [
            dict(kind='module',
                 obj_name='Top',
                 module_name='Top',
                 port_vars=ports,
                 children=[])
        ]
    }))
lines = [
    'module Top;', 'reg [255:0] a, b;', 'reg [7:0] s, n;', 'reg [3:0] x,y;',
    'reg c;'
]
for name, (width, verilog, _) in cases.items():
  lines.append(f'wire [{width-1}:0] {name} = {verilog};')
lines += [
    'initial begin', '$dumpfile("input.vcd"); $dumpvars(0,Top);',
    "a = (256'd1 << 200) + 12345; b = (256'd1 << 130) + 3; s=8'hf8; n=3; x=4'b10x1; y=4'b1001; c=1'bx;",
    "#5 a = {256{1'b1}}; b = 17; s=8'h80; n=8'hff; x=4'b1001; c=0;",
    "#5 a = (256'd1 << 255); b = 0; s=8'h7f; n=0; x=4'bzzzz; c=1;",
    '#5 $finish;', 'end', 'endmodule'
]
(work / 'design.sv').write_text('\n'.join(lines))
subprocess.run(['iverilog', '-g2012', '-o', 'sim', 'design.sv'],
               cwd=work,
               check=True)
subprocess.run(['vvp', 'sim'], cwd=work, check=True)
subprocess.run([
    sys.argv[1],
    str(work),
    str(work / 'input.vcd'), 'Top', 'Top',
    str(work / 'typed')
],
               check=True)
from support import m


def read(path):
  decl, ids, changes = m.read_vcd(path)
  return {name: changes.get(code, []) for (_, name), code in zip(decl, ids)}


def normalize(value, width):
  return value.lower().rjust(
      width, value[0].lower() if value[0].lower() in 'xz' else '0')


raw, typed = read(work / 'input.vcd'), read(work / 'typed.vcd')
errors = []
for name, (width, _, _) in cases.items():
  assert name in typed, ('missing expression', name)
  times = sorted({t for t, _ in raw[name]} | {t for t, _ in typed[name]})
  for t, expected, actual in zip(times, m.at(raw[name], times),
                                 m.at(typed[name], times)):
    if actual is None or normalize(actual, width) != normalize(expected, width):
      errors.append((name, t, expected, actual))
assert not errors, errors
print(
    f'PASS: {len(cases)} expressions match Icarus Verilog, including 256-bit and four-state cases'
)
