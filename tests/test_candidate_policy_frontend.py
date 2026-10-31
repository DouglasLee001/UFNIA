"""Critical candidates are opt-in and do not alter broad core invocations."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class CandidatePolicyFrontendTests(unittest.TestCase):
    def test_policy_matrix_omits_broad_and_reports_the_independent_choice(self):
        from UFNIA_LS import run as driver
        cases = (
            ((), 'broad', False),
            (('--candidate-policy', 'broad'), 'broad', False),
            (('--uf-observers',), 'broad', False),
            (('--search-policy', 'greedy', '--candidate-policy', 'broad'), 'broad', False),
            (('--candidate-policy', 'critical'), 'critical', True),
            (('--uf-observers', '--candidate-policy', 'critical'), 'critical', True),
            (('--uf-observers', '--search-policy', 'greedy', '--search-phase', 'alternating',
              '--arithmetic-repair', 'joint', '--active-arithmetic-rows', 'on',
              '--candidate-policy', 'critical'), 'critical', True),
        )
        for options, requested, forwarded in cases:
            with tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                source, core = directory / 'input.smt2', directory / 'core'
                source.write_text('(assert true)(check-sat)')
                core.write_text('''#!/usr/bin/env python3
import json, sys
args = sys.argv[1:]
assert ('--candidate-policy' in args) == %r
if '--candidate-policy' in args:
    assert args[args.index('--candidate-policy') + 1] == 'critical'
print(json.dumps({'status': 'parsed'}))
''' % forwarded)
                core.chmod(0o755)
                output, error = io.StringIO(), io.StringIO()
                with patch.object(driver, 'CORE', core), contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                    code = driver.main([str(source), '--parse-only', *options])
            self.assertEqual(code, 0, error.getvalue())
            report = json.loads(output.getvalue())
            self.assertIn('candidate_policy', report)
            self.assertEqual(report['candidate_policy'], requested)
            self.assertEqual(report['last_query']['candidate_policy'], requested)
            self.assertEqual(report['post_lower_eqs'], 'off')
            self.assertFalse(report['dump_candidate'])

    def test_real_critical_values_survive_the_native_candidate_cap(self):
        from UFNIA_LS import run as driver
        names = ['critical_source_' + str(index) for index in range(32)]
        source_text = ''.join('(declare-const %s Int)' % name for name in names)
        source_text += ''.join('(assert (>= %s 0))' % name for name in names)
        source_text += '(assert (or ' + ' '.join('(= (* %s %s) 4)' % (name, name) for name in names) + '))(check-sat)'
        for search, candidate, expected in (('greedy', 'broad', 'unknown'),
                                            ('greedy', 'critical', 'sat'),
                                            ('legacy', 'critical', 'sat')):
            with self.subTest(search=search, candidate=candidate), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                source, report_path = directory / 'input.smt2', directory / 'report.json'
                source.write_text(source_text)
                output, error = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                    code = driver.main([str(source), '--search-policy', search, '--candidate-policy', candidate,
                                        '--scoring', 'count', '--seconds', '.05', '--json-report', str(report_path)])
                self.assertEqual(code, 0, error.getvalue())
                self.assertEqual(output.getvalue().strip(), expected, error.getvalue())
                report = json.loads(report_path.read_text())
                query, stats = report['last_query'], report['last_query']['core']
                self.assertEqual(report['candidate_policy'], candidate)
                self.assertEqual(stats['candidate_policy'], candidate)
                self.assertEqual(query['preprocessing']['integer_variable_count'], 32)
                if candidate == 'critical':
                    self.assertTrue(stats['validated'])
                    self.assertTrue(query['original_model_validated'])
                    self.assertEqual(stats['steps'], 1)
                    self.assertGreater(stats['candidate_probes'], 0)
                    self.assertGreater(stats['critical_candidates'], 0)
                else:
                    self.assertFalse(query['validated'])
                    self.assertEqual(stats['candidate_probes'], 0)
                    self.assertEqual(stats['best_arithmetic_unsat'], 1)


if __name__ == '__main__':
    unittest.main()
