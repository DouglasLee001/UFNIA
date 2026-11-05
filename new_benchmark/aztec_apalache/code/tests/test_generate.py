import sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import z3
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
try:
    import generate
except ImportError:
    generate=None

class PublicationTests(unittest.TestCase):
    def api(self):
        self.assertIsNotNone(generate,"certified generator missing")
        return generate
    def test_only_certified_sat_smt2_is_written_and_proofs_stay_separate(self):
        g=self.api()
        with tempfile.TemporaryDirectory(prefix="aztec-publish-",dir="/tmp") as d:
            p=Path(d);out=p/"instance";proof=p/"proof"
            report=g.generate(out,["sp_quorum_k2"],proof)
            files=list(out.iterdir())
            self.assertEqual([f.suffix for f in files],[".smt2"])
            self.assertIn("(set-info :status sat)",files[0].read_text())
            self.assertEqual(report["certified_sat"],["sp_quorum_k2"])
            self.assertTrue((proof/"sp_quorum_k2.json").exists())
    def test_bad_complete_valuation_cannot_publish(self):
        g=self.api()
        with tempfile.TemporaryDirectory(prefix="aztec-reject-",dir="/tmp") as d:
            with patch.object(g.model,"verify_certificate",return_value={"result":"fail"}):
                with self.assertRaises(ValueError):
                    g.generate(Path(d)/"instance",["sp_quorum_k2"],Path(d)/"proof")
            self.assertFalse(list(Path(d).rglob("*.smt2")))
    def test_no_solver_check_is_called_during_publication(self):
        g=self.api()
        with tempfile.TemporaryDirectory(prefix="aztec-no-solver-",dir="/tmp") as d:
            with patch.object(z3.Solver,"check",side_effect=AssertionError("solving during generation")):
                g.generate(Path(d)/"instance",["sp_quorum_k2"],Path(d)/"proof")
    def test_identical_file_is_preserved_and_different_file_is_rejected(self):
        g=self.api()
        with tempfile.TemporaryDirectory(prefix="aztec-existing-",dir="/tmp") as d:
            p=Path(d);out=p/"instance";proof=p/"proof"
            g.generate(out,["sp_quorum_k2"],proof)
            f=next(out.glob("*.smt2"));before=f.stat().st_mtime_ns
            g.generate(out,["sp_quorum_k2"],proof)
            self.assertEqual(before,f.stat().st_mtime_ns)
            f.write_text("(set-logic QF_UFNIA)\n(assert false)\n(check-sat)\n")
            with self.assertRaises(ValueError):g.generate(out,["sp_quorum_k2"],proof)
    def test_non_temporary_alternate_output_or_proof_is_rejected(self):
        g=self.api()
        with self.assertRaises(ValueError):
            g.generate(Path("/home/lbh/smt/ufnia/result/invalid-generator-output"),["sp_quorum_k2"])
        with tempfile.TemporaryDirectory(prefix="aztec-paths-",dir="/tmp") as d:
            with self.assertRaises(ValueError):
                g.generate(Path(d)/"instance",["sp_quorum_k2"],Path("/home/lbh/smt/ufnia"))
    def test_racing_existing_file_is_never_overwritten(self):
        g=self.api()
        with tempfile.TemporaryDirectory(prefix="aztec-race-",dir="/tmp") as d:
            p=Path(d);out=p/"instance";proof=p/"proof"
            g.generate(out,["sp_quorum_k2"],proof)
            f=next(out.glob("*.smt2"));content="concurrent different file\n";f.write_text(content)
            exists=Path.exists
            with patch.object(Path,"exists",lambda path:False if path==f else exists(path)):
                with self.assertRaises(ValueError):g.generate(out,["sp_quorum_k2"],proof)
            self.assertEqual(f.read_text(),content)
    def test_unknown_profile_is_rejected(self):
        g=self.api()
        with tempfile.TemporaryDirectory(prefix="aztec-unknown-",dir="/tmp") as d:
            with self.assertRaises(ValueError):g.generate(Path(d),["../bad"])
    def test_final_snapshot_keeps_every_original_assertion(self):
        g=self.api()
        with tempfile.TemporaryDirectory(prefix="aztec-assertions-",dir="/tmp") as d:
            p=Path(d);g.generate(p/"instance",["sp_quorum_k2"],p/"proof")
            case=g.model.build_case(g.PROFILES["sp_quorum_k2"])
            g.assert_serialization_matches(next((p/"instance").glob("*.smt2")).read_text(),case.assertions)

if __name__=="__main__":unittest.main()
