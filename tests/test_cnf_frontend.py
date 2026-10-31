"""Optional AST Boolean search must preserve conversion and model semantics."""
import contextlib
import inspect
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import z3

from scripts.ufnia_converter import ConversionError, convert_assertions


def operator_kinds(assertions):
    pending, seen, kinds = list(assertions), set(), set()
    while pending:
        expression = pending.pop()
        if expression.get_id() in seen:
            continue
        seen.add(expression.get_id())
        kinds.add(expression.decl().kind())
        pending.extend(expression.children())
    return kinds


class CnfFrontendTests(unittest.TestCase):
    def convert(self, assertions, **options):
        self.assertIn('cnf', inspect.signature(convert_assertions).parameters)
        return convert_assertions(assertions, arbitrary_precision=True, uf_observers=True, **options)

    def sample(self):
        x, y = z3.Ints('cnf_source_x cnf_source_y')
        p, q = z3.Bools('cnf_source_p cnf_source_q')
        f = z3.Function('cnf_source_f', z3.IntSort(), z3.IntSort())
        return (x == y + 3, f(x) == x*y + 1, z3.Or(p, z3.If(q, x > 0, y > 0)))

    def test_default_cnf_and_explicit_off_keep_different_boolean_structures(self):
        assertions = self.sample()
        default = self.convert(assertions)
        enabled = self.convert(assertions, cnf=True)
        disabled = self.convert(assertions, cnf=False)
        self.assertEqual(default.metadata['cnf'], 'on')
        self.assertEqual(enabled.metadata['cnf'], 'on')
        self.assertEqual(disabled.metadata['cnf'], 'off')
        self.assertNotIn(z3.Z3_OP_ITE, operator_kinds(enabled.goal))
        self.assertIn(z3.Z3_OP_ITE, operator_kinds(disabled.goal))
        self.assertIn(z3.Z3_OP_ITE, operator_kinds(z3.parse_smt2_string(disabled.normalized_smt2)))

    def test_refinement_preserves_cnf_off_and_restores_the_original_model(self):
        conversion = self.convert(self.sample(), cnf=False)
        refined = conversion.refine((z3.BoolVal(True),))
        self.assertEqual(refined.metadata['cnf'], 'off')
        self.assertIn(z3.Z3_OP_ITE, operator_kinds(refined.goal))
        self.assertIs(refined.uf_state, conversion.uf_state)
        values = {name: True if z3.is_bool(expr) else '2' for name, expr in refined.native_symbols.items()}
        _, valid = refined.restore_model(values)
        self.assertTrue(valid)

    def test_cnf_off_rejects_nonobserver_and_legacy_conversion(self):
        self.assertIn('cnf', inspect.signature(convert_assertions).parameters)
        for options in ({'arbitrary_precision': True}, {'arbitrary_precision': False, 'uf_observers': True}):
            with self.assertRaises(ConversionError):
                convert_assertions((z3.BoolVal(True),), cnf=False, **options)

    def test_noncnf_xor_distinct_and_boolean_ite_enter_the_native_frontend(self):
        x, y, z = z3.Ints('cnf_ops_x cnf_ops_y cnf_ops_z')
        p, q, r = z3.Bools('cnf_ops_p cnf_ops_q cnf_ops_r')
        f = z3.Function('cnf_ops_f', z3.BoolSort(), z3.IntSort())
        assertions = (z3.Or(x > 0, z3.Xor(p, q)), z3.Or(r, z3.Distinct(x, y, z)),
                      z3.Or(p, z3.If(q, x > 0, y > 0)), f(z3.Xor(p, q)) == x)
        conversion = self.convert(assertions, cnf=False)
        kinds = operator_kinds(conversion.goal)
        self.assertNotIn(z3.Z3_OP_XOR, kinds)
        self.assertNotIn(z3.Z3_OP_DISTINCT, kinds)
        self.assertIn(z3.Z3_OP_ITE, kinds)

    def test_driver_cnf_and_distance_reports_do_not_change_default_core_flags(self):
        from UFNIA_LS import run as driver
        cases = (((), 'on', 'classic', False),
                 (('--distance', 'structural'), 'on', 'structural', True),
                 (('--uf-observers', '--cnf', 'off'), 'off', 'classic', True),
                 (('--uf-observers', '--cnf', 'off', '--distance', 'structural'), 'off', 'structural', True))
        for options, cnf, distance, forwarded in cases:
            with tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                source, core = directory / 'input.smt2', directory / 'core'
                source.write_text('''(declare-const p Bool)(declare-const q Bool)
(declare-const x Int)(declare-const y Int)(declare-fun f (Int) Int)
(assert (or p (ite q (> x 0) (> y 0))))(assert (= (f x) 7))(check-sat)''')
                core.write_text('''#!/usr/bin/env python3
import json, sys
args = sys.argv[1:]
assert '--cnf' not in args
assert ('--distance' in args) == %r
if '--distance' in args:
    assert args[args.index('--distance') + 1] == %r
print(json.dumps({'status': 'parsed'}))
''' % (forwarded, distance))
                core.chmod(0o755)
                output, error = io.StringIO(), io.StringIO()
                with patch.object(driver, 'CORE', core), contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                    code = driver.main([str(source), '--parse-only', *options])
            self.assertEqual(code, 0, error.getvalue())
            report = json.loads(output.getvalue())
            self.assertIn('cnf', report)
            self.assertIn('distance', report)
            self.assertEqual((report['cnf'], report['distance']), (cnf, distance))
            query = report['last_query']
            self.assertEqual((query['cnf'], query['distance']), (cnf, distance))
            self.assertEqual(query['preprocessing']['cnf'], cnf)

    def test_driver_rejects_cnf_off_without_observers(self):
        from UFNIA_LS import run as driver
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'input.smt2'
            source.write_text('(assert true)(check-sat)')
            error = io.StringIO()
            self.assertIn('--cnf', inspect.getsource(driver._arguments))
            with contextlib.redirect_stderr(error):
                code = driver.main([str(source), '--cnf', 'off'])
            self.assertEqual(code, 2)
            self.assertIn('--uf-observers', error.getvalue())

    def test_real_noncnf_structural_driver_validates_the_original_model(self):
        from UFNIA_LS import run as driver
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source, report_path = directory / 'input.smt2', directory / 'report.json'
            source.write_text('''(declare-const p Bool)(declare-const q Bool)
(declare-const x Int)(declare-const y Int)(declare-fun f (Int) Int)
(assert (= x (+ y 3)))(assert (= (f x) (+ (* x y) 1)))
(assert (or p (ite q (> x 0) (> y 0))))(check-sat)''')
            output, error = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                code = driver.main([str(source), '--uf-observers', '--cnf', 'off', '--distance', 'structural',
                                    '--scoring', 'paws', '--seconds', '.3', '--json-report', str(report_path)])
            self.assertEqual(code, 0, error.getvalue())
            self.assertEqual(output.getvalue().strip(), 'sat', error.getvalue())
            report = json.loads(report_path.read_text())
            self.assertEqual((report['cnf'], report['distance']), ('off', 'structural'))
            self.assertTrue(report['last_query']['core']['validated'])
            self.assertTrue(report['last_query']['original_model_validated'])


if __name__ == '__main__':
    unittest.main()
