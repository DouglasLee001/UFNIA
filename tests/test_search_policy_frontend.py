"""Greedy recovery is explicitly selected without changing legacy invocations."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class SearchPolicyFrontendTests(unittest.TestCase):
    def test_policy_matrix_omits_legacy_and_reports_the_independent_choice(self):
        from UFNIA_LS import run as driver
        cases = (
            ((), 'legacy', False),
            (('--search-policy', 'legacy'), 'legacy', False),
            (('--uf-observers',), 'legacy', False),
            (('--uf-observers', '--search-policy', 'legacy'), 'legacy', False),
            (('--search-policy', 'greedy'), 'greedy', True),
            (('--uf-observers', '--search-policy', 'greedy'), 'greedy', True),
            (('--uf-observers', '--arithmetic-repair', 'joint', '--active-arithmetic-rows', 'on',
              '--search-policy', 'greedy'), 'greedy', True),
        )
        for options, requested, forwarded in cases:
            with tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                source, core = directory / 'input.smt2', directory / 'core'
                source.write_text('(assert true)(check-sat)')
                core.write_text('''#!/usr/bin/env python3
import json, sys
args = sys.argv[1:]
assert ('--search-policy' in args) == %r
if '--search-policy' in args:
    assert args[args.index('--search-policy') + 1] == 'greedy'
print(json.dumps({'status': 'parsed'}))
''' % forwarded)
                core.chmod(0o755)
                output, error = io.StringIO(), io.StringIO()
                with patch.object(driver, 'CORE', core), contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                    code = driver.main([str(source), '--parse-only', *options])
            self.assertEqual(code, 0, error.getvalue())
            report = json.loads(output.getvalue())
            self.assertIn('search_policy', report)
            self.assertEqual(report['search_policy'], requested)
            self.assertEqual(report['last_query']['search_policy'], requested)
            self.assertEqual(report['post_lower_eqs'], 'off')

    def test_real_greedy_guarded_repairs_validate_the_original_model(self):
        from UFNIA_LS import run as driver
        names = ['policy_source_' + str(index) for index in range(8)]
        total = '(+ ' + ' '.join(names) + ')'
        source_text = ''.join('(declare-const %s Int)' % name for name in names)
        source_text += '(declare-const q Int)(declare-const r Int)(declare-fun policy_f (Int) Int)'
        source_text += '(assert (>= %s 24))(assert (= (* %s %s) 576))' % (total, total, total)
        source_text += '(assert (>= r (- 8)))(assert (<= r 23))'
        source_text += '(assert (or (< %s 0) (= %s (+ (* 32 q) r))))' % (total, total)
        source_text += '(assert (= (policy_f %s) 0))(check-sat)' % total
        for scoring in ('count', 'paws'):
            with self.subTest(scoring=scoring), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                source, report_path = directory / 'input.smt2', directory / 'report.json'
                source.write_text(source_text)
                output, error = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                    code = driver.main([str(source), '--uf-observers', '--cnf', 'off', '--distance', 'structural',
                                        '--post-lower-eqs', 'off', '--arithmetic-repair', 'joint',
                                        '--active-arithmetic-rows', 'on', '--search-policy', 'greedy',
                                        '--scoring', scoring, '--seconds', '.3', '--json-report', str(report_path)])
                self.assertEqual(code, 0, error.getvalue())
                self.assertEqual(output.getvalue().strip(), 'sat', error.getvalue())
                report = json.loads(report_path.read_text())
                self.assertEqual(report['search_policy'], 'greedy')
                self.assertEqual(report['post_lower_eqs'], 'off')
                query = report['last_query']
                self.assertEqual(query['core']['search_policy'], 'greedy')
                self.assertTrue(query['core']['validated'])
                self.assertTrue(query['original_model_validated'])
                self.assertGreater(query['core']['arith_active_rows'], 0)
                self.assertGreater(query['core']['arith_repair_proposals'], 0)
                self.assertGreater(query['core']['arith_joint_moves'], 0)


if __name__ == '__main__':
    unittest.main()
