"""Arithmetic repair flags must remain independent of frontend elimination."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class ArithmeticRepairFrontendTests(unittest.TestCase):
    def test_mode_matrix_preserves_default_core_compatibility_and_reports_both_flags(self):
        from UFNIA_LS import run as driver
        cases = (
            ((), 'off', False, 'off'),
            (('--arithmetic-repair', 'off'), 'off', False, 'off'),
            (('--arithmetic-repair', 'linear'), 'linear', True, 'off'),
            (('--arithmetic-repair', 'quotient'), 'quotient', True, 'off'),
            (('--arithmetic-repair', 'joint'), 'joint', True, 'off'),
            (('--uf-observers',), 'off', False, 'off'),
            (('--uf-observers', '--arithmetic-repair', 'off', '--post-lower-eqs', 'on'), 'off', False, 'on'),
            (('--uf-observers', '--arithmetic-repair', 'quotient', '--post-lower-eqs', 'on'), 'quotient', True, 'on'),
            (('--uf-observers', '--arithmetic-repair', 'joint', '--post-lower-eqs', 'off'), 'joint', True, 'off'),
        )
        for options, mode, forwarded, post_lower in cases:
            with tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                source, core = directory / 'input.smt2', directory / 'core'
                source.write_text('(assert true)(check-sat)')
                core.write_text('''#!/usr/bin/env python3
import json, sys
args = sys.argv[1:]
assert ('--arithmetic-repair' in args) == %r
if '--arithmetic-repair' in args:
    assert args[args.index('--arithmetic-repair') + 1] == %r
assert '--post-lower-eqs' not in args
print(json.dumps({'status': 'parsed'}))
''' % (forwarded, mode))
                core.chmod(0o755)
                output, error = io.StringIO(), io.StringIO()
                with patch.object(driver, 'CORE', core), contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                    code = driver.main([str(source), '--parse-only', *options])
            self.assertEqual(code, 0, error.getvalue())
            report = json.loads(output.getvalue())
            self.assertIn('arithmetic_repair', report)
            self.assertEqual(report['arithmetic_repair'], mode)
            self.assertEqual(report['last_query']['arithmetic_repair'], mode)
            self.assertEqual(report['post_lower_eqs'], post_lower)
            self.assertEqual(report['last_query']['post_lower_eqs'], post_lower)
            self.assertEqual(report['last_query']['preprocessing']['post_lower_eqs'], post_lower)

    def test_real_quotient_and_joint_repairs_preserve_division_models(self):
        from UFNIA_LS import run as driver
        names = ['repair_source_' + str(index) for index in range(8)]
        total = '(+ ' + ' '.join(names) + ')'
        source_text = ''.join('(declare-const %s Int)' % name for name in names)
        source_text += '(declare-fun repair_source_f (Int) Int)'
        source_text += '(assert (>= (* %s %s) 1024))' % (total, total)
        source_text += '(assert (= (repair_source_f (div %s 32)) 0))(check-sat)' % total
        for mode in ('quotient', 'joint'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                source, report_path = directory / 'input.smt2', directory / 'report.json'
                source.write_text(source_text)
                output, error = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                    code = driver.main([str(source), '--uf-observers', '--cnf', 'off', '--distance', 'structural',
                                        '--post-lower-eqs', 'off', '--arithmetic-repair', mode,
                                        '--seconds', '.3', '--json-report', str(report_path)])
                self.assertEqual(code, 0, error.getvalue())
                self.assertEqual(output.getvalue().strip(), 'sat', error.getvalue())
                report = json.loads(report_path.read_text())
                self.assertEqual(report['arithmetic_repair'], mode)
                self.assertEqual(report['post_lower_eqs'], 'off')
                query = report['last_query']
                self.assertTrue(query['core']['validated'])
                self.assertTrue(query['original_model_validated'])
                self.assertGreater(query['core']['arith_quotient_relations'], 0)
                self.assertGreater(query['core']['arith_repair_proposals'], 0)
                self.assertGreater(query['core']['arith_joint_moves'], 0)


if __name__ == '__main__':
    unittest.main()
