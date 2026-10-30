import hashlib
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import z3


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / 'run.sh'
CORE = ROOT / 'build/ufnia_ls/ufnia_core'
BIG = (1 << 180) + 12345


class DriverTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(RUNNER.is_file(), 'unified UFNIA entry point is missing')
        self.assertTrue(CORE.is_file(), 'build the SLS core before driver tests')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def run_input(self, source, *options):
        path = self.directory / 'source.smt2'
        path.write_text(source)
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        proc = subprocess.run([str(RUNNER), str(path), *map(str, options)],
                              cwd=self.directory, capture_output=True, text=True, timeout=20)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)
        return path, proc

    def test_bigint_uf_sat_restores_and_validates_original_model(self):
        report = self.directory / 'report.json'
        model = self.directory / 'model.smt2'
        _, proc = self.run_input(f'''
            (set-logic QF_UFNIA)(declare-const x Int)(declare-fun f (Int) Int)
            (assert (= x {BIG}))(assert (= (f x) {BIG + 1}))
            (assert (> (f x) x))(check-sat)
        ''', '--seconds', '1', '--json-report', report, '--model-output', model)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), 'sat', proc.stderr)
        info = json.loads(report.read_text())
        self.assertTrue(info['last_query']['original_model_validated'])
        self.assertTrue(info['last_query']['core']['validated'])
        self.assertIn(str(BIG), model.read_text())
        self.assertIn('define-fun f', model.read_text())

    def test_uf_same_arguments_contradiction_returns_unknown(self):
        report = self.directory / 'report.json'
        _, proc = self.run_input('''
            (set-logic QF_UFNIA)(declare-fun f (Int) Int)
            (assert (= (f 2) 1))(assert (= (f (+ 1 1)) 2))(check-sat)
        ''', '--seconds', '.05', '--json-report', report)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), 'unknown')
        self.assertFalse(json.loads(report.read_text())['last_query']['original_model_validated'])

    def test_actual_lazy_congruence_refinement_rejects_inconsistent_candidate(self):
        report = self.directory / 'lazy.json'
        _, proc = self.run_input('''
            (declare-fun f (Int) Int)
            (declare-const x Int)(declare-const y Int)(declare-const z Int)
            (assert (<= x y))(assert (<= y x))
            (assert (distinct (f x) (f y) (f z)))(check-sat)
        ''', '--seconds', '1', '--ackermann-limit', '1', '--json-report', report)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), 'unknown')
        info = json.loads(report.read_text())['last_query']
        self.assertEqual(info['preprocessing']['uf_mode'], 'lazy')
        self.assertGreater(info['refinement_lemmas'], 0, proc.stderr)
        self.assertGreaterEqual(info['rounds'], 2)

    def test_actual_lazy_uf_model_is_restored_and_checked(self):
        report = self.directory / 'lazy-sat.json'
        model = self.directory / 'model.smt2'
        _, proc = self.run_input('''
            (declare-fun f (Int) Int)
            (assert (= (f 0) 7))(assert (= (f 1) 8))
            (assert (= (f 2) 9))(check-sat)
        ''', '--seconds', '1', '--ackermann-limit', '1', '--json-report', report, '--model-output', model)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), 'sat', proc.stderr)
        info = json.loads(report.read_text())['last_query']
        self.assertEqual(info['preprocessing']['uf_mode'], 'lazy')
        self.assertTrue(info['original_model_validated'])
        self.assertIn('define-fun f', model.read_text())

    def test_opaque_carrier_parse_uses_actual_core(self):
        _, proc = self.run_input('''
            (set-logic QF_UFNIA)(declare-sort U 0)
            (declare-const a U)(declare-const b U)(assert (distinct a b))(check-sat)
        ''', '--parse-only')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        info = json.loads(proc.stdout)
        self.assertEqual(info['status'], 'parsed')
        self.assertEqual(info['last_query']['core']['status'], 'parsed')
        self.assertTrue(info['last_query']['preprocessing']['carrier_sort_encoding'])
        self.assertIn('carrier', info['last_query']['model_limitation'])

    def test_large_integer_survives_preprocessing_and_core_parse(self):
        keep = self.directory / 'kept'
        _, proc = self.run_input(f'''
            (declare-const x Int)(assert (> (* x x) {BIG}))(check-sat)
        ''', '--parse-only', '--keep-dir', keep)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        info = json.loads(proc.stdout)
        self.assertGreaterEqual(info['last_query']['core']['max_numeral_bits'], 180)
        self.assertTrue(list(keep.rglob('*.normalized.smt2')))
        self.assertTrue(list(keep.rglob('*.metadata.json')))

    def test_incremental_queries_keep_their_own_context(self):
        report = self.directory / 'report.json'
        source = '(declare-const p Bool)(assert p)(check-sat)(push 1)(assert (not p))(check-sat)(pop 1)(check-sat)'
        _, proc = self.run_input(source, '--seconds', '.05', '--json-report', report)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.split(), ['sat', 'unknown', 'sat'])
        info = json.loads(report.read_text())
        self.assertEqual(info['counts']['sat'], 2)
        self.assertEqual(info['queries_processed'], 3)

    def test_query_selection_processes_only_requested_query(self):
        source = '(assert true)(check-sat)(assert false)(check-sat)(check-sat)'
        _, proc = self.run_input(source, '--parse-only', '--query', 2)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        info = json.loads(proc.stdout)
        self.assertEqual(info['queries_processed'], 1)
        self.assertEqual(info['last_query']['index'], 2)

    def test_lazy_refinement_uses_the_stable_conversion_mapping(self):
        from UFNIA_LS import run as driver
        source = self.directory / 'source.smt2'
        source.write_text('(assert true)(check-sat)')
        refinements = []
        refinement_timeouts = []
        class Conversion:
            normalized_smt2 = '(set-logic QF_NIA)(assert true)(check-sat)'
            metadata = {'uf_mode': 'lazy', 'uf_applications': []}
            def __init__(self, valid=False):
                self.valid = valid
            def restore_model(self, values):
                return z3.Model(), self.valid
            def congruence_conflicts(self, values):
                return [z3.BoolVal(True)]
            def refine(self, lemmas, timeout_ms=None):
                refinements.append(tuple(lemmas))
                refinement_timeouts.append(timeout_ms)
                return Conversion(valid=True)
        out, err = io.StringIO(), io.StringIO()
        with patch.object(driver, 'convert_assertions', return_value=Conversion()) as convert, \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = driver.main([str(source), '--seconds', '1'])
        self.assertEqual(code, 0, err.getvalue())
        self.assertEqual(out.getvalue().strip(), 'sat', err.getvalue())
        self.assertEqual(convert.call_count, 1)
        self.assertEqual(len(refinements), 1)
        self.assertIsNotNone(refinement_timeouts[0])
        self.assertGreater(refinement_timeouts[0], 0)
        self.assertLessEqual(refinement_timeouts[0], 1000)

    def test_invalid_core_model_never_becomes_sat(self):
        from UFNIA_LS import run as driver
        source = self.directory / 'source.smt2'
        source.write_text('(declare-const x Int)(assert (> x 5))(check-sat)')
        fake = self.directory / 'fake-core'
        fake.write_text('''#!/usr/bin/env python3
import json, sys
path = sys.argv[sys.argv.index('--model-output') + 1]
with open(path, 'w') as out:
    json.dump({'i_0': '0'}, out)
print('sat')
print(json.dumps({'status': 'sat', 'validated': True}), file=sys.stderr)
''')
        fake.chmod(0o755)
        out, err = io.StringIO(), io.StringIO()
        with patch.object(driver, 'CORE', fake), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = driver.main([str(source), '--seconds', '.1'])
        self.assertEqual(code, 0, err.getvalue())
        self.assertEqual(out.getvalue().strip(), 'unknown')
        self.assertIn('model', err.getvalue())

    def test_missing_core_explains_build_command(self):
        from UFNIA_LS import run as driver
        source = self.directory / 'source.smt2'
        source.write_text('(assert true)(check-sat)')
        err = io.StringIO()
        with patch.object(driver, 'CORE', self.directory / 'missing-core'), contextlib.redirect_stderr(err):
            code = driver.main([str(source), '--parse-only'])
        self.assertEqual(code, 2)
        self.assertIn('make -C UFNIA_LS', err.getvalue())

    def test_output_filesystem_errors_are_reported_without_traceback(self):
        from UFNIA_LS import run as driver
        source = self.directory / 'source.smt2'
        source.write_text('(assert true)(check-sat)')
        blocker = self.directory / 'regular-file'
        blocker.write_text('preserve me')
        for flag in ('--json-report', '--keep-dir'):
            with self.subTest(flag=flag):
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = driver.main([str(source), '--parse-only', flag, str(blocker / 'output')])
                self.assertEqual(code, 2)
                self.assertIn('error:', err.getvalue())
                self.assertEqual(blocker.read_text(), 'preserve me')

    def test_output_aliases_cannot_overwrite_source(self):
        for flag in ('--model-output', '--json-report', '--keep-dir'):
            with self.subTest(flag=flag):
                path = self.directory / 'source.smt2'
                path.write_text('(assert true)(check-sat)')
                before = path.read_bytes()
                proc = subprocess.run([str(RUNNER), str(path), '--parse-only', flag, str(path)],
                                      capture_output=True, text=True, timeout=20)
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn('input', proc.stderr.lower())
                self.assertEqual(path.read_bytes(), before)
        alias = self.directory / 'hardlink.json'
        alias.hardlink_to(path)
        proc = subprocess.run([str(RUNNER), str(path), '--parse-only', '--json-report', str(alias)],
                              capture_output=True, text=True, timeout=20)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
