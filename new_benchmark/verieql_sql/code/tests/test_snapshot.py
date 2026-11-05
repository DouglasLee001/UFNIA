import sys
import unittest
from pathlib import Path
_LOCAL_CODE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(_LOCAL_CODE/"vendor"))
import z3
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    from capture import snapshot_text
except ModuleNotFoundError:
    def snapshot_text(solver,assumptions=()):
        return "(check-sat)"
class CompleteSnapshot(unittest.TestCase):
    def test_negated_property_and_assumption_are_present(self):
        x=z3.Int("x");a=z3.Bool("activation")
        s=z3.Solver();s.add(x>=0,z3.Implies(a,x<0))
        s.push();s.add(x==9);s.pop()
        text=snapshot_text(s,[a])
        replay=z3.Solver();replay.from_string(text)
        self.assertEqual(replay.check(),z3.unsat)
        self.assertEqual(len(replay.assertions()),3)
        self.assertNotIn("(= x 9)",text)
if __name__=="__main__":unittest.main()
