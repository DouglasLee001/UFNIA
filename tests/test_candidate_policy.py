"""Critical numeric values must survive the bounded native candidate pool."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CORE = Path(os.environ.get('UFNIA_CORE', ROOT / 'build/ufnia_ls/ufnia_core'))


def cap_formula():
    declarations = ''.join(f'(declare-const x{i} Int)(assert (>= x{i} 0))' for i in range(32))
    return declarations + '(assert (or ' + ' '.join(f'(= (* x{i} x{i}) 4)' for i in range(32)) + '))'


class CandidatePolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if 'UFNIA_CORE' not in os.environ:
            subprocess.run(['make', '-C', str(ROOT / 'UFNIA_LS'), '../build/ufnia_ls/ufnia_core'],
                           check=True, capture_output=True, text=True)

    def run_core(self, formula, *options):
        return subprocess.run([str(CORE), '--seconds', '0.08', '--seed', '0', *options],
                              input=formula, capture_output=True, text=True, timeout=10)

    def test_exact_signed_and_fallback_candidate_values(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'candidate-policy'
            compiled = subprocess.run(['c++', '-std=c++17', '-O1', '-I', str(ROOT / 'UFNIA_LS'),
                                       str(ROOT / 'tests/uf_candidate_policy_test.cpp'), '-o', str(binary),
                                       '-lz3', '-lgmpxx', '-lgmp'], capture_output=True, text=True, timeout=30)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertIn('critical candidate policies passed', checked.stdout)

    def test_native_broad_cap_misses_root_critical_solves_in_one_step(self):
        broad = self.run_core(cap_formula(), '--search-policy', 'greedy')
        self.assertEqual(broad.stdout.strip(), 'unknown', broad.stderr)
        critical = self.run_core(cap_formula(), '--search-policy', 'greedy', '--candidate-policy', 'critical')
        self.assertEqual(critical.stdout.strip(), 'sat', critical.stderr)
        stats = json.loads(critical.stderr.splitlines()[-1])
        self.assertEqual(stats['steps'], 1);self.assertGreater(stats['critical_candidates'], 0)

    def test_broad_is_default_and_does_no_extra_probes(self):
        result = self.run_core('(declare-const x Int)(assert (= (* x x) 4))', '--search-policy', 'greedy')
        self.assertEqual(result.stdout.strip(), 'sat', result.stderr)
        stats = json.loads(result.stderr.splitlines()[-1])
        self.assertEqual(stats['candidate_policy'], 'broad')
        self.assertEqual(stats['candidate_probes'], 0)
        self.assertEqual(stats['critical_candidates'], 0)
        self.assertEqual(stats['fallback_candidates'], 0)

    def test_invalid_candidate_policy_is_rejected(self):
        result = self.run_core('(assert true)', '--candidate-policy', 'invalid')
        self.assertEqual(result.returncode, 2);self.assertIn('broad or critical', result.stderr)

    def test_critical_routes_pure_nia_count_legacy_to_native(self):
        result = self.run_core(cap_formula(), '--candidate-policy', 'critical')
        self.assertEqual(result.stdout.strip(), 'sat', result.stderr)
        stats = json.loads(result.stderr.splitlines()[-1])
        self.assertEqual(stats['candidate_policy'], 'critical');self.assertEqual(stats['steps'], 1)
        self.assertGreater(stats['candidate_probes'], 0);self.assertGreater(stats['critical_candidates'], 0)


if __name__ == '__main__':
    unittest.main()
