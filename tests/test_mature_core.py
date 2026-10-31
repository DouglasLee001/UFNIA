import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
BIN = Path(os.environ.get('MATURE_BINARY',str(ROOT / 'build/ufnia_ls/ufnia_mature')))

class MatureCoreTests(unittest.TestCase):
    def run_native(self, atoms, clauses, *options):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)/'input.native'
            model = Path(tmp)/'model.json'
            source.write_text('\n'.join([str(len(atoms)+1), *[f'{i} {a}' for i,a in enumerate(atoms,1)], '0', str(len(clauses)), *['( '+' '.join(map(str,c))+' )' for c in clauses], '0'])+'\n')
            p = subprocess.run([str(BIN),str(source),'--seconds','0.2','--seed','1','--model-output',str(model),*options],capture_output=True,text=True)
            return p, json.loads(model.read_text()) if model.exists() else None

    def test_parse_actual_mature_path_and_huge_numerals(self):
        p,_ = self.run_native(['( = x '+str(2**300)+' )'], [[1]], '--parse-only')
        self.assertEqual(p.returncode,0,p.stderr)
        data=json.loads(p.stdout)
        self.assertEqual(data['status'],'parsed')
        self.assertEqual(data['backend'],'mature_mpz')
        self.assertGreaterEqual(data['max_numeral_bits'],301)

    def test_exact_huge_linear_sat(self):
        value=2**300+2147483647
        p,model=self.run_native(['( = x '+str(value)+' )'],[[1]])
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual(p.stdout.strip(),'sat',p.stderr)
        self.assertEqual(model['x'],str(value))
        self.assertTrue(json.loads(p.stderr.splitlines()[-1])['validated'])

    def test_exact_huge_quadratic_and_bool_indices(self):
        value=2**140+7
        p,m=self.run_native(['b','( = ( * x x ) '+str(value*value)+' )'], [[1],[2]])
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual(p.stdout.strip(),'sat',p.stderr)
        self.assertTrue(m['b'])
        self.assertEqual(int(m['x'])**2,value*value)

    def test_false_never_reported_unsat_or_sat(self):
        p,m=self.run_native(['( = ( * x x ) 2 )'],[[1]])
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual(p.stdout.strip(),'unknown')
        self.assertIsNone(m)

    def test_huge_critical_move_not_unit_preset(self):
        value=2**200+3
        p,m=self.run_native(['b','( = x '+str(value)+' )'],[[2,1],[-1]])
        self.assertEqual(p.stdout.strip(),'sat',p.stderr)
        self.assertEqual(m['x'],str(value))
        self.assertFalse(m['b'])
        self.assertGreater(json.loads(p.stderr.splitlines()[-1])['int_phase_steps'],0)

    def test_negative_strict_and_negated_literal(self):
        value=-(2**180)-3
        p,m=self.run_native(['( < x '+str(value+1)+' )','( <= x '+str(value-1)+' )'],[[1],[-2]])
        self.assertEqual(p.stdout.strip(),'sat',p.stderr)
        self.assertEqual(m['x'],str(value))

    def test_int32_sentinel_is_a_valid_value(self):
        p,m=self.run_native(['( = x 2147483647 )','( = x 2147483648 )'],[[1,2]])
        self.assertEqual(p.stdout.strip(),'sat',p.stderr)
        self.assertIn(m['x'],['2147483647','2147483648'])

    def test_constant_false_and_empty_clause(self):
        for atoms,clauses in [(['( = 0 1 )'],[[1]]),([], [[]])]:
            p,m=self.run_native(atoms,clauses)
            self.assertEqual(p.returncode,0,p.stderr)
            self.assertEqual(p.stdout.strip(),'unknown')
            self.assertIsNone(m)

    def test_zero_product_activation_and_high_degree_exactness(self):
        p,m=self.run_native(['( = ( * x y ) 6 )','( = ( * x x x ) 8 )'],[[1],[2]])
        self.assertEqual(p.stdout.strip(),'sat',p.stderr)
        self.assertEqual(int(m['x'])**3,8)
        self.assertEqual(int(m['x'])*int(m['y']),6)

    def test_duplicate_boolean_atoms_share_value(self):
        p,m=self.run_native(['b','b','( = x 7 )'],[[1],[2],[3]])
        self.assertEqual(p.stdout.strip(),'sat',p.stderr)
        self.assertTrue(m['b'])
        self.assertEqual(m['x'],'7')

    def test_constant_true_and_no_variables(self):
        for atoms,clauses in [([],[]),(['( = 1 1 )'],[[1]])]:
            p,m=self.run_native(atoms,clauses)
            self.assertEqual(p.stdout.strip(),'sat',p.stderr)
            self.assertEqual(m,{})

    def test_large_multilinear_and_higher_power_evaluation(self):
        value=2**100+9
        p,m=self.run_native(['( = x '+str(value)+' )','( = y -3 )','( = z 11 )',
            '( = ( * x x x y z ) '+str(value**3*-33)+' )'],[[1],[2],[3],[4]])
        self.assertEqual(p.stdout.strip(),'sat',p.stderr)
        self.assertEqual(m['x'],str(value))

    def test_mature_bool_int_phases_repair_guarded_arithmetic(self):
        atoms=['b','c'];clauses=[[1,2]]
        for guard in [1,2]:
            for j in range(3):
                atoms.append('( <= ( * -1 x'+str(guard)+'_'+str(j)+' ) -100 )')
                atom=len(atoms);clauses.extend([[-guard,atom],[guard,-atom]])
        p,m=self.run_native(atoms,clauses)
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual(p.stdout.strip(),'sat',p.stderr)
        stats=json.loads(p.stderr.splitlines()[-1])
        self.assertGreater(stats['bool_phase_steps'],0)
        self.assertGreater(stats['int_phase_steps'],0)
        self.assertGreater(stats['phase_switches'],0)
        self.assertGreater(stats['weight_updates'],0)
        for guard,name in [(1,'b'),(2,'c')]:
            for j in range(3):self.assertEqual(int(m['x'+str(guard)+'_'+str(j)])>=100,m[name])

    def test_invalid_numeric_token_errors(self):
        p,m=self.run_native(['( = x 123junk )'],[[1]],'--parse-only')
        self.assertNotEqual(p.returncode,0)
        self.assertIsNone(m)

if __name__ == '__main__': unittest.main()
