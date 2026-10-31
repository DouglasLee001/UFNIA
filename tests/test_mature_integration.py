"""Actual SMT-LIB -> full GMP search -> source-model validation checks.

These exercise the explicit backend through the public entry point. They do
not use a complete SMT solver as an oracle or mock a successful SLS result.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MATURE = ROOT / 'build/ufnia_ls/ufnia_mature'
BIG = (1 << 300) + 71


class MatureIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(MATURE.is_file(), 'run make -C UFNIA_LS mature first')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)

    def invoke(self, source, *options):
        path = self.work / 'source.smt2'
        report = self.work / 'report.json'
        model = self.work / 'model.smt2'
        path.write_text(source)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        proc = subprocess.run(
            [str(ROOT / 'run.sh'), str(path), '--backend', 'mature',
             '--seconds', '1', '--preprocess-timeout', '10',
             '--json-report', str(report), '--model-output', str(model),
             '--keep-dir', str(self.work / 'artifacts'), *map(str, options)],
            cwd=self.work, capture_output=True, text=True, timeout=25)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        info = json.loads(report.read_text())
        self.assertIs(info['complete_solver_used'], False)
        return proc, info, model

    def assert_original_sat(self, proc, info):
        self.assertEqual(proc.stdout.strip(), 'sat', proc.stderr)
        query = info['last_query']
        self.assertIs(query['validated'], True)
        self.assertIs(query['original_model_validated'], True)
        self.assertEqual(query['core']['backend'], 'mature_mpz')
        self.assertIs(query['core']['validated'], True)

    def test_quadratic_root_beyond_256_bits_has_a_source_model(self):
        proc, info, model = self.invoke(f'''
            (set-logic QF_NIA) (declare-const x Int)
            (assert (= (* x x) {BIG * BIG}))
            (assert (> x 0)) (check-sat)
        ''')
        self.assert_original_sat(proc, info)
        self.assertIn(str(BIG), model.read_text())

    def test_huge_negative_interval_is_not_clamped_to_machine_bounds(self):
        proc, info, model = self.invoke(f'''
            (set-logic QF_NIA) (declare-const x Int)
            (assert (<= x {-BIG})) (assert (>= x {-BIG - 2}))
            (assert (distinct x {-BIG - 1})) (check-sat)
        ''')
        self.assert_original_sat(proc, info)
        self.assertTrue(model.is_file())

    def test_negative_divisor_and_uf_model_restore_exactly(self):
        proc, info, model = self.invoke(f'''
            (set-logic QF_UFNIA) (declare-const x Int)
            (declare-fun f (Int) Int)
            (assert (= x {-BIG}))
            (assert (= (+ (* (- 7) (div x (- 7))) (mod x (- 7))) x))
            (assert (<= 0 (mod x (- 7)))) (assert (< (mod x (- 7)) 7))
            (assert (= (f x) (+ x 1)))
            (assert (> (f x) x)) (check-sat)
        ''')
        self.assert_original_sat(proc, info)
        self.assertIn('define-fun f', model.read_text())

    def test_actual_lazy_collision_is_refined_and_never_reported_sat(self):
        proc, info, model = self.invoke('''
            (set-logic QF_UFNIA) (declare-fun f (Int) Int)
            (declare-const x Int) (declare-const y Int) (declare-const z Int)
            (assert (<= x y)) (assert (<= y x))
            (assert (distinct (f x) (f y) (f z))) (check-sat)
        ''', '--ackermann-limit', '1')
        self.assertEqual(proc.stdout.strip(), 'unknown', proc.stderr)
        query = info['last_query']
        self.assertEqual(query['preprocessing']['uf_mode'], 'lazy')
        self.assertGreater(query['refinement_lemmas'], 0)
        self.assertGreaterEqual(query['rounds'], 2)
        self.assertIs(query['original_model_validated'], False)
        self.assertFalse(model.exists())

    def test_incremental_queries_restore_after_an_unknown_query(self):
        proc, info, model = self.invoke(f'''
            (set-logic QF_UFNIA) (declare-const x Int)
            (declare-fun f (Int) Int)
            (assert (= x {BIG})) (assert (= (f x) (+ x 1))) (check-sat)
            (push 1) (assert (= (f x) x)) (check-sat)
            (pop 1) (check-sat)
        ''', '--seconds', '.15')
        self.assertEqual(proc.stdout.split(), ['sat', 'unknown', 'sat'], proc.stderr)
        self.assertEqual(info['queries_processed'], 3)
        self.assertEqual(info['counts']['sat'], 2)
        self.assertEqual(info['counts']['unknown'], 1)
        text = model.read_text()
        self.assertIn('; query 1\n', text)
        self.assertIn('; query 3\n', text)
        self.assertNotIn('; query 2\n', text)

    def test_parse_only_reaches_the_actual_new_reader(self):
        proc, info, model = self.invoke(f'''
            (set-logic QF_NIA) (declare-const x Int)
            (assert (= (* x x) {BIG * BIG})) (check-sat)
        ''', '--parse-only')
        self.assertEqual(info['counts']['parsed'], 1, proc.stderr)
        self.assertEqual(info['last_query']['core']['status'], 'parsed')
        self.assertEqual(info['last_query']['core']['backend'], 'mature_mpz')
        self.assertFalse(model.exists())


if __name__ == '__main__':
    unittest.main()
