import sys
import unittest
from pathlib import Path
_LOCAL_CODE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(_LOCAL_CODE/"vendor"))
import z3
CODE=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(CODE),str(CODE/"upstream/src")]
try:
    from compat import prepare_z3_api
except ModuleNotFoundError:
    def prepare_z3_api():pass
class Z3Compatibility(unittest.TestCase):
    def test_official_context_wrappers_preserve_empty_and_nonempty_connectives(self):
        import constants as c
        prepare_z3_api()
        try:
            formula=c.And(c.Or(c.Z3_TRUE,c.Z3_FALSE),c.Not(c.Or()),c.And(),c.Sum()==c.Z3_0,c.Sum(c.Z3_1,c.Z3_1)==c.IntVal(2))
        except TypeError as error:
            self.fail("Official context keyword contract is unsupported: "+str(error))
        s=z3.Solver(ctx=c.Z3_CONTEXT);s.add(c.Not(formula))
        self.assertEqual(s.check(),z3.unsat)
if __name__=="__main__":unittest.main()
