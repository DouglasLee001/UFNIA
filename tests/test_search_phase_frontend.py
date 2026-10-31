"""Alternating phases are opt-in and require the greedy search policy."""
import contextlib
import inspect
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class SearchPhaseFrontendTests(unittest.TestCase):
    def test_phase_matrix_omits_mixed_and_reports_the_independent_choice(self):
        from UFNIA_LS import run as driver
        cases = (
            ((), 'mixed', False),
            (('--search-phase', 'mixed'), 'mixed', False),
            (('--uf-observers',), 'mixed', False),
            (('--search-policy', 'greedy'), 'mixed', False),
            (('--search-policy', 'greedy', '--search-phase', 'mixed'), 'mixed', False),
            (('--search-policy', 'greedy', '--search-phase', 'alternating'), 'alternating', True),
            (('--uf-observers', '--search-policy', 'greedy', '--search-phase', 'alternating'), 'alternating', True),
            (('--uf-observers', '--arithmetic-repair', 'joint', '--active-arithmetic-rows', 'on',
              '--scoring', 'paws', '--search-policy', 'greedy', '--search-phase', 'alternating'), 'alternating', True),
        )
        for options, requested, forwarded in cases:
            with tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                source, core = directory / 'input.smt2', directory / 'core'
                source.write_text('(assert true)(check-sat)')
                core.write_text('''#!/usr/bin/env python3
import json, sys
args = sys.argv[1:]
assert ('--search-phase' in args) == %r
if '--search-phase' in args:
    assert args[args.index('--search-phase') + 1] == 'alternating'
    assert args[args.index('--search-policy') + 1] == 'greedy'
print(json.dumps({'status': 'parsed'}))
''' % forwarded)
                core.chmod(0o755)
                output, error = io.StringIO(), io.StringIO()
                with patch.object(driver, 'CORE', core), contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                    code = driver.main([str(source), '--parse-only', *options])
            self.assertEqual(code, 0, error.getvalue())
            report = json.loads(output.getvalue())
            self.assertIn('search_phase', report)
            self.assertEqual(report['search_phase'], requested)
            self.assertEqual(report['last_query']['search_phase'], requested)
            self.assertEqual(report['post_lower_eqs'], 'off')

    def test_alternating_rejects_the_legacy_policy(self):
        from UFNIA_LS import run as driver
        self.assertIn('--search-phase', inspect.getsource(driver._arguments))
        for options in ((), ('--search-policy', 'legacy'), ('--uf-observers',)):
            with tempfile.TemporaryDirectory() as temporary:
                source = Path(temporary) / 'input.smt2'
                source.write_text('(assert true)(check-sat)')
                error = io.StringIO()
                with contextlib.redirect_stderr(error):
                    code = driver.main([str(source), '--search-phase', 'alternating', *options])
                self.assertEqual(code, 2)
                self.assertIn('--search-policy greedy', error.getvalue())

    def test_real_gate_barrier_alternates_boolean_and_integer_repairs(self):
        from UFNIA_LS import run as driver
        names = ['phase_gate_int_' + str(index) for index in range(6)]
        source_text = ''.join('(declare-const %s Int)' % name for name in names)
        source_text += '(declare-const b Bool)(declare-const c Bool)(declare-fun phase_gate_f (Int) Int)'
        source_text += '(assert (or b c))'
        for guard, arguments in (('b', names[:3]), ('c', names[3:])):
            for argument in arguments:
                predicate = '(>= %s 100)' % argument
                source_text += '(assert (or (not %s) %s))' % (guard, predicate)
                source_text += '(assert (or %s (not %s)))' % (guard, predicate)
        source_text += '(assert (= (phase_gate_f %s) 0))(check-sat)' % names[0]
        for phase, scoring in (('mixed', 'count'), ('alternating', 'count'), ('alternating', 'paws')):
            with self.subTest(phase=phase, scoring=scoring), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                source, report_path = directory / 'input.smt2', directory / 'report.json'
                source.write_text(source_text)
                output, error = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                    code = driver.main([str(source), '--uf-observers', '--search-policy', 'greedy',
                                        '--search-phase', phase, '--scoring', scoring, '--seconds', '.05',
                                        '--json-report', str(report_path)])
                self.assertEqual(code, 0, error.getvalue())
                report = json.loads(report_path.read_text())
                query, stats = report['last_query'], report['last_query']['core']
                self.assertEqual(query['preprocessing']['boolean_variable_count'], 2)
                self.assertEqual(query['preprocessing']['integer_variable_count'], 6)
                self.assertEqual(stats['initial_arithmetic_unsat'], 1)
                self.assertEqual(report['search_phase'], phase)
                if phase == 'mixed':
                    self.assertEqual(output.getvalue().strip(), 'unknown', error.getvalue())
                    self.assertEqual(stats['best_arithmetic_unsat'], 1)
                    self.assertFalse(query['validated'])
                    self.assertEqual(stats['bool_phase_steps'], 0)
                    self.assertEqual(stats['int_phase_steps'], 0)
                else:
                    self.assertEqual(output.getvalue().strip(), 'sat', error.getvalue())
                    self.assertTrue(stats['validated'])
                    self.assertTrue(query['original_model_validated'])
                    self.assertEqual(stats['best_arithmetic_unsat'], 0)
                    self.assertGreater(stats['bool_phase_steps'], 0)
                    self.assertGreaterEqual(stats['int_phase_steps'], 3)
                    self.assertGreater(stats['phase_switches'], 0)


if __name__ == '__main__':
    unittest.main()
