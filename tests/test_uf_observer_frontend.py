"""UF observations must follow preprocessing and canonical symbol rewriting."""
import contextlib
import inspect
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import z3

from scripts.ufnia_converter import convert_assertions
from UFNIA_LS import native_frontend


PREFIX = '__ufnia_observe_'


def observations(assertions):
    pending, found = list(assertions), []
    while pending:
        expr = pending.pop()
        if z3.is_and(expr):
            pending.extend(expr.children())
        elif expr.decl().kind() == z3.Z3_OP_UNINTERPRETED and str(expr.decl().name()).startswith(PREFIX):
            found.append(expr)
    return found


class ObserverFrontendTests(unittest.TestCase):
    def convert(self, assertions):
        self.assertIn('uf_observers', inspect.signature(convert_assertions).parameters)
        return convert_assertions(assertions, arbitrary_precision=True, uf_observers=True)

    def test_equation_elimination_rewrites_live_arguments_and_results(self):
        x, y = z3.Ints('observer_source_x observer_source_y')
        f = z3.Function('observer_source_f', z3.IntSort(), z3.IntSort())
        conversion = self.convert((x == y + 3, f(x) == 1 + 2*x, f(y) == x*y))
        self.assertEqual(conversion.metadata['uf_mode'], 'observed')
        self.assertTrue(conversion.metadata['uf_observers'])
        self.assertEqual(conversion.metadata['uf_observer_prefix'], PREFIX)
        markers = observations(conversion.goal)
        self.assertEqual(len(markers), 2)
        self.assertTrue(all(marker.num_args() == 2 for marker in markers))
        self.assertTrue(all(z3.is_expr(marker.arg(1)) for marker in markers))
        # No independently assigned observation proxy may replace a derived value.
        self.assertTrue(all(not z3.is_const(marker.arg(1)) for marker in markers))
        parsed = z3.parse_smt2_string(conversion.normalized_smt2)
        self.assertEqual(len(observations(parsed)), 2)
        self.assertNotIn('observer_source_x', conversion.normalized_smt2)
        self.assertNotIn('observer_source_y', conversion.normalized_smt2)
        values = {name: '2' for name in conversion.native_symbols}
        _, valid = conversion.restore_model(values)
        self.assertTrue(valid)

    def test_nested_bool_functions_and_divmod_are_lowered_with_observations(self):
        x = z3.Int('observer_div_source')
        pred = z3.Function('observer_pred', z3.IntSort(), z3.BoolSort())
        f = z3.Function('observer_nested', z3.BoolSort(), z3.IntSort())
        conversion = self.convert((f(pred(x/3)) == x%3, x >= 0))
        markers = observations(conversion.goal)
        self.assertEqual(len(markers), 2)
        self.assertEqual({str(marker.arg(0).sort()) for marker in markers}, {'Int', 'Bool'})
        self.assertNotIn('(div ', conversion.normalized_smt2)
        self.assertNotIn('(mod ', conversion.normalized_smt2)
        self.assertEqual(len(observations(z3.parse_smt2_string(conversion.normalized_smt2))), 2)

    def test_native_marker_acceptance_requires_opt_in_and_positive_conjunct(self):
        x = z3.Int('observer_native_source')
        marker = z3.Function(PREFIX + 'native_test', z3.IntSort(), z3.IntSort(), z3.BoolSort())
        self.assertIn('uf_observers', inspect.signature(native_frontend.analyze_normalized).parameters)
        with self.assertRaises(native_frontend.UnsupportedInput):
            native_frontend.analyze_normalized((marker(x, x + 1),))
        symbols, _, _ = native_frontend.analyze_normalized((marker(x, x + 1),), uf_observers=True)
        text = native_frontend.serialize_normalized((marker(x, x + 1),), symbols, uf_observers=True)
        self.assertIn(PREFIX, text)
        self.assertNotIn('observer_native_source', text)
        with self.assertRaises(native_frontend.UnsupportedInput):
            native_frontend.analyze_normalized((z3.Or(marker(x, x), x > 0),), uf_observers=True)

    def test_source_function_with_reserved_prefix_is_still_abstracted(self):
        f = z3.Function(PREFIX + 'user_function', z3.IntSort(), z3.IntSort())
        conversion = convert_assertions((f(0) == 7, f(1) == 8), arbitrary_precision=True)
        self.assertFalse(observations(z3.parse_smt2_string(conversion.normalized_smt2)))
        self.assertEqual(len(conversion.uf_state['applications']), 2)
        observed = self.convert((f(0) == 7, f(1) == 8))
        self.assertEqual(len(observations(observed.goal)), 2)
        self.assertNotIn(PREFIX + 'user_function', observed.normalized_smt2)
        restored, valid = observed.restore_model({})
        self.assertTrue(valid)
        self.assertEqual(str(restored.eval(f(0))), '7')
        self.assertEqual(str(restored.eval(f(1))), '8')

    def test_driver_forwards_observer_protocol_and_reports_interaction(self):
        from UFNIA_LS import run as driver
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source, core = directory / 'input.smt2', directory / 'core'
            source.write_text('(declare-fun f (Int) Int)(assert (= (f 0) 1))(check-sat)')
            core.write_text('''#!/usr/bin/env python3
import json, pathlib, sys
args = sys.argv[1:]
assert '--parse-only' in args and '--uf-observers' in args
assert args[args.index('--interaction') + 1] == 'soft'
assert '__ufnia_observe_' in pathlib.Path(args[0]).read_text()
print(json.dumps({'status': 'parsed', 'uf_applications': 1}))
''')
            core.chmod(0o755)
            output, error = io.StringIO(), io.StringIO()
            with patch.object(driver, 'CORE', core), contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                code = driver.main([str(source), '--parse-only', '--uf-observers', '--interaction', 'soft'])
        self.assertEqual(code, 0, error.getvalue())
        report = json.loads(output.getvalue())
        self.assertIn('interaction', report)
        self.assertEqual(report['interaction'], 'soft')
        self.assertTrue(report['uf_observers'])
        self.assertEqual(report['last_query']['normalized_semantics'], 'abstraction_with_congruence_table')

    def test_real_joint_driver_restores_and_validates_the_original_uf_model(self):
        from UFNIA_LS import run as driver
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source, report_path, model_path = (directory / name for name in ('input.smt2', 'report.json', 'model.smt2'))
            source.write_text('''(set-logic QF_UFNIA)
(declare-const x Int)
(declare-fun __ufnia_observe_source_collision (Int) Int)
(assert (= x 3))
(assert (= (__ufnia_observe_source_collision x) 7))
(assert (= (__ufnia_observe_source_collision (+ x 1)) 8))
(check-sat)''')
            output, error = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                code = driver.main([str(source), '--seconds', '.5', '--uf-observers', '--interaction', 'joint',
                                    '--json-report', str(report_path), '--model-output', str(model_path)])
            self.assertEqual(code, 0, error.getvalue())
            self.assertEqual(output.getvalue().strip(), 'sat', error.getvalue())
            report = json.loads(report_path.read_text())
            self.assertEqual(report['interaction'], 'joint')
            self.assertEqual(report['last_query']['preprocessing']['uf_mode'], 'observed')
            self.assertTrue(report['last_query']['core']['validated'])
            self.assertTrue(report['last_query']['original_model_validated'])
            self.assertIn('define-fun __ufnia_observe_source_collision', model_path.read_text())

    def test_scoring_reports_and_core_forwarding_preserve_the_default_baseline(self):
        from UFNIA_LS import run as driver
        cases = (((), 'count', False), (('--scoring', 'paws'), 'paws', True),
                 (('--uf-observers',), 'count', True),
                 (('--uf-observers', '--scoring', 'paws'), 'paws', True))
        for options, scoring, forwarded in cases:
            with tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                source, core = directory / 'input.smt2', directory / 'core'
                source.write_text('(assert true)(check-sat)')
                core.write_text('''#!/usr/bin/env python3
import json, sys
args = sys.argv[1:]
assert ('--scoring' in args) == %r
if '--scoring' in args:
    assert args[args.index('--scoring') + 1] == %r
print(json.dumps({'status': 'parsed'}))
''' % (forwarded, scoring))
                core.chmod(0o755)
                output, error = io.StringIO(), io.StringIO()
                with patch.object(driver, 'CORE', core), contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                    code = driver.main([str(source), '--parse-only', *options])
            self.assertEqual(code, 0, error.getvalue())
            report = json.loads(output.getvalue())
            self.assertIn('scoring', report)
            self.assertEqual(report['scoring'], scoring)
            self.assertEqual(report['last_query']['scoring'], scoring)

    def test_zero_divisor_arguments_preserve_divmod_models_and_congruence(self):
        source = '''(declare-const x Int)(declare-const zero Int)(declare-fun f (Int) Int)
(assert (= zero 0))
(assert (= (f (div x zero)) 7))
(assert (= (f (mod x zero)) 8))'''
        conversion = self.convert(z3.parse_smt2_string(source))
        core = Path(__file__).resolve().parents[1] / 'build/ufnia_ls/ufnia_core'
        # No scoring option is needed to exercise the default core semantics.
        result = subprocess.run([str(core), '--uf-observers', '--interaction', 'joint', '--seconds', '.2',
                                 '--model-output', '/dev/stdout'], input=conversion.normalized_smt2,
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        values, end = json.JSONDecoder().raw_decode(result.stdout.lstrip())
        self.assertEqual(result.stdout.lstrip()[end:].strip(), 'sat', result.stderr)
        _, valid = conversion.restore_model(values)
        self.assertTrue(valid)
        self.assertTrue(json.loads(result.stderr.splitlines()[-1])['validated'])
        contradictory = self.convert(z3.parse_smt2_string('''
(declare-const x Int)(declare-const y Int)(declare-fun f (Int) Int)
(assert (= x y))
(assert (= (f (div x 0)) 7))
(assert (= (f (div y 0)) 8))'''))
        result = subprocess.run([str(core), '--uf-observers', '--seconds', '.05'],
                                input=contradictory.normalized_smt2, text=True, capture_output=True, timeout=10)
        self.assertEqual(result.stdout.strip(), 'unknown', result.stderr)


if __name__ == '__main__':
    unittest.main()
