"""Source transition and complete valuation checks; never run a SAT solver."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import z3

CODE = Path(__file__).resolve().parents[1]
MODULE = CODE / "slashing_model.py"

class SlashingModelTests(unittest.TestCase):
    def load(self):
        self.assertTrue(MODULE.is_file(), "source-derived slashing model implementation is missing")
        spec = importlib.util.spec_from_file_location("slashing_model_tests_subject", MODULE)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    def case(self, **profile):
        module = self.load()
        return module, module.build_case(dict(kind="quorum", signals=4, qmin=4, **profile))

    def lookup(self, certificate, name, args):
        function = certificate["functions"][name]
        for entry in function["entries"]:
            if entry["args"] == list(args):
                return entry["value"]
        return function["default"]

    def test_full_certificate_checks_every_assertion_and_is_json_portable(self):
        m, case = self.case()
        cert = json.loads(json.dumps(m.certificate_parameters(case)))
        result = m.verify_certificate(case, cert)
        self.assertEqual(result["result"], "pass", result)
        self.assertEqual(result["checked_assertions"], len(case.assertions))
        self.assertEqual(m.replay_certificate(case, cert)["result"], "pass")
        text = m.snapshot_text(case)
        self.assertEqual(text.count("(check-sat)"), 1)
        self.assertIn("(declare-fun signal_count (Int Int Int Int) Int)", text)
        self.assertIn("(div slot_0 R)", text)
        self.assertEqual(len(z3.parse_smt2_string(text)), len(case.assertions))

    def test_invalid_majority_configuration_is_rejected(self):
        m, case = self.case()
        cert = m.certificate_parameters(case)
        cert["scalars"]["R"] = 2 * cert["scalars"]["Q"] + 1
        self.assertEqual(m.verify_certificate(case, cert)["result"], "fail")
        self.assertEqual(m.replay_certificate(case, cert)["result"], "fail")

    def test_wrong_storage_valuation_is_rejected_independently_of_replay(self):
        m, case = self.case()
        cert = m.certificate_parameters(case)
        cert["functions"]["signal_count"]["entries"][-1]["value"] += 1
        result = m.verify_certificate(case, cert)
        self.assertEqual(result["result"], "fail", result)
        self.assertTrue(result["failed_assertion_indices"])
        self.assertEqual(m.replay_certificate(case, cert)["result"], "pass")

    def test_symbolic_round_aliases_accumulate_the_same_storage(self):
        m, case = self.case()
        cert = m.certificate_parameters(case)
        self.assertGreaterEqual(len(case.observed_rounds), 5)
        self.assertEqual(self.lookup(cert, "signal_count", (4, m.INSTANCE, 0, 1)), 4)
        self.assertEqual(m.verify_certificate(case, cert)["result"], "pass")

    def test_absent_round_zero_payload_and_present_payload_are_distinguished(self):
        m, case = self.case()
        cert = m.certificate_parameters(case)
        self.assertFalse(self.lookup(cert, "round_present", (0, m.INSTANCE, 0)))
        self.assertEqual(self.lookup(cert, "winner", (0, m.INSTANCE, 0)), m.ZERO)
        self.assertTrue(self.lookup(cert, "round_present", (1, m.INSTANCE, 0)))
        self.assertTrue(self.lookup(cert, "payload_present", (1, m.INSTANCE, 0, 1)))
        self.assertFalse(self.lookup(cert, "payload_present", (1, m.INSTANCE, 0, m.ZERO)))
        self.assertEqual(self.lookup(cert, "signal_count", (1, m.INSTANCE, 0, m.ZERO)), 0)

    def test_winner_tie_retains_incumbent_then_strict_greater_switches(self):
        m = self.load()
        case = m.build_case(dict(kind="quorum", signals=6, qmin=2))
        changes = dict(R=7, Q=4)
        changes.update({f"payload_{i}": p for i, p in enumerate([1, 2, 2, 1, 1, 1])})
        cert = m.certificate_parameters(case, changes)
        self.assertEqual(self.lookup(cert, "winner", (4, m.INSTANCE, 0)), 2)
        self.assertEqual(self.lookup(cert, "winner", (5, m.INSTANCE, 0)), 1)
        self.assertEqual(m.verify_certificate(case, cert)["result"], "pass")

    def test_global_slot_decrease_is_allowed_when_each_round_guard_holds(self):
        m = self.load()
        case = m.build_case(dict(kind="quorum", signals=4, qmin=2))
        changes = dict(R=3, Q=2, slot_0=4, slot_1=5, slot_2=1, slot_3=2)
        cert = m.certificate_parameters(case, changes)
        self.assertEqual(m.replay_certificate(case, cert)["result"], "pass")
        self.assertEqual(m.verify_certificate(case, cert)["result"], "pass")

    def test_zero_slot_is_not_a_successful_first_signal(self):
        m, case = self.case()
        cert = m.certificate_parameters(case)
        cert["scalars"]["slot_0"] = 0
        result = m.replay_certificate(case, cert)
        self.assertEqual(result["result"], "fail")
        self.assertIn("SignalAlreadyCastForSlot", result["reason"])
        self.assertEqual(m.verify_certificate(case, cert)["result"], "fail")

    def test_submit_window_and_callback_guards_are_complete(self):
        m = self.load()
        case = m.build_case(dict(kind="executed", signals=2, qmin=2))
        cert = m.certificate_parameters(case)
        self.assertEqual(m.verify_certificate(case, cert)["result"], "pass")
        for newslot, message in [(3, "RoundTooNew"), (9, "RoundTooOld")]:
            changed = copy.deepcopy(cert)
            changed["scalars"]["slot_2"] = newslot
            result = m.replay_certificate(case, changed)
            self.assertEqual(result["result"], "fail")
            self.assertIn(message, result["reason"])
            self.assertEqual(m.verify_certificate(case, changed)["result"], "fail")
        changed = copy.deepcopy(cert)
        changed["scalars"]["callback_caller_2"] = m.USER1
        self.assertEqual(m.replay_certificate(case, changed)["result"], "fail")
        self.assertEqual(m.verify_certificate(case, changed)["result"], "fail")

    def test_veto_of_other_payload_succeeds_and_winner_veto_blocks_submit(self):
        m = self.load()
        case = m.build_case(dict(kind="executed", signals=2, qmin=2, vetoes=1))
        cert = m.certificate_parameters(case)
        self.assertEqual(m.verify_certificate(case, cert)["result"], "pass")
        changed = copy.deepcopy(cert)
        changed["scalars"]["payload_2"] = 1
        result = m.replay_certificate(case, changed)
        self.assertEqual(result["result"], "fail")
        self.assertIn("PayloadVetoed", result["reason"])
        self.assertEqual(m.verify_certificate(case, changed)["result"], "fail")

    def test_source_pin_and_input_archives_are_unchanged(self):
        before = hashlib.sha256((CODE / "source-manifest.json").read_bytes()).hexdigest()
        archive = CODE / "deps/aztec-6fee2e4.tar.gz"
        with archive.open("rb") as stream:
            archive_before = hashlib.file_digest(stream, "sha256").hexdigest()
        m, case = self.case()
        self.assertEqual(case.metadata["source_commit"], "6fee2e4f6dbbe632cf62b411e344e1a008642cd7")
        self.assertEqual(case.metadata["classification"], "application_derived")
        self.assertIn("spec/empire_base.qnt", case.metadata["source_file_sha256"])
        self.assertIn("spec/slashing_proposer_machine.qnt", case.metadata["source_file_sha256"])
        self.assertEqual(before, hashlib.sha256((CODE / "source-manifest.json").read_bytes()).hexdigest())
        with archive.open("rb") as stream:
            self.assertEqual(archive_before, hashlib.file_digest(stream, "sha256").hexdigest())

    def test_generation_and_validation_never_call_solver_check(self):
        m = self.load()
        with patch.object(z3.Solver, "check", side_effect=AssertionError("a solver was invoked")):
            case = m.build_case(dict(kind="quorum", signals=2, qmin=2))
            text = m.snapshot_text(case)
            cert = m.certificate_parameters(case)
            self.assertEqual(m.verify_certificate(case, cert)["result"], "pass")
            self.assertEqual(m.replay_certificate(case, cert)["result"], "pass")
            self.assertTrue(text)

if __name__ == "__main__":
    unittest.main()
