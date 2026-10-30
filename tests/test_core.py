"""Regression coverage for the arbitrary-precision local-search core."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CORE = Path(os.environ.get("UFNIA_CORE", ROOT / "build/ufnia_ls/ufnia_core"))


class CoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if "UFNIA_CORE" not in os.environ:
            subprocess.run(["make", "-C", str(ROOT / "UFNIA_LS")], check=True, capture_output=True, text=True)

    def run_core(self, formula, *options):
        return subprocess.run([str(CORE), *options], input=formula, text=True, capture_output=True, timeout=10)

    def solve(self, body, *options):
        return self.run_core("(set-logic QF_NIA)\n" + body + "\n(check-sat)\n", "--seconds", "0.08", "--seed", "4", *options)

    def test_huge_square_root_uses_real_search(self):
        root = (1 << 256) + 37
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / "model.json"
            result = self.solve(f"(declare-const x Int)(assert (= (* x x) {root * root}))", "--model-output", str(model))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "sat", result.stderr)
            diagnostics = json.loads(result.stderr.strip().splitlines()[-1])
            self.assertGreater(diagnostics["steps"], 0)
            self.assertTrue(diagnostics["validated"])
            value = int(json.loads(model.read_text())["x"])
            self.assertEqual(value * value, root * root)

    def test_big_linear_and_boolean_model(self):
        huge = (1 << 300) + 99
        result = self.solve(f"(declare-const x Int)(declare-const b Bool)(assert b)(assert (= x {huge}))")
        self.assertEqual(result.stdout.strip(), "sat", result.stderr)
        self.assertTrue(json.loads(result.stderr.strip().splitlines()[-1])["validated"])

    def test_quadratic_integer_inequality(self):
        n = 1 << 200
        result = self.solve(f"(declare-const x Int)(assert (> (* x x) {n * n}))(assert (< x 0))")
        self.assertEqual(result.stdout.strip(), "sat", result.stderr)

    def test_false_and_unsatisfiable_square_do_not_claim_sat(self):
        for body in ("(assert false)", "(declare-const x Int)(assert (= (* x x) 2))"):
            with self.subTest(body=body):
                result = self.solve(body)
                self.assertEqual(result.stdout.strip(), "unknown", result.stderr)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_higher_degree_falls_back_without_crash(self):
        result = self.solve("(declare-const x Int)(assert (= (* x x x x x) 2))")
        self.assertEqual(result.stdout.strip(), "unknown", result.stderr)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreater(json.loads(result.stderr.strip().splitlines()[-1])["higher_degree_fallbacks"], 0)

    def test_parse_only_checks_big_numbers_and_nonlinearity(self):
        huge = (1 << 512) + 91
        result = self.solve(f"(declare-const x Int)(assert (= (* x x) {huge}))", "--parse-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        stats = json.loads(result.stdout)
        self.assertEqual(stats["status"], "parsed")
        self.assertEqual(stats["integer_variables"], 1)
        self.assertEqual(stats["nonlinear_multiplications"], 1)
        self.assertGreaterEqual(stats["max_numeral_bits"], 513)

    def test_unsupported_sort_and_operators_never_claim_sat(self):
        formulas = [
            "(declare-const x Real)(assert (= x 1.5))",
            "(declare-const x Int)(assert (= (div x 2) 3))",
            "(declare-fun f (Int) Int)(assert (= (f 1) 2))",
        ]
        for body in formulas:
            with self.subTest(body=body):
                parsed = self.run_core(body, "--parse-only")
                self.assertNotEqual(parsed.returncode, 0)
                self.assertEqual(json.loads(parsed.stdout)["status"], "unsupported")
                solved = self.run_core(body, "--seconds", "0.01")
                self.assertEqual(solved.stdout.strip(), "unknown")
                self.assertNotIn("sat\n", solved.stdout)

    def test_sat_models_satisfy_independent_integer_constraints(self):
        cases = [
            ("(assert (= (+ (* 3 x x) (* (- 5) x)) 2))", lambda x: 3*x*x-5*x == 2),
            ("(assert (not (<= (* x x) 81)))(assert (< x 0))", lambda x: x*x > 81 and x < 0),
            ("(assert (= (* (- 2) x x) (- 200)))", lambda x: -2*x*x == -200),
            ("(assert (< (* x x) 9))(assert (> x 1))", lambda x: x*x < 9 and x > 1),
        ]
        with tempfile.TemporaryDirectory() as directory:
            for index, (constraints, predicate) in enumerate(cases):
                with self.subTest(index=index):
                    model = Path(directory) / f"model-{index}.json"
                    result = self.solve("(declare-const x Int)" + constraints, "--model-output", str(model))
                    self.assertEqual(result.stdout.strip(), "sat", result.stderr)
                    value = int(json.loads(model.read_text())["x"])
                    self.assertTrue(predicate(value), value)

    def test_preprocessed_variables_are_reconstructed_before_validation(self):
        huge = (1 << 160) + 29
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / "model.json"
            body = f"(declare-const x Int)(declare-const y Int)(assert (= x (+ y 7)))(assert (= (* y y) {huge*huge}))"
            result = self.solve(body, "--model-output", str(model))
            self.assertEqual(result.stdout.strip(), "sat", result.stderr)
            values = json.loads(model.read_text())
            self.assertEqual(int(values["x"]), int(values["y"])+7)
            self.assertEqual(int(values["y"])**2, huge**2)

    def test_zero_budget_shared_ast_returns_promptly(self):
        expression = "(= t32 0)"
        for index in range(32, -1, -1):
            value = "x" if index == 0 else f"(+ t{index-1} t{index-1})"
            expression = f"(let ((t{index} {value})) {expression})"
        formula = f"(declare-const x Int)(assert {expression})"
        for seconds, expected in (("0", "unknown"), ("0.1", "sat")):
            with self.subTest(seconds=seconds):
                result = subprocess.run([str(CORE), "--seconds", seconds], input=formula,
                                        text=True, capture_output=True, timeout=3)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), expected, result.stderr)
                self.assertEqual(json.loads(result.stderr.strip().splitlines()[-1])["validated"], expected == "sat")

    def test_file_input_matches_stdin(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "case.smt2"
            source.write_text("(declare-const x Int)(assert (= x 19))")
            result = subprocess.run([str(CORE), str(source), "--seconds", "0.05"], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.stdout.strip(), "sat", result.stderr)


if __name__ == "__main__":
    unittest.main()
