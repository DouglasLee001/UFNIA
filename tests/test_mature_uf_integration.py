"""Public-entry integration of the mature search loop and live UF feedback."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MatureUFIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def invoke(self, source, interaction='joint', scoring='paws', seconds='1'):
        path = self.directory / 'source.smt2'
        report = self.directory / 'report.json'
        model = self.directory / 'model.smt2'
        path.write_text(source)
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        proc = subprocess.run([
            str(ROOT / 'run.sh'), str(path), '--backend', 'mature', '--uf-observers',
            '--interaction', interaction, '--scoring', scoring,
            '--seconds', seconds, '--preprocess-timeout', '5',
            '--model-output', str(model), '--json-report', str(report),
            '--keep-dir', str(self.directory / 'artifacts'),
        ], cwd=self.directory, text=True, capture_output=True, timeout=20)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        info = json.loads(report.read_text())
        self.assertIs(info['complete_solver_used'], False)
        return proc, info, model

    def assert_source_sat(self, proc, info, model):
        self.assertEqual(proc.stdout.strip(), 'sat', proc.stderr)
        query = info['last_query']
        self.assertIs(query['original_model_validated'], True)
        self.assertIs(query['validated'], True)
        self.assertEqual(query['core']['backend'], 'mature_mpz')
        self.assertEqual(query['core']['backend_version'], 'mature-mpz-v2')
        self.assertIs(query['core']['uf_observers'], True)
        self.assertEqual(query['core']['uf_conflicts'], 0)
        self.assertEqual(query['core']['validation_scope'], 'native_cnf_and_uf_congruence')
        self.assertTrue(model.is_file())

    def test_argument_separation_in_both_feedback_strategies(self):
        source = '''
            (set-logic QF_UFNIA) (declare-const x Int) (declare-const y Int)
            (declare-fun f (Int) Int)
            (assert (>= x 0)) (assert (>= y 0))
            (assert (= (f x) 1)) (assert (= (f y) 2)) (check-sat)
        '''
        for interaction, scoring in (('soft', 'count'), ('joint', 'paws')):
            with self.subTest(interaction=interaction, scoring=scoring):
                proc, info, model = self.invoke(source, interaction, scoring)
                self.assert_source_sat(proc, info, model)
                self.assertEqual(info['last_query']['rounds'], 1)
                self.assertGreater(info['last_query']['core']['uf_separation_moves'], 0)

    def test_none_feedback_does_not_publish_an_inconsistent_uf_model(self):
        proc, info, model = self.invoke('''
            (set-logic QF_UFNIA) (declare-const x Int) (declare-const y Int)
            (declare-fun f (Int) Int)
            (assert (>= x 0)) (assert (>= y 0))
            (assert (= (f x) 1)) (assert (= (f y) 2)) (check-sat)
        ''', interaction='none', scoring='count', seconds='.08')
        self.assertEqual(proc.stdout.strip(), 'unknown', proc.stderr)
        self.assertIs(info['last_query']['original_model_validated'], False)
        self.assertFalse(model.exists())

    def test_observer_only_boolean_argument_can_be_flipped(self):
        proc, info, model = self.invoke('''
            (set-logic QF_UFNIA) (declare-const b Bool)
            (declare-fun f (Bool) Bool)
            (assert (f false)) (assert (not (f b))) (check-sat)
        ''')
        self.assert_source_sat(proc, info, model)
        self.assertIn('define-fun f', model.read_text())
        self.assertGreater(info['last_query']['core']['uf_separation_moves'], 0)

    def test_compound_arguments_keep_their_exact_arithmetic_definitions(self):
        proc, info, model = self.invoke('''
            (set-logic QF_UFNIA)
            (declare-const x Int) (declare-const y Int) (declare-const z Int)
            (declare-fun f (Int) Int)
            (assert (>= x 0)) (assert (>= y 0)) (assert (>= z 0))
            (assert (= (f (+ x y)) 1)) (assert (= (f (* 2 z)) 2)) (check-sat)
        ''')
        self.assert_source_sat(proc, info, model)

    def test_nested_uf_reconstruction_with_huge_result_values(self):
        big = (1 << 300) + 71
        proc, info, model = self.invoke(f'''
            (set-logic QF_UFNIA) (declare-const x Int) (declare-const y Int)
            (declare-fun g (Int) Int) (declare-fun f (Int) Int)
            (assert (>= x 0)) (assert (>= y 0))
            (assert (= (g x) 0)) (assert (= (g y) 1))
            (assert (= (f (g x)) {big})) (assert (= (f (g y)) {big + 1}))
            (check-sat)
        ''')
        self.assert_source_sat(proc, info, model)
        text = model.read_text()
        self.assertIn('define-fun f', text)
        self.assertIn('define-fun g', text)
        self.assertIn(str(big), text)

    def test_nonlinear_argument_can_leave_a_zero_product_assignment(self):
        proc, info, model = self.invoke('''
            (set-logic QF_UFNIA) (declare-const x Int) (declare-const y Int)
            (declare-fun f (Int) Int)
            (assert (>= x 0)) (assert (>= y 0))
            (assert (= (f (* x y)) 1)) (assert (= (f 0) 2)) (check-sat)
        ''')
        self.assert_source_sat(proc, info, model)

    def test_implied_equal_arguments_with_distinct_results_never_claim_sat(self):
        proc, info, model = self.invoke('''
            (set-logic QF_UFNIA) (declare-const x Int) (declare-const y Int)
            (declare-fun f (Int) Int)
            (assert (<= x y)) (assert (<= y x))
            (assert (= (f x) 1)) (assert (= (f y) 2)) (check-sat)
        ''', seconds='.08')
        self.assertEqual(proc.stdout.strip(), 'unknown', proc.stderr)
        self.assertIs(info['last_query']['original_model_validated'], False)
        self.assertFalse(model.exists())


if __name__ == '__main__':
    unittest.main()
