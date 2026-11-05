import sys
import unittest
from pathlib import Path
_LOCAL_CODE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(_LOCAL_CODE/"vendor"))
import z3
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
try:
    from replay import replay
except ModuleNotFoundError:
    def replay(task,model):return {"status":"not_run"}
class SQLReplay(unittest.TestCase):
    def test_replays_integer_null_and_duplicate_bag_rows(self):
        row=z3.DeclareSort("TupleSort")
        label=z3.DeclareSort("__VeriEQL_Label")
        r1,r2=z3.Consts("t1 t2",row)
        q=z3.Function("ORDERS__Q",row,z3.IntSort())
        p=z3.Function("ORDERS__P",row,z3.IntSort())
        n=z3.Function("NULL",row,label,z3.BoolSort())
        ql,pl=z3.Consts("q_label p_label",label)
        s=z3.Solver();s.add(r1!=r2)
        for r in (r1,r2):
            s.add(q(r)==2,p(r)==3,z3.Not(n(r,ql)),z3.Not(n(r,pl)))
        self.assertEqual(s.check(),z3.sat)
        task={"replay_spec":{"schema":{"ORDERS":{"Q":"INT","P":"INT"}},
            "sql":["SELECT Q*P FROM ORDERS","SELECT Q*(P+1) FROM ORDERS"],
            "tables":{"ORDERS":[{"tuple":str(r),"columns":[
                {"column":"Q","value_function":"ORDERS__Q","null_function":"NULL","label":"q_label"},
                {"column":"P","value_function":"ORDERS__P","null_function":"NULL","label":"p_label"}]} for r in (r1,r2)]}}}
        out=replay(task,s.model())
        self.assertEqual(out["status"],"pass")
        self.assertEqual(out["results"],[[[6],[6]],[[8],[8]]])
if __name__=="__main__":unittest.main()
