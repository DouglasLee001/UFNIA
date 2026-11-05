import sys
import unittest
from pathlib import Path
_LOCAL_CODE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(_LOCAL_CODE/"vendor"))
import z3
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    from transform import abstract_labels
except ModuleNotFoundError:
    def abstract_labels(xs):
        return xs, {}

class LabelTranslation(unittest.TestCase):
    def test_aliasing_relations_survive(self):
        t = z3.DeclareSort("Row")
        a,b=z3.Strings("a b")
        row=z3.Const("r",t)
        f=z3.Function("null",t,z3.StringSort(),z3.BoolSort())
        xs=[a==b, f(row,a), z3.Not(f(row,b))]
        ys,_=abstract_labels(xs)
        self.assertFalse(any(x.sort().kind()==z3.Z3_SEQ_SORT for y in ys for x in walk(y)), "String labels must be abstracted")
        s=z3.Solver();s.add(ys)
        self.assertEqual(s.check(),z3.unsat, "equal labels must still alias")
    def test_real_string_operation_is_refused(self):
        a=z3.String("a")
        with self.assertRaisesRegex(ValueError,"String"):
            abstract_labels([z3.Length(a)>0])
    def test_reserved_free_sort_collision_is_refused(self):
        old=z3.DeclareSort("__VeriEQL_Label")
        x=z3.Const("existing",old)
        pred=z3.Function("existing_pred",old,z3.BoolSort())
        a=z3.String("a")
        with self.assertRaisesRegex(ValueError,"collision"):
            abstract_labels([pred(x),a==a])
    def test_literals_are_refused(self):
        a=z3.String("a")
        with self.assertRaisesRegex(ValueError,"String"):
            abstract_labels([a==z3.StringVal("quantity")])

def walk(e):
    yield e
    for c in e.children():
        yield from walk(c)

if __name__=="__main__":
    unittest.main()
