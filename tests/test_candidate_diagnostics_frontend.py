"""Explicit unknown-candidate diagnostics must never become validated models."""
import contextlib
import inspect
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class CandidateDiagnosticsFrontendTests(unittest.TestCase):
    def require_interface(self):
        from UFNIA_LS import run as driver
        self.assertIn('--dump-candidate', inspect.getsource(driver._arguments))
        return driver

    def test_dump_candidate_requires_keep_dir(self):
        driver = self.require_interface()
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'input.smt2'
            source.write_text('(assert true)(check-sat)')
            error = io.StringIO()
            with contextlib.redirect_stderr(error):
                code = driver.main([str(source), '--dump-candidate'])
            self.assertEqual(code, 2)
            self.assertIn('--keep-dir', error.getvalue())

    def test_explicit_dumps_record_only_written_artifacts_and_preserve_unknown(self):
        driver = self.require_interface()
        for requested, available in ((False, True), (True, True), (True, False)):
            with self.subTest(requested=requested, available=available), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                source, core = directory / 'input.smt2', directory / 'core'
                keep, report_path, model_path = directory / 'kept', directory / 'report.json', directory / 'model.smt2'
                source.write_text('(assert true)(check-sat)')
                core.write_text('''#!/usr/bin/env python3
import json, pathlib, sys
args = sys.argv[1:]
requested, available = %r, %r
assert ('--candidate-output' in args) == requested
assert ('--violations-output' in args) == requested
if requested and available:
    candidate = pathlib.Path(args[args.index('--candidate-output') + 1])
    violations = pathlib.Path(args[args.index('--violations-output') + 1])
    assert str(candidate).endswith('.candidate.json')
    assert str(violations).endswith('.violations.json')
    candidate.write_text(json.dumps({}))
    violations.write_text(json.dumps({'status': 'unknown', 'validated': False}))
print('unknown')
print(json.dumps({'status': 'unknown', 'validated': False,
                  'candidate_available': available,
                  'candidate_written': requested and available,
                  'violations_written': requested and available}), file=sys.stderr)
''' % (requested, available))
                core.chmod(0o755)
                output, error = io.StringIO(), io.StringIO()
                options = ['--dump-candidate'] if requested else []
                with patch.object(driver, 'CORE', core), contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                    code = driver.main([str(source), '--seconds', '.1', '--keep-dir', str(keep),
                                        '--json-report', str(report_path), '--model-output', str(model_path), *options])
                self.assertEqual(code, 0, error.getvalue())
                self.assertEqual(output.getvalue().strip(), 'unknown')
                report = json.loads(report_path.read_text())
                self.assertEqual(report['dump_candidate'], requested)
                self.assertFalse(report['model_output_written'])
                self.assertFalse(model_path.exists())
                query = report['last_query']
                self.assertEqual(query['dump_candidate'], requested)
                self.assertEqual(query['status'], 'unknown')
                self.assertFalse(query['validated'])
                self.assertFalse(query['original_model_validated'])
                for artifact in ('candidate', 'violations'):
                    if requested and available:
                        self.assertTrue(Path(query['artifacts'][artifact]).is_file())
                        self.assertTrue(Path(query['artifacts'][artifact]).is_relative_to(keep))
                    else:
                        self.assertNotIn(artifact, query['artifacts'])

    def test_real_unknown_candidate_is_unvalidated_and_has_exact_violation_report(self):
        driver = self.require_interface()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source, keep, report_path = directory / 'input.smt2', directory / 'kept', directory / 'report.json'
            source.write_text('(declare-const x Int)(assert (= (* x x) 2))(check-sat)')
            output, error = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                code = driver.main([str(source), '--dump-candidate', '--keep-dir', str(keep), '--seconds', '.03',
                                    '--json-report', str(report_path)])
            self.assertEqual(code, 0, error.getvalue())
            self.assertEqual(output.getvalue().strip(), 'unknown', error.getvalue())
            report = json.loads(report_path.read_text())
            query = report['last_query']
            self.assertFalse(query['validated'])
            self.assertTrue(query['core']['candidate_written'])
            self.assertTrue(query['core']['violations_written'])
            values = json.loads(Path(query['artifacts']['candidate']).read_text())
            self.assertTrue(values)
            self.assertTrue(all(isinstance(value, (str, bool)) for value in values.values()))
            violations = json.loads(Path(query['artifacts']['violations']).read_text())
            self.assertEqual(violations['status'], 'unknown')
            self.assertFalse(violations['validated'])
            self.assertGreater(violations['input_unsat_count'], 0)

    def test_real_sat_and_no_best_do_not_create_unknown_artifacts(self):
        driver = self.require_interface()
        for seconds, expected in (('0', 'unknown'), ('.1', 'sat')):
            with self.subTest(seconds=seconds), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                source, keep, report_path = directory / 'input.smt2', directory / 'kept', directory / 'report.json'
                source.write_text('(declare-const x Int)(assert (= x 7))(check-sat)')
                output, error = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                    code = driver.main([str(source), '--dump-candidate', '--keep-dir', str(keep),
                                        '--seconds', seconds, '--json-report', str(report_path)])
                self.assertEqual(code, 0, error.getvalue())
                self.assertEqual(output.getvalue().strip(), expected, error.getvalue())
                query = json.loads(report_path.read_text())['last_query']
                self.assertNotIn('candidate', query['artifacts'])
                self.assertNotIn('violations', query['artifacts'])
                self.assertFalse(query['core']['candidate_written'])
                self.assertFalse(query['core']['violations_written'])
                self.assertEqual(query['validated'], expected == 'sat')


if __name__ == '__main__':
    unittest.main()
