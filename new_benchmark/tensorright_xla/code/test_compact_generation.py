"""Portable layout regressions: parse/simplify only, no satisfiability runs."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import z3

CODE = Path(__file__).resolve().parent
GEN = CODE / "generate.py"
RETAINED_IDS = {
    "pad_restore_rank1_unit_stride", "pad_restore_rank2_unit_stride",
    "reverse_slice_rank1_naive_mirror", "reverse_slice_rank2_naive_mirror",
    "concat_right_rank1_missing_offset", "concat_right_rank2_missing_offset",
    "nested_slice_rank1_dropped_inner_stride", "nested_slice_rank2_dropped_inner_stride",
}


def load_generator():
    spec = importlib.util.spec_from_file_location("tensor_compact_generator", GEN)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def assertion_fingerprint(text):
    assertions = list(z3.parse_smt2_string(text))
    encoded = "\n".join(term.sexpr() for term in assertions).encode()
    return hashlib.sha256(encoded).hexdigest()


class CompactGenerationTests(unittest.TestCase):
    def test_default_entrypoint_produces_only_eight_non_excluded_instances(self):
        originals = CODE.parent / "instance"
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in originals.glob("*.smt2") if p.stem in RETAINED_IDS}
        self.assertEqual(len(before), 8)
        with tempfile.TemporaryDirectory(prefix="tensorright-compact-test-", dir="/tmp") as temporary:
            output = Path(temporary)
            result = subprocess.run(
                [sys.executable, str(GEN), "--output-root", str(output)],
                capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stderr)
            family = output / "tensorright_xla"
            self.assertEqual({p.name for p in family.iterdir()}, {"instance"})
            generated = sorted((family / "instance").glob("*.smt2"))
            self.assertEqual(len(generated), 8)
            self.assertEqual({p.name for p in generated}, set(before))
            for path in generated:
                self.assertEqual(assertion_fingerprint(path.read_text()),
                                 assertion_fingerprint((originals / path.name).read_text()), path.name)
        self.assertEqual(before, {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in originals.glob("*.smt2") if p.stem in RETAINED_IDS})

    def test_checker_source_pin_and_license_are_local_to_code(self):
        for name in ("common/smt_tools.py", "source-pin.json", "known_unsat.json", "requirements.txt",
                     "LICENSE.TensorRight", "README.md"):
            self.assertTrue((CODE / name).is_file(), f"missing portable dependency {name}")

    def test_all_twenty_eight_configurations_apply_only_recorded_unsat_and_uf_exclusions(self):
        generator = load_generator()
        self.assertTrue(callable(getattr(generator, "generate_direct", None)),
                        "direct audited generation is missing")
        with tempfile.TemporaryDirectory(prefix="tensorright-qualify-test-", dir="/tmp") as temporary:
            summary = generator.generate_direct(Path(temporary), generator.cases("campaign"), [])
            self.assertEqual(summary["selected"], 28)
            self.assertEqual(len(summary["written"]), 8)
            self.assertEqual({Path(name).stem for name in summary["written"]}, RETAINED_IDS)
            unsat = [item for item in summary["excluded"] if item.get("category") == "known_unsat"]
            uf = [item for item in summary["excluded"] if item.get("category") != "known_unsat"]
            self.assertEqual(len(unsat), 18)
            self.assertEqual({item["instance_id"] for item in uf},
                             {"slice_broadcast_rank2", "slice_broadcast_rank3"})
            self.assertTrue(all("positive-arity UF" in " ".join(item["reasons"]) for item in uf))
            for filename in summary["written"]:
                text = (Path(temporary) / "tensorright_xla/instance" / filename).read_text()
                self.assertIn("(set-info :status unknown)", text)

    def test_existing_identical_formula_preserves_bytes_and_different_formula_is_rejected(self):
        generator = load_generator()
        self.assertTrue(callable(getattr(generator, "generate_direct", None)),
                        "direct audited generation is missing")
        selected = [("pad_restore", 1, "unit_stride")]
        with tempfile.TemporaryDirectory(prefix="tensorright-existing-test-", dir="/tmp") as temporary:
            output = Path(temporary)
            generator.generate_direct(output, selected, [])
            target = output / "tensorright_xla/instance/pad_restore_rank1_unit_stride.smt2"
            original = target.read_bytes()
            summary = generator.generate_direct(output, selected, [])
            self.assertEqual(summary["written"], [])
            self.assertEqual(summary["existing"], [target.name])
            self.assertEqual(target.read_bytes(), original)
            target.write_text("(set-logic QF_UFNIA)\n(assert false)\n(check-sat)\n(exit)\n")
            different = target.read_bytes()
            with self.assertRaises(ValueError):
                generator.generate_direct(output, selected, [])
            self.assertEqual(target.read_bytes(), different)



    def test_explicit_known_unsat_case_and_old_pilot_cannot_republish(self):
        for arguments, expected in [
            (["--case", "pad_restore_rank1"], 1),
            (["--mode", "pilot"], 5),
        ]:
            with self.subTest(arguments=arguments), tempfile.TemporaryDirectory(
                    prefix="tensorright-unsat-policy-", dir="/tmp") as temporary:
                result = subprocess.run(
                    [sys.executable, str(GEN), *arguments, "--output-root", temporary],
                    capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                summary = json.loads(result.stdout)
                self.assertEqual(summary["written"], [])
                self.assertEqual(len(summary["excluded"]), expected)
                self.assertTrue(all(item["category"] == "known_unsat" for item in summary["excluded"]))
                self.assertEqual(list(Path(temporary).rglob("*.smt2")), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
