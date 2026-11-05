import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
_LOCAL_CODE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(_LOCAL_CODE/"vendor"))
import z3

CODE=Path(__file__).resolve().parents[1]
ENV=dict(os.environ,PYTHONHASHSEED="0",PYTHONDONTWRITEBYTECODE="1",OPENBLAS_NUM_THREADS="1",OMP_NUM_THREADS="1")

class RealGeneration(unittest.TestCase):
    def invoke(self,taskfile,out,*extra):
        return subprocess.run([sys.executable,"-B",str(CODE/"generate.py"),
            "--tasks",str(taskfile),"--bounds","2","--output",str(out),*extra],
            capture_output=True,text=True,env=ENV,timeout=90)
    def test_known_unsat_task_is_skipped_before_native_generation(self):
        with tempfile.TemporaryDirectory(prefix="ufnia-sql-test-",dir="/tmp") as td:
            root=Path(td)
            source=root/"inputs.json"
            source.write_text(json.dumps([{"task_id":"calcite_149","schema":{},"sql":["INVALID","INVALID"]}]))
            result=self.invoke(source,root/"instance")
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertEqual(list((root/"instance").glob("*")),[])
            report=json.loads(result.stdout.strip())
            self.assertEqual(report.get("reason"),"known_unsat")
            self.assertEqual(report.get("result"),"excluded")
    def test_low_level_public_encoder_remains_available(self):
        sys.path[:0]=[str(CODE),str(CODE/"vendor")]
        from generate import _generate_snapshot
        from common.smt_tools import audit_formula,extract_queries
        source=CODE/"campaign-inputs.json"
        task=next(t for t in json.loads(source.read_text()) if t["task_id"]=="calcite_149")
        with tempfile.TemporaryDirectory(prefix="ufnia-sql-model-test-",dir="/tmp") as td:
            work=Path(td)
            record=_generate_snapshot(task,2,work,source)
            self.assertEqual(record["original_result"],"unsat")
            self.assertEqual(record["transformed_result"],"unsat")
            self.assertEqual(record["premise_result"],"sat")
            query=extract_queries((work/"candidates/verieql_calcite_149_rows2.smt2").read_text())[0]["text"]
            self.assertTrue(audit_formula(query)["research_eligible"])
    def test_orders_roundtrip_and_existing_file_is_preserved(self):
        with tempfile.TemporaryDirectory(prefix="ufnia-sql-test-",dir="/tmp") as td:
            out=Path(td)/"instance"
            first=self.invoke(CODE/"inputs.json",out,"--task-id","orders_amount_overcharge")
            self.assertEqual(first.returncode,0,first.stdout+first.stderr)
            files=list(out.glob("*.smt2"))
            self.assertEqual(len(files),1,"Publish one final SQL counterexample query directly")
            original=files[0].read_bytes()
            self.assertIn("(set-info :status sat)",original.decode())
            second=self.invoke(CODE/"inputs.json",out,"--task-id","orders_amount_overcharge")
            self.assertEqual(second.returncode,0,second.stdout+second.stderr)
            self.assertEqual(files[0].read_bytes(),original)
            self.assertEqual({p.suffix for p in out.iterdir()},{".smt2"})
    def test_different_existing_formula_is_never_overwritten(self):
        with tempfile.TemporaryDirectory(prefix="ufnia-sql-test-",dir="/tmp") as td:
            out=Path(td)/"instance";out.mkdir()
            path=out/"verieql_orders_amount_overcharge_rows2.smt2"
            data=b"(set-logic QF_UFNIA)\n(assert false)\n(check-sat)\n(exit)\n";path.write_bytes(data)
            result=self.invoke(CODE/"inputs.json",out,"--task-id","orders_amount_overcharge")
            self.assertNotEqual(result.returncode,0)
            self.assertEqual(path.read_bytes(),data)
    def test_vacuous_premise_produces_no_output_artifact(self):
        with tempfile.TemporaryDirectory(prefix="ufnia-sql-test-",dir="/tmp") as td:
            root=Path(td)
            task={"task_id":"vacuous","schema":{"ORDERS":{"Q":"INT","P":"INT"}},
                "constraints":[{"gt":[{"value":"ORDERS__Q"},2147483647]}],
                "sql":["SELECT Q*P FROM ORDERS","SELECT P*Q FROM ORDERS"]}
            source=root/"inputs.json";source.write_text(json.dumps([task]))
            result=self.invoke(source,root/"instance")
            self.assertEqual(list((root/"instance").glob("*")),[])
            rows=[json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
            self.assertEqual(rows[0].get("result"),"excluded")
    def test_bad_constraint_does_not_stop_later_valid_task(self):
        with tempfile.TemporaryDirectory(prefix="ufnia-sql-test-",dir="/tmp") as td:
            root=Path(td)
            good={"task_id":"after_bad","schema":{"ORDERS":{"Q":"INT","P":"INT"}},
                "sql":["SELECT Q*P FROM ORDERS","SELECT Q*(P+1) FROM ORDERS"]}
            bad=dict(good,task_id="bad_constraint",constraints=[{"consec":{"value":"ORDERS__Q"}}])
            source=root/"inputs.json";source.write_text(json.dumps([bad,good]))
            result=self.invoke(source,root/"instance")
            self.assertNotEqual(result.returncode,0)
            self.assertEqual(len(list((root/"instance").glob("*.smt2"))),1)
            self.assertEqual({p.suffix for p in (root/"instance").iterdir()},{".smt2"})

if __name__=="__main__":unittest.main()
