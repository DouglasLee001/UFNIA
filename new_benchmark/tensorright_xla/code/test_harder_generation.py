"""Certificate-first harder generation; these tests never call solver.check."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import z3

CODE = Path(__file__).resolve().parent
SCRIPT = CODE / "generate_harder.py"
FROZEN = {
    "coupled_mirror_rank2_depth2_lanes2": (
        19, "89c4035826aba5398d41ad1c20af901553b8bdadadf802e0ff90140ece191fef"),
    "coupled_mirror_rank3_depth3_lanes2": (
        21, "a7935ee35fa73dab16333de489a7c9b29a7025b507abf8373903fa463e916f81"),
}


def fingerprint(text):
    assertions = list(z3.parse_smt2_string(text))
    return hashlib.sha256("\n".join(a.sexpr() for a in assertions).encode()).hexdigest()


class HarderGenerationTests(unittest.TestCase):
    def tool(self):
        self.assertTrue(SCRIPT.is_file(), "certificate-first harder generator is missing")
        spec = importlib.util.spec_from_file_location("tensor_harder_test", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    def test_default_cases_keep_frozen_formulas_and_verify_every_assertion(self):
        module = self.tool()
        self.assertEqual(tuple(module.DEFAULT_CONFIGS), ((2, 2, 2), (3, 3, 2)))
        with patch.object(z3.Solver, "check", side_effect=AssertionError("solver.check is prohibited")):
            for config in module.DEFAULT_CONFIGS:
                case = module.build_case(*config)
                snapshot = module.snapshot_text(case)
                expected_count, expected_hash = FROZEN[case["name"]]
                self.assertEqual(fingerprint(snapshot), expected_hash)
                proof = module.verify_certificate(case, snapshot)
                self.assertEqual(proof["result"], "pass")
                self.assertEqual(proof["assertions_checked"], expected_count)
                self.assertEqual(proof["theory"]["result"], "pass")
                self.assertTrue(proof["theory"]["research_eligible"])
                self.assertEqual(proof["independent_replay"]["result"], "pass")
                self.assertTrue(proof["independent_replay"]["equal_valid_shapes"])
                self.assertTrue(proof["independent_replay"]["source_data_only"])
                self.assertEqual(proof["solver_check_calls"], 0)

    def test_bad_valuation_is_rejected_before_any_sat_file_is_written(self):
        module = self.tool()
        case = module.build_case(2, 2, 2)
        parameters = module.certificate_parameters(case)
        parameters["extent_0"] = -1
        with self.assertRaises(ValueError):
            module.verify_certificate(case, module.snapshot_text(case), parameters)
        with tempfile.TemporaryDirectory(prefix="tensor-harder-reject-", dir="/tmp") as root:
            with patch.object(module, "certificate_parameters", return_value=parameters):
                with self.assertRaises(ValueError):
                    module.generate_harder(Path(root), selected=[(2, 2, 2)])
            self.assertEqual(list(Path(root).rglob("*.smt2")), [])

    def test_independent_replay_uses_python_operations(self):
        module = self.tool()
        case = module.build_case(2, 2, 2)
        parameters = module.certificate_parameters(case)
        with patch.object(z3, "simplify", side_effect=AssertionError("symbolic replay is prohibited")):
            replay = module.replay_certificate(case, parameters)
        self.assertEqual(replay["result"], "pass")
        self.assertTrue(any(row["lhs"] != row["rhs"] for row in replay["observations"]))

    def test_default_generation_publishes_only_two_sat_snapshots_and_preserves_existing(self):
        module = self.tool()
        with tempfile.TemporaryDirectory(prefix="tensor-harder-publish-", dir="/tmp") as root:
            root = Path(root)
            with patch.object(z3.Solver, "check", side_effect=AssertionError("solver.check is prohibited")):
                result = module.generate_harder(root)
            self.assertEqual(len(result["written"]), 2)
            family = root / "tensorright_xla"
            self.assertEqual({p.name for p in family.iterdir()}, {"instance"})
            before = {}
            for path in sorted((family / "instance").glob("*.smt2")):
                text = path.read_text()
                self.assertIn("(set-info :status sat)", text)
                self.assertEqual(fingerprint(text), FROZEN[path.stem][1])
                before[path.name] = path.read_bytes()
            again = module.generate_harder(root)
            self.assertEqual(again["written"], [])
            self.assertEqual(set(again["existing"]), set(before))
            self.assertTrue(all((family / "instance" / name).read_bytes() == value
                                for name, value in before.items()))
            path = family / "instance" / next(iter(before))
            path.write_text("(set-logic QF_UFNIA)\n(assert false)\n(check-sat)\n(exit)\n")
            invalid = path.read_bytes()
            with self.assertRaises(ValueError):
                module.generate_harder(root)
            self.assertEqual(path.read_bytes(), invalid)

    def test_cli_lists_cases_and_writes_proofs_only_to_explicit_tmp_directory(self):
        self.tool()
        result = subprocess.run([sys.executable, str(SCRIPT), "--list"],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(set(FROZEN).issubset(set(result.stdout.splitlines())))
        with tempfile.TemporaryDirectory(prefix="tensor-harder-cli-", dir="/tmp") as root:
            root = Path(root)
            proofs = root / "proofs"
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--case", next(iter(FROZEN)),
                 "--output-root", str(root / "output"), "--proof-dir", str(proofs)],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(list((root / "output/tensorright_xla/instance").glob("*.smt2"))), 1)
            proof_files = list(proofs.glob("*.certificate.json"))
            self.assertEqual(len(proof_files), 1)
            self.assertEqual(json.loads(proof_files[0].read_text())["result"], "pass")


if __name__ == "__main__":
    unittest.main(verbosity=2)
