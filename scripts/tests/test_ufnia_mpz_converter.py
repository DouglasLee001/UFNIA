"""The new arbitrary-precision target is independent of the legacy reader."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import z3

ROOT = Path(__file__).resolve().parents[2]


class MpzConverterTests(unittest.TestCase):
    def convert(self, text):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        source = Path(temporary.name) / 'input.smt2'
        output = Path(temporary.name) / 'output.smt2'
        source.write_text(text)
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/convert_ufnia.py'), str(source), '-o', str(output), '--target', 'ufnia'], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        metadata = json.loads(Path(str(output) + '.json').read_text())
        self.assertTrue(metadata['arbitrary_precision'])
        parsed = z3.parse_smt2_file(str(output))
        core = subprocess.run([str(ROOT / 'build/ufnia_ls/ufnia_core'), str(output), '--parse-only'], capture_output=True, text=True, timeout=10)
        self.assertEqual(core.returncode, 0, core.stderr + core.stdout)
        return parsed

    def test_512_bit_arithmetic_enters_the_actual_gmp_core(self):
        root = 2**256 + 1
        assertions = self.convert(f'(declare-const x Int)(assert (> x {root-1}))(assert (< x {root+1}))(assert (= (* x x) {root*root}))(check-sat)')
        solver = z3.Solver(); solver.add(assertions)
        self.assertEqual(solver.check(), z3.sat)

    def test_uninterpreted_carrier_sort_and_uf_are_encoded(self):
        assertions = self.convert('(declare-sort S 0)(declare-fun f (Int) S)(declare-const x Int)(declare-const y Int)(assert (<= x y))(assert (<= y x))(assert (distinct (f x) (f y)))(check-sat)')
        solver = z3.Solver(); solver.add(assertions)
        self.assertEqual(solver.check(), z3.unsat)

    def test_former_int64_minimum_rhs_is_preserved_exactly(self):
        assertions = self.convert('(declare-const x Int)(declare-const y Int)(assert (= (+ (* x x) (* y y)) (- 9223372036854775808)))(check-sat)')
        solver = z3.Solver(); solver.add(assertions)
        self.assertEqual(solver.check(), z3.unsat)
