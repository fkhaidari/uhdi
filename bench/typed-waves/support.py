import importlib.util
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(root / 'converter/src'), str(root / 'bench/src')]
spec = importlib.util.spec_from_file_location('tyview',
                                              root / 'bench/tyview/run.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
