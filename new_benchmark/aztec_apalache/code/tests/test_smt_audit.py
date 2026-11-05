import sys,unittest
from pathlib import Path
import z3
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
try:
    import smt_audit
except ImportError:
    smt_audit=None

BASE="(declare-fun f (Int) Int)\n(declare-const x Int)\n(declare-const y Int)\n(assert (= (f (* x y)) (+ x y)))\n(check-sat)\n"
class StrictAuditTests(unittest.TestCase):
    def audit(self,text):
        self.assertIsNotNone(smt_audit,"strict standalone audit missing")
        return smt_audit.audit_snapshot(text)
    def test_genuine_uf_and_nonlinear_integer_formula_passes(self):
        r=self.audit(BASE)
        self.assertEqual(r["result"],"pass")
        self.assertTrue(r["research_eligible"])
    def test_unused_array_signature_is_rejected(self):
        r=self.audit("(declare-const leaked (Array Int Int))\n"+BASE)
        self.assertEqual(r["result"],"fail")
        self.assertFalse(r["research_eligible"])
        self.assertTrue(r["declaration_errors"])
    def test_unused_real_argument_signature_is_rejected(self):
        r=self.audit("(declare-fun leaked (Real) Int)\n"+BASE)
        self.assertEqual(r["result"],"fail")
    def test_actual_quantifier_is_rejected(self):
        text=BASE.replace("(check-sat)","(assert (forall ((z Int)) (= (f z) z)))\n(check-sat)")
        self.assertEqual(self.audit(text)["result"],"fail")
    def test_unused_uf_does_not_establish_research_eligibility(self):
        text=BASE.replace("(assert (= (f (* x y)) (+ x y)))","(assert (= (* x y) (+ x y)))")
        r=self.audit(text)
        self.assertEqual(r["result"],"pass")
        self.assertFalse(r["research_eligible"])
    def test_nullary_free_sort_and_real_uf_use_are_allowed(self):
        text="(declare-sort Address 0)\n(declare-const a Address)\n"+BASE.replace("f (Int)","f (Address Int)").replace("(f (* x y))","(f a (* x y))")
        self.assertTrue(self.audit(text)["research_eligible"])
    def test_serialization_preserves_all_assertions(self):
        self.assertIsNotNone(smt_audit)
        x=z3.Int("serialize_x")
        original=[x>0,x<3]
        smt_audit.assert_serialization_matches("(declare-const serialize_x Int)(assert (> serialize_x 0))(assert (< serialize_x 3))(check-sat)",original)
        with self.assertRaises(ValueError):
            smt_audit.assert_serialization_matches("(declare-const serialize_x Int)(assert (> serialize_x 0))(check-sat)",original)

if __name__=="__main__":unittest.main()
