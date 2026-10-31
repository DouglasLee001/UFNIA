"""Explicit unknown-candidate diagnostics remain separate from validated SAT models."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CORE = Path(os.environ.get('UFNIA_CORE', ROOT / 'build/ufnia_ls/ufnia_core'))


class CoreDiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if 'UFNIA_CORE' not in os.environ:
            subprocess.run(['make', '-C', str(ROOT / 'UFNIA_LS'), '../build/ufnia_ls/ufnia_core'],
                           check=True, capture_output=True, text=True)

    def run_core(self, formula, *options):
        return subprocess.run([str(CORE), '--seconds', '0.04', *options], input=formula,
                              capture_output=True, text=True, timeout=10)

    def test_unknown_candidate_and_true_remaining_atom_are_separate_from_sat_model(self):
        with tempfile.TemporaryDirectory() as directory:
            candidate, violations, model = [Path(directory) / name for name in ('candidate.json', 'violations.json', 'model.json')]
            formula = '(declare-const x Int)(assert (= (* x x) 2))'
            result = self.run_core(formula, '--candidate-output', str(candidate),
                                   '--violations-output', str(violations), '--model-output', str(model))
            self.assertEqual(result.stdout.strip(), 'unknown', result.stderr)
            self.assertFalse(model.exists())
            values = json.loads(candidate.read_text());details = json.loads(violations.read_text())
            self.assertNotEqual(int(values['x'])**2, 2)
            self.assertEqual(details['status'], 'unknown');self.assertFalse(details['validated'])
            self.assertEqual(details['input_unsat_count'], 1);self.assertEqual(details['working_unsat_count'], 1)
            self.assertEqual(details['uf_conflicts'], 0)
            self.assertIn('(* x x)', details['violations'][0]['expression'])
            self.assertEqual(details['violations'][0]['assignments']['x'], values['x'])
            stats = json.loads(result.stderr.splitlines()[-1])
            self.assertTrue(stats['candidate_written']);self.assertTrue(stats['violations_written'])
            self.assertFalse(stats['validated'])

    def test_diagnostics_use_reconstructed_substitution_values(self):
        with tempfile.TemporaryDirectory() as directory:
            candidate, violations = Path(directory) / 'candidate.json', Path(directory) / 'violations.json'
            result = self.run_core('(declare-const x Int)(declare-const y Int)(assert (= x (+ y 7)))'
                                   '(assert (= (* (- x 7) (- x 7)) 2))',
                                   '--candidate-output', str(candidate), '--violations-output', str(violations))
            self.assertEqual(result.stdout.strip(), 'unknown', result.stderr)
            values = json.loads(candidate.read_text());details = json.loads(violations.read_text())
            self.assertEqual(int(values['x']), int(values['y'])+7)
            self.assertEqual(details['input_unsat_count'], 1)
            inputs = [v for v in details['violations'] if v['scope']=='input']
            self.assertTrue(inputs)
            self.assertEqual(inputs[0]['assignments']['x'], values['x'])
            for violation in details['violations']:
                for name, value in violation['assignments'].items():self.assertEqual(value, values[name])

    def test_no_best_candidate_does_not_create_debug_files(self):
        with tempfile.TemporaryDirectory() as directory:
            candidate, violations = Path(directory) / 'candidate.json', Path(directory) / 'violations.json'
            result = self.run_core('(declare-const x Int)(assert (= (* x x) 2))', '--seconds', '0',
                                   '--candidate-output', str(candidate), '--violations-output', str(violations))
            self.assertEqual(result.stdout.strip(), 'unknown', result.stderr)
            self.assertFalse(candidate.exists());self.assertFalse(violations.exists())
            stats = json.loads(result.stderr.splitlines()[-1]);self.assertFalse(stats['candidate_available'])
            self.assertFalse(stats['candidate_written']);self.assertFalse(stats['violations_written'])

    def test_diagnostic_limit_and_exact_uf_conflicts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'violations.json'
            formula = ''.join(f'(declare-const x{i} Int)(assert (= (* x{i} x{i}) 2))' for i in range(11))
            result = self.run_core(formula, '--violations-output', str(path))
            self.assertEqual(result.stdout.strip(), 'unknown', result.stderr)
            details = json.loads(path.read_text());self.assertEqual(details['input_unsat_count'], 11)
            self.assertEqual(details['working_unsat_count'], 11)
            self.assertLessEqual(len(details['violations']), 8);self.assertTrue(details['truncated'])
            result = self.run_core('(declare-fun __ufnia_observe_f (Int Int) Bool)'
                                   '(assert (__ufnia_observe_f 0 0))(assert (__ufnia_observe_f 0 1))',
                                   '--uf-observers', '--violations-output', str(path))
            self.assertEqual(result.stdout.strip(), 'unknown', result.stderr)
            details = json.loads(path.read_text());self.assertEqual(details['uf_conflicts'], 1)
            self.assertEqual(details['input_unsat_count'], 0);self.assertEqual(details['violations'], [])

    def test_boolean_candidate_values_stay_boolean(self):
        with tempfile.TemporaryDirectory() as directory:
            candidate, violations = Path(directory) / 'candidate.json', Path(directory) / 'violations.json'
            result = self.run_core('(declare-const b Bool)(declare-const c Bool)'
                                   '(assert (not b))(assert (not c))(assert (or b c))',
                                   '--candidate-output', str(candidate), '--violations-output', str(violations))
            self.assertEqual(result.stdout.strip(), 'unknown', result.stderr)
            values = json.loads(candidate.read_text());details = json.loads(violations.read_text())
            self.assertIs(values['b'], False);self.assertIs(values['c'], False)
            for violation in details['violations']:
                for value in violation['assignments'].values():self.assertIsInstance(value, bool)

    def test_default_unknown_writes_no_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([str(CORE), '--seconds', '0.02'],
                                    input='(declare-const x Int)(assert (= (* x x) 2))',
                                    capture_output=True, text=True, cwd=directory, timeout=10)
            self.assertEqual(result.stdout.strip(), 'unknown', result.stderr)
            self.assertEqual(list(Path(directory).iterdir()), [])
            stats = json.loads(result.stderr.splitlines()[-1])
            self.assertFalse(stats['candidate_written']);self.assertFalse(stats['violations_written'])

    def test_candidate_and_validated_model_paths_must_differ(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'same.json'
            result = self.run_core('(assert false)', '--model-output', str(path), '--candidate-output', str(path))
            self.assertEqual(result.returncode, 2);self.assertFalse(path.exists())
            self.assertIn('different paths', result.stderr)

    def test_sat_does_not_write_unknown_candidate_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            candidate, violations = Path(directory) / 'candidate.json', Path(directory) / 'violations.json'
            result = self.run_core('(declare-const x Int)(assert (= x 3))',
                                   '--candidate-output', str(candidate), '--violations-output', str(violations))
            self.assertEqual(result.stdout.strip(), 'sat', result.stderr)
            self.assertFalse(candidate.exists());self.assertFalse(violations.exists())
            stats = json.loads(result.stderr.splitlines()[-1]);self.assertTrue(stats['validated'])
            self.assertFalse(stats['candidate_written']);self.assertFalse(stats['violations_written'])


if __name__ == '__main__':
    unittest.main()
