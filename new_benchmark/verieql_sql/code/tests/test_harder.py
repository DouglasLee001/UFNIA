import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
CODE=Path(__file__).resolve().parents[1]
ENV=dict(os.environ,PYTHONHASHSEED="0",PYTHONDONTWRITEBYTECODE="1",OPENBLAS_NUM_THREADS="1",OMP_NUM_THREADS="1")
class HarderProbe(unittest.TestCase):
    def test_default_selection_is_the_four_reviewed_configurations(self):
        result=subprocess.run([sys.executable,"-B",str(CODE/"generate_harder.py"),"--list"],
            env=ENV,capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        data=json.loads(result.stdout)
        self.assertEqual(data["defaults"],[
            {"case_id":"overcharge","bound":12},{"case_id":"overcharge","bound":16},
            {"case_id":"loyalty_wrong_join","bound":3},{"case_id":"line_discount_pushdown","bound":3}])
    def test_new_probe_refuses_the_delivery_instance_directory(self):
        result=subprocess.run([sys.executable,"-B",str(CODE/"generate_harder.py"),
            "--probe","--case","overcharge","--bound","2","--output",str(CODE.parent/"instance")],
            env=ENV,capture_output=True,text=True,timeout=15)
        self.assertNotEqual(result.returncode,0)
        self.assertIn("restricted to /tmp",result.stderr)
    def test_stale_output_is_not_reported_as_a_new_success(self):
        with tempfile.TemporaryDirectory(prefix="ufnia-harder-test-",dir="/tmp") as td:
            path=Path(td)/"verieql_orders_amount_overcharge_rows2.smt2"
            path.write_text("(set-logic QF_UFNIA)\n(assert false)\n(check-sat)\n(exit)\n")
            result=subprocess.run([sys.executable,"-B",str(CODE/"generate_harder.py"),
                "--probe","--case","overcharge","--bound","2","--output",td,"--proof-dir",str(Path(td)/"proof")],
                env=ENV,capture_output=True,text=True,timeout=45)
            self.assertNotEqual(result.returncode,0)
            report=json.loads(next(Path(td).rglob("*.probe.json")).read_text())
            self.assertNotIn("candidate",report)
    def test_existing_sat_probe_records_actual_checks_and_replay(self):
        with tempfile.TemporaryDirectory(prefix="ufnia-harder-test-",dir="/tmp") as td:
            result=subprocess.run([sys.executable,"-B",str(CODE/"generate_harder.py"),
                "--probe","--case","overcharge","--bound","2","--output",td,"--proof-dir",str(Path(td)/"proof")],
                env=ENV,capture_output=True,text=True,timeout=45)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            reports=list(Path(td).rglob("*.probe.json"))
            self.assertEqual(len(reports),1)
            report=json.loads(reports[0].read_text())
            self.assertEqual(report["original_result"],"sat")
            self.assertEqual(report["transformed_result"],"sat")
            self.assertEqual(report["original_sql_replay"]["status"],"pass")
            self.assertEqual(report["transformed_sql_replay"]["status"],"pass")
            self.assertEqual(report["theory"]["result"],"pass")
            self.assertTrue(report["theory"]["research_eligible"])
            validation=report.get("model_validation",{})
            self.assertEqual(validation.get("result"),"pass")
            self.assertEqual(validation["original_counterexample"]["result"],"pass")
            self.assertEqual(validation["transformed_counterexample"]["result"],"pass")
            self.assertGreater(validation["original_counterexample"]["assertions_checked"],0)
            self.assertGreater(validation["transformed_counterexample"]["assertions_checked"],0)
            self.assertGreater(report["bytes"],0)
if __name__=="__main__":unittest.main()
