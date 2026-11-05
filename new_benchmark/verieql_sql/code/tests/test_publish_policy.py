import sys
import tempfile
import unittest
from pathlib import Path
CODE=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(CODE/"vendor"),str(CODE)]
from publish import publish

FORMULA="""(set-logic QF_UFNIA)
(declare-fun f (Int) Int)
(declare-const x Int)
(declare-const y Int)
(assert (= (f x) (* x y)))
(check-sat)
(exit)
"""
class PublishPolicy(unittest.TestCase):
    def run_policy(self,record):
        with tempfile.TemporaryDirectory(prefix="ufnia-sql-policy-",dir="/tmp") as td:
            root=Path(td);candidate=root/"candidate.smt2";candidate.write_text(FORMULA)
            target=root/"output"/"instance.smt2"
            result=publish(candidate,target,{"task_id":"policy_fixture","classification":"crafted_demo"},
                dict(premise_result="sat",row_count_per_table=2,**record),"test","test")
            return result,target.read_text() if target.exists() else None
    def test_actual_unsat_is_not_published(self):
        result,text=self.run_policy({"original_result":"unsat","transformed_result":"unsat"})
        self.assertEqual(result.get("result"),"excluded")
        self.assertEqual(result.get("reason"),"actual_unsat")
        self.assertIsNone(text)
    def test_timeout_unknown_is_not_relabelled_unsat(self):
        with self.assertRaisesRegex(ValueError,"matching definite solver results"):
            self.run_policy({"original_result":"unknown","transformed_result":"unknown",
                "original_reason_unknown":"timeout","transformed_reason_unknown":"timeout"})
    def test_disagreement_is_not_presented_as_unsat(self):
        with self.assertRaises(ValueError):
            self.run_policy({"original_result":"sat","transformed_result":"unsat"})

if __name__=="__main__":unittest.main()
