"""Temporary guarded arithmetic rows are an explicit, validated core option."""
import contextlib
import inspect
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class ActiveArithmeticRowsFrontendTests(unittest.TestCase):
    def test_flag_matrix_preserves_off_compatibility_and_reports_active_rows(self):
        from UFNIA_LS import run as driver
        cases = (
            ((), 'off', False),
            (('--active-arithmetic-rows', 'off'), 'off', False),
            (('--uf-observers', '--arithmetic-repair', 'quotient'), 'off', False),
            (('--arithmetic-repair', 'linear', '--active-arithmetic-rows', 'on'), 'on', True),
            (('--uf-observers', '--arithmetic-repair', 'quotient', '--active-arithmetic-rows', 'on'), 'on', True),
            (('--uf-observers', '--arithmetic-repair', 'joint', '--active-arithmetic-rows', 'on',
              '--post-lower-eqs', 'on'), 'on', True),
        )
        for options, requested, forwarded in cases:
            with tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                source, core = directory / 'input.smt2', directory / 'core'
                source.write_text('(assert true)(check-sat)')
                core.write_text('''#!/usr/bin/env python3
import json, sys
args = sys.argv[1:]
assert ('--active-arithmetic-rows' in args) == %r
if '--active-arithmetic-rows' in args:
    assert args[args.index('--active-arithmetic-rows') + 1] == 'on'
assert '--post-lower-eqs' not in args
print(json.dumps({'status': 'parsed'}))
''' % forwarded)
                core.chmod(0o755)
                output, error = io.StringIO(), io.StringIO()
                with patch.object(driver, 'CORE', core), contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                    code = driver.main([str(source), '--parse-only', *options])
            self.assertEqual(code, 0, error.getvalue())
            report = json.loads(output.getvalue())
            self.assertIn('active_arithmetic_rows', report)
            self.assertEqual(report['active_arithmetic_rows'], requested)
            self.assertEqual(report['last_query']['active_arithmetic_rows'], requested)

    def test_enabled_active_rows_require_enabled_arithmetic_repair(self):
        from UFNIA_LS import run as driver
        self.assertIn('--active-arithmetic-rows', inspect.getsource(driver._arguments))
        for options in ((), ('--uf-observers',), ('--arithmetic-repair', 'off')):
            with tempfile.TemporaryDirectory() as temporary:
                source = Path(temporary) / 'input.smt2'
                source.write_text('(assert true)(check-sat)')
                error = io.StringIO()
                with contextlib.redirect_stderr(error):
                    code = driver.main([str(source), '--active-arithmetic-rows', 'on', *options])
                self.assertEqual(code, 2)
                self.assertIn('--arithmetic-repair', error.getvalue())

    def test_real_guarded_quotient_platform_validates_the_original_model(self):
        from UFNIA_LS import run as driver
        self.assertIn('--active-arithmetic-rows', inspect.getsource(driver._arguments))
        names = ['active_source_' + str(index) for index in range(8)]
        total = '(+ ' + ' '.join(names) + ')'
        source_text = ''.join('(declare-const %s Int)' % name for name in names)
        source_text += '(declare-const q Int)(declare-const r Int)(declare-fun active_f (Int) Int)'
        source_text += '(assert (>= %s 24))(assert (= (* %s %s) 576))' % (total, total, total)
        source_text += '(assert (>= r (- 8)))(assert (<= r 23))'
        source_text += '(assert (or (< %s 0) (= %s (+ (* 32 q) r))))' % (total, total)
        source_text += '(assert (= (active_f %s) 0))(check-sat)' % total
        for mode in ('quotient', 'joint'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                source, report_path = directory / 'input.smt2', directory / 'report.json'
                source.write_text(source_text)
                output, error = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                    code = driver.main([str(source), '--uf-observers', '--cnf', 'off', '--distance', 'structural',
                                        '--post-lower-eqs', 'off', '--arithmetic-repair', mode,
                                        '--active-arithmetic-rows', 'on', '--seconds', '.3',
                                        '--json-report', str(report_path)])
                self.assertEqual(code, 0, error.getvalue())
                self.assertEqual(output.getvalue().strip(), 'sat', error.getvalue())
                report = json.loads(report_path.read_text())
                self.assertEqual(report['active_arithmetic_rows'], 'on')
                self.assertEqual(report['post_lower_eqs'], 'off')
                query = report['last_query']
                self.assertTrue(query['core']['validated'])
                self.assertTrue(query['original_model_validated'])
                self.assertGreater(query['core']['arith_active_rows'], 0)
                self.assertGreater(query['core']['arith_repair_proposals'], 0)
                self.assertGreater(query['core']['arith_joint_moves'], 0)


if __name__ == '__main__':
    unittest.main()
