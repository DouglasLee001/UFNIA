"""Native UF observations must participate in search and exact validation."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CORE = Path(os.environ.get('UFNIA_CORE', ROOT / 'build/ufnia_ls/ufnia_core'))


class NativeUfCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if 'UFNIA_CORE' not in os.environ:
            subprocess.run(['make', '-C', str(ROOT / 'UFNIA_LS'), '../build/ufnia_ls/ufnia_core'], check=True,
                           capture_output=True, text=True)

    def solve(self, body, *options):
        return subprocess.run([str(CORE), '--uf-observers', '--seconds', '0.12', '--seed', '4', *options],
                              input='(set-logic QF_UFNIA)\n' + body, text=True,
                              capture_output=True, timeout=10)

    def test_constant_collision_is_never_sat(self):
        result = self.solve('(declare-fun __ufnia_observe_f (Int Int) Bool)'
                            '(assert (__ufnia_observe_f 0 0))(assert (__ufnia_observe_f 0 1))')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'unknown', result.stderr)
        self.assertFalse(json.loads(result.stderr.splitlines()[-1])['validated'])

    def test_collision_separates_otherwise_unconstrained_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / 'model.json'
            result = self.solve('(declare-const x Int)(declare-const y Int)'
                                '(declare-fun __ufnia_observe_f (Int Int) Bool)'
                                '(assert (and (__ufnia_observe_f x 0)(__ufnia_observe_f y 1)))',
                                '--model-output', str(model))
            self.assertEqual(result.stdout.strip(), 'sat', result.stderr)
            values = json.loads(model.read_text())
            self.assertNotEqual(int(values['x']), int(values['y']))

    def test_preprocessing_rewrites_observed_results(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / 'model.json'
            result = self.solve('(declare-const x Int)(declare-const y Int)(declare-const u Int)'
                                '(declare-fun __ufnia_observe_f (Int Int) Bool)'
                                '(assert (= x 7))(assert (= u (+ y 3)))'
                                '(assert (__ufnia_observe_f x u))(assert (__ufnia_observe_f 7 10))',
                                '--model-output', str(model))
            self.assertEqual(result.stdout.strip(), 'sat', result.stderr)
            values = json.loads(model.read_text())
            self.assertEqual(int(values['u']), 10)
            self.assertEqual(int(values['y']), 7)

    def test_group_result_repair_crosses_single_variable_plateau(self):
        result = self.solve('(declare-const u Int)(declare-const v Int)'
                            '(declare-fun __ufnia_observe_f (Int Int) Bool)'
                            '(assert (__ufnia_observe_f 0 u))(assert (__ufnia_observe_f 0 v))'
                            '(assert (__ufnia_observe_f 0 1))')
        self.assertEqual(result.stdout.strip(), 'sat', result.stderr)
        stats = json.loads(result.stderr.splitlines()[-1])
        self.assertEqual(stats['steps'], 1)
        self.assertGreater(stats['uf_joint_moves'], 0)

    def test_boolean_observations_and_function_identity(self):
        result = self.solve('(declare-const b Bool)'
                            '(declare-fun __ufnia_observe_f (Bool Bool) Bool)'
                            '(declare-fun __ufnia_observe_g (Bool Bool) Bool)'
                            '(assert (__ufnia_observe_f false b))(assert (__ufnia_observe_f false true))'
                            '(assert (__ufnia_observe_g false false))')
        self.assertEqual(result.stdout.strip(), 'sat', result.stderr)

    def test_boolean_result_repairs_integer_comparison(self):
        result = self.solve('(declare-const x Int)'
                            '(declare-fun __ufnia_observe_f (Int Bool) Bool)'
                            '(assert (__ufnia_observe_f 0 (< x 100)))'
                            '(assert (__ufnia_observe_f 0 false))')
        self.assertEqual(result.stdout.strip(), 'sat', result.stderr)

    def test_boolean_result_can_repair_multiple_conjuncts(self):
        result = self.solve('(declare-const p Bool)(declare-const q Bool)'
                            '(declare-fun __ufnia_observe_f (Int Bool) Bool)'
                            '(assert (__ufnia_observe_f 0 (and p q)))'
                            '(assert (__ufnia_observe_f 0 true))')
        self.assertEqual(result.stdout.strip(), 'sat', result.stderr)

    def test_multilinear_argument_can_activate_multiple_zero_factors(self):
        result = self.solve('(declare-const x Int)(declare-const y Int)(declare-const z Int)'
                            '(declare-fun __ufnia_observe_f (Int Int) Bool)'
                            '(assert (__ufnia_observe_f (* x y z) 0))'
                            '(assert (__ufnia_observe_f 0 1))')
        self.assertEqual(result.stdout.strip(), 'sat', result.stderr)

    def test_substitution_chain_is_composed_for_observers(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / 'model.json'
            result = self.solve('(declare-const x Int)(declare-const y Int)(declare-const z Int)'
                                '(declare-fun __ufnia_observe_f (Int Int) Bool)'
                                '(assert (= x (+ y 7)))(assert (= y (+ z 3)))'
                                '(assert (__ufnia_observe_f 0 x))(assert (__ufnia_observe_f 0 15))',
                                '--model-output', str(model))
            self.assertEqual(result.stdout.strip(), 'sat', result.stderr)
            values = json.loads(model.read_text())
            self.assertEqual([int(values[name]) for name in ('x', 'y', 'z')], [15, 8, 5])

    def test_incremental_joint_trials_match_full_exact_evaluation(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'uf-scoring'
            compiled = subprocess.run(['c++', '-std=c++17', '-O1', '-I', str(ROOT / 'UFNIA_LS'),
                                       str(ROOT / 'tests/uf_scoring_test.cpp'), '-o', str(binary),
                                       '-lz3', '-lgmpxx', '-lgmp'], capture_output=True, text=True, timeout=30)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertIn('1200 exact joint trials passed', checked.stdout)

    def test_paws_updates_weights_without_claiming_impossible_square_sat(self):
        result = self.solve('(declare-const x Int)(assert (= (* x x) 2))', '--scoring', 'paws')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'unknown', result.stderr)
        stats = json.loads(result.stderr.splitlines()[-1])
        self.assertGreater(stats['weight_updates'], 0)
        self.assertFalse(stats['validated'])

    def test_count_scoring_remains_available_for_ablation(self):
        result = self.solve('(declare-const u Int)(declare-const v Int)'
                            '(declare-fun __ufnia_observe_f (Int Int) Bool)'
                            '(assert (__ufnia_observe_f 0 u))(assert (__ufnia_observe_f 0 v))'
                            '(assert (__ufnia_observe_f 0 1))', '--scoring', 'count')
        self.assertEqual(result.stdout.strip(), 'sat', result.stderr)
        self.assertEqual(json.loads(result.stderr.splitlines()[-1])['weight_updates'], 0)

    def test_candidate_cap_keeps_late_boolean_guard(self):
        declarations = ''.join(f'(declare-const x{i} Int)' for i in range(24))
        impossible = ' '.join(f'(= (* x{i} x{i}) (- 1))' for i in range(24))
        result = self.solve(declarations + '(declare-const guard Bool)'
                            '(declare-fun __ufnia_observe_f (Int Int) Bool)'
                            '(assert (__ufnia_observe_f 0 0))'
                            f'(assert (or {impossible} guard))')
        self.assertEqual(result.stdout.strip(), 'sat', result.stderr)
        self.assertEqual(json.loads(result.stderr.splitlines()[-1])['steps'], 1)

    def test_shared_boolean_dag_distance_is_linear(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'uf-distance'
            compiled = subprocess.run(['c++', '-std=c++17', '-O1', '-I', str(ROOT / 'UFNIA_LS'),
                                       str(ROOT / 'tests/uf_distance_test.cpp'), '-o', str(binary),
                                       '-lz3', '-lgmpxx', '-lgmp'], capture_output=True, text=True, timeout=30)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertIn('structural and shared-DAG distances passed', checked.stdout)

    def test_structural_distance_can_be_selected(self):
        result = self.solve('(declare-const p Bool)(declare-const q Bool)'
                            '(declare-fun __ufnia_observe_f (Int Bool) Bool)'
                            '(assert (__ufnia_observe_f 0 (and p q)))'
                            '(assert (__ufnia_observe_f 0 true))', '--distance', 'structural')
        self.assertEqual(result.stdout.strip(), 'sat', result.stderr)

    def test_integer_auxiliary_joint_repair_platforms(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'uf-arithmetic-repair'
            compiled = subprocess.run(['c++', '-std=c++17', '-O1', '-I', str(ROOT / 'UFNIA_LS'),
                                       str(ROOT / 'tests/uf_arithmetic_repair_test.cpp'), '-o', str(binary),
                                       '-lz3', '-lgmpxx', '-lgmp'], capture_output=True, text=True, timeout=30)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertIn('integer auxiliary repairs passed', checked.stdout)

    def test_arithmetic_repair_cli_and_cached_progress_diagnostics(self):
        result = self.solve('(declare-const x Int)(assert (= (* x x) 2))',
                            '--arithmetic-repair', 'quotient')
        self.assertEqual(result.stdout.strip(), 'unknown', result.stderr)
        stats = json.loads(result.stderr.splitlines()[-1])
        self.assertGreater(stats['initial_arithmetic_unsat'], 0)
        self.assertGreater(stats['best_arithmetic_unsat'], 0)
        self.assertIn('arith_joint_moves', stats)
        self.assertIn('arith_repair_proposals', stats)

    def test_invalid_arithmetic_repair_modes_are_rejected(self):
        for option in ('--arithmetic-repair', '--active-arithmetic-rows'):
            result = self.solve('(assert true)', option, 'invalid')
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn('error', result.stderr)

    def test_search_policy_resumes_greedy_and_reselects_after_paws(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'uf-search-policy'
            compiled = subprocess.run(['c++', '-std=c++17', '-O1', '-I', str(ROOT / 'UFNIA_LS'),
                                       str(ROOT / 'tests/uf_search_policy_test.cpp'), '-o', str(binary),
                                       '-lz3', '-lgmpxx', '-lgmp'], capture_output=True, text=True, timeout=30)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertIn('search policy traces passed', checked.stdout)

    def test_greedy_search_policy_cli(self):
        result = self.solve('(declare-const x Int)(assert (= (* x x) 4))', '--search-policy', 'greedy')
        self.assertEqual(result.stdout.strip(), 'sat', result.stderr)
        stats = json.loads(result.stderr.splitlines()[-1])
        self.assertEqual(stats['search_policy'], 'greedy')
        self.assertGreater(stats['greedy_evaluations'], 0)

    def test_boolean_integer_phases_accept_regression_then_finish(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'uf-phases'
            compiled = subprocess.run(['c++', '-std=c++17', '-O1', '-I', str(ROOT / 'UFNIA_LS'),
                                       str(ROOT / 'tests/uf_phase_test.cpp'), '-o', str(binary),
                                       '-lz3', '-lgmpxx', '-lgmp'], capture_output=True, text=True, timeout=30)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertIn('Boolean integer phase traces passed', checked.stdout)

    def test_alternating_phase_requires_greedy_policy(self):
        result = self.solve('(assert true)', '--search-phase', 'alternating')
        self.assertEqual(result.returncode, 2)
        self.assertIn('requires', result.stderr)
        self.assertIn('greedy', result.stderr)

    def test_alternating_finishes_cnf_guard_platform_that_mixed_undoes(self):
        declarations = '(declare-const b Bool)(declare-const c Bool)' + ''.join(
            f'(declare-const x{i} Int)' for i in range(6))
        bindings = ''.join(f'(assert (or (not {guard}) (>= x{i} 100)))'
                           f'(assert (or {guard} (not (>= x{i} 100))))'
                           for guard, group in (('b', range(3)), ('c', range(3, 6))) for i in group)
        body = declarations + '(assert (or b c))' + bindings
        mixed = self.solve(body, '--search-policy', 'greedy', '--search-phase', 'mixed')
        self.assertEqual(mixed.stdout.strip(), 'unknown', mixed.stderr)
        alternate = self.solve(body, '--search-policy', 'greedy', '--search-phase', 'alternating')
        self.assertEqual(alternate.stdout.strip(), 'sat', alternate.stderr)
        stats = json.loads(alternate.stderr.splitlines()[-1])
        self.assertEqual(stats['initial_arithmetic_unsat'], 1)
        self.assertGreater(stats['bool_phase_steps'], 0)
        self.assertGreaterEqual(stats['int_phase_steps'], 3)
        self.assertGreater(stats['phase_switches'], 0)
        self.assertTrue(stats['validated'])

    def test_observation_marker_requires_argument_and_result(self):
        result = self.solve('(declare-fun __ufnia_observe_f (Int) Bool)'
                            '(assert (__ufnia_observe_f 0))')
        self.assertEqual(result.stdout.strip(), 'unknown')
        self.assertEqual(json.loads(result.stderr.splitlines()[-1])['status'], 'unsupported')

    def test_multilinear_activation_under_addition(self):
        result = self.solve('(declare-const x Int)(declare-const y Int)(declare-const z Int)'
                            '(declare-fun __ufnia_observe_f (Int Int) Bool)'
                            '(assert (__ufnia_observe_f (+ 5 (* x y z)) 0))'
                            '(assert (__ufnia_observe_f 5 1))')
        self.assertEqual(result.stdout.strip(), 'sat', result.stderr)

    def test_observer_marker_is_rejected_outside_positive_top_level(self):
        result = self.solve('(declare-fun __ufnia_observe_f (Int Int) Bool)'
                            '(assert (not (__ufnia_observe_f 0 1)))')
        self.assertEqual(result.stdout.strip(), 'unknown')
        self.assertEqual(json.loads(result.stderr.splitlines()[-1])['status'], 'unsupported')

    def test_observer_marker_requires_explicit_flag(self):
        result = subprocess.run([str(CORE), '--seconds', '0.01'],
                                input='(declare-fun __ufnia_observe_f (Int Int) Bool)'
                                      '(assert (__ufnia_observe_f 0 0))',
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.stdout.strip(), 'unknown')
        self.assertEqual(json.loads(result.stderr.splitlines()[-1])['status'], 'unsupported')


if __name__ == '__main__':
    unittest.main()
