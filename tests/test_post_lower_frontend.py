"""Post-lowering elimination is an optional, independently recorded pass."""
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


class PostLowerFrontendTests(unittest.TestCase):
    SOURCE = '''(declare-const x Int)(declare-fun f (Int) Int)(declare-fun g (Int) Int)
(assert (= (f (div x 3)) 1))(assert (= (g (mod x 3)) 2))'''

    def convert(self, assertions=None, **options):
        self.assertIn('post_lower_eqs', inspect.signature(convert_assertions).parameters)
        if assertions is None:
            assertions = z3.parse_smt2_string(self.SOURCE)
        return convert_assertions(assertions, arbitrary_precision=True, uf_observers=True, **options)

    def assert_zero_model_valid(self, conversion):
        values = {name: True if z3.is_bool(expr) else '0' for name, expr in conversion.native_symbols.items()}
        _, valid = conversion.restore_model(values)
        self.assertTrue(valid)

    def test_default_off_preserves_the_unoptimized_shape(self):
        default = self.convert()
        disabled = self.convert(post_lower_eqs=False)
        self.assertIs(inspect.signature(convert_assertions).parameters['post_lower_eqs'].default, False)
        self.assertEqual(default.metadata['post_lower_eqs'], 'off')
        self.assertEqual(disabled.metadata['post_lower_eqs'], 'off')
        for field in ('integer_variable_count', 'boolean_variable_count', 'clause_count', 'normalized_ast_nodes'):
            self.assertEqual(default.metadata[field], disabled.metadata[field])

    def test_post_lower_eqs_reduces_divmod_auxiliaries_in_both_cnf_modes(self):
        for cnf in (True, False):
            with self.subTest(cnf=cnf):
                disabled = self.convert(cnf=cnf, post_lower_eqs=False)
                enabled = self.convert(cnf=cnf, post_lower_eqs=True)
                self.assertEqual(enabled.metadata['post_lower_eqs'], 'on')
                self.assertEqual(enabled.metadata['cnf'], 'on' if cnf else 'off')
                self.assertLess(enabled.metadata['integer_variable_count'], disabled.metadata['integer_variable_count'])
                self.assertLess(enabled.metadata['clause_count'], disabled.metadata['clause_count'])
                self.assertLess(enabled.metadata['normalized_ast_nodes'], disabled.metadata['normalized_ast_nodes'])
                self.assert_zero_model_valid(enabled)

    def test_refinement_preserves_post_lower_eqs_and_cnf_selection(self):
        conversion = self.convert(cnf=False, post_lower_eqs=True)
        refined = conversion.refine((z3.BoolVal(True),))
        self.assertIs(refined.uf_state, conversion.uf_state)
        self.assertEqual(refined.metadata['post_lower_eqs'], 'on')
        self.assertEqual(refined.metadata['cnf'], 'off')
        self.assertEqual(refined.metadata['integer_variable_count'], conversion.metadata['integer_variable_count'])
        self.assert_zero_model_valid(refined)

    def test_zero_divisor_observations_keep_exact_model_conversion(self):
        source = '''(declare-const x Int)(declare-const zero Int)
(declare-fun f (Int) Int)(declare-fun g (Int) Int)(assert (= zero 0))
(assert (= (f (div x zero)) 1))(assert (= (g (mod x zero)) 2))'''
        conversion = self.convert(z3.parse_smt2_string(source), post_lower_eqs=True)
        self.assert_zero_model_valid(conversion)

    def test_post_steps_have_one_second_limits_and_skip_on_failure(self):
        disabled = self.convert(post_lower_eqs=False)
        real_try_for = z3.TryFor
        limits = []
        def fail_post_steps(step, limit):
            if limit == 1000:
                limits.append(limit)
                if len(limits) > 2:
                    return z3.Tactic('fail', ctx=step.ctx)
            return real_try_for(step, limit)
        with patch.object(z3, 'TryFor', side_effect=fail_post_steps):
            enabled = self.convert(post_lower_eqs=True)
        self.assertEqual(limits, [1000] * 4)
        self.assertEqual(enabled.metadata['integer_variable_count'], disabled.metadata['integer_variable_count'])
        self.assertEqual(enabled.goal.precision(), z3.Z3_GOAL_PRECISE)
        self.assert_zero_model_valid(enabled)

    def test_legacy_target_rejects_enabled_post_lower_eqs(self):
        self.assertIn('post_lower_eqs', inspect.signature(convert_assertions).parameters)
        with self.assertRaises(ConversionError):
            convert_assertions((z3.BoolVal(True),), post_lower_eqs=True)
        x = z3.Int('post_lower_pure_nia')
        conversion = convert_assertions((x / 3 == 1,),
                                        arbitrary_precision=True, post_lower_eqs=True)
        self.assertEqual(conversion.metadata['post_lower_eqs'], 'on')
        values = {name: '3' if z3.eq(expr, x) else '0' for name, expr in conversion.native_symbols.items()}
        _, valid = conversion.restore_model(values)
        self.assertTrue(valid)

    def test_driver_reports_the_frontend_only_flag_without_forwarding_it(self):
        from UFNIA_LS import run as driver
        cases = (((), 'off'), (('--post-lower-eqs', 'on'), 'on'),
                 (('--uf-observers', '--cnf', 'off', '--post-lower-eqs', 'on'), 'on'))
        for options, requested in cases:
            with tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                source, core = directory / 'input.smt2', directory / 'core'
                source.write_text(self.SOURCE + '(check-sat)')
                core.write_text('''#!/usr/bin/env python3
import json, sys
assert '--post-lower-eqs' not in sys.argv
print(json.dumps({'status': 'parsed'}))
''')
                core.chmod(0o755)
                output, error = io.StringIO(), io.StringIO()
                with patch.object(driver, 'CORE', core), contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                    code = driver.main([str(source), '--parse-only', *options])
            self.assertEqual(code, 0, error.getvalue())
            report = json.loads(output.getvalue())
            self.assertIn('post_lower_eqs', report)
            self.assertEqual(report['post_lower_eqs'], requested)
            self.assertEqual(report['last_query']['post_lower_eqs'], requested)
            self.assertEqual(report['last_query']['preprocessing']['post_lower_eqs'], requested)

    def test_real_driver_validates_divmod_uf_model_after_post_lower_eqs(self):
        from UFNIA_LS import run as driver
        source_text = '''(declare-const x Int)(declare-const zero Int)(declare-fun f (Int) Int)
(assert (= zero 0))(assert (= (f (div x zero)) 7))(assert (= (f (mod x zero)) 8))(check-sat)'''
        self.assertIn('post_lower_eqs', inspect.signature(convert_assertions).parameters)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source, report_path = directory / 'input.smt2', directory / 'report.json'
            source.write_text(source_text)
            output, error = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                code = driver.main([str(source), '--uf-observers', '--post-lower-eqs', 'on', '--cnf', 'off',
                                    '--distance', 'structural', '--scoring', 'paws', '--seconds', '.3',
                                    '--json-report', str(report_path)])
            self.assertEqual(code, 0, error.getvalue())
            self.assertEqual(output.getvalue().strip(), 'sat', error.getvalue())
            report = json.loads(report_path.read_text())
            self.assertEqual(report['post_lower_eqs'], 'on')
            self.assertTrue(report['last_query']['core']['validated'])
            self.assertTrue(report['last_query']['original_model_validated'])


if __name__ == '__main__':
    unittest.main()
