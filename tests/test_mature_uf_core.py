import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BIN=Path(os.environ.get('MATURE_BINARY',str(ROOT/'build/ufnia_ls/ufnia_mature')))

class MatureUfCoreTests(unittest.TestCase):
    def solve(self,atoms,clauses,table,interaction='joint',scoring='count',extra=(),env=None):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'input.native';model=Path(tmp)/'model.json'
            source.write_text('\n'.join([str(len(atoms)+1),*[f'{i} {a}' for i,a in enumerate(atoms,1)],'0',str(len(clauses)),*['( '+' '.join(map(str,c))+' )' for c in clauses],'0',f'UFNIA_OBSERVERS 1 {len(table)}',*table])+'\n')
            proc=subprocess.run([str(BIN),str(source),'--seconds','0.1','--seed','0','--model-output',str(model),'--uf-observers','--interaction',interaction,'--scoring',scoring,*extra],text=True,capture_output=True,env=env)
            stats=json.loads(proc.stderr.splitlines()[-1]) if proc.stderr.strip() else {}
            return proc,stats,json.loads(model.read_text()) if model.exists() else None

    def test_cnf_true_uf_conflict_never_early_sat(self):
        for mode in ['none','soft','joint']:
            p,s,m=self.solve(['( = a 0 )','( = b 0 )','( = r0 0 )','( = r1 1 )'],[[1],[2],[3],[4]],['0 1 Int a Int r0','0 1 Int b Int r1'],mode)
            self.assertEqual(p.returncode,0,p.stderr)
            self.assertEqual(p.stdout.strip(),'unknown')
            self.assertIsNone(m)
            self.assertEqual(s['uf_conflicts'],1)
            self.assertEqual(s['backend_version'],'mature-mpz-v2')

    def test_result_group_repair_moves_three_results_together(self):
        p,s,m=self.solve(['( = anchor 1 )','( = ( + r0 ( * -1 r1 ) ) 0 )','( = ( + r1 ( * -1 r2 ) ) 0 )'],[[1],[2],[3]],['0 1 Int x Int '+v for v in ['anchor','r0','r1','r2']])
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual(p.stdout.strip(),'sat',p.stderr)
        self.assertTrue(s['validated'])
        self.assertEqual([m[v] for v in ['anchor','r0','r1','r2']],['1']*4)
        self.assertGreater(s['uf_group_moves'],0)
        self.assertGreaterEqual(s['uf_max_patch'],3)
        self.assertEqual(s['uf_conflicts'],0)

    def test_argument_separation_preserves_fixed_different_results(self):
        p,s,m=self.solve(['( = r0 0 )','( = r1 1 )'],[[1],[2]],['0 1 Int a Int r0','0 1 Int b Int r1'])
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual(p.stdout.strip(),'sat',p.stderr)
        self.assertEqual(m['r0'],'0');self.assertEqual(m['r1'],'1')
        self.assertNotEqual(m['a'],m['b'])
        self.assertGreater(s['uf_separation_moves'],0)
        self.assertEqual(s['uf_conflicts'],0)

    def test_group_candidate_survives_singleton_pool_cap(self):
        atoms=['( = anchor 1 )']+['( = ( + r'+str(i)+' ( * -1 r'+str(i+1)+' ) ) 0 )' for i in range(19)]
        p,s,m=self.solve(atoms,[[i] for i in range(1,len(atoms)+1)],['0 1 Int x Int '+v for v in ['anchor']+['r'+str(i) for i in range(20)]])
        self.assertEqual(p.stdout.strip(),'sat',p.stderr)
        self.assertGreaterEqual(s['uf_max_patch'],20)
        self.assertEqual([m['r'+str(i)] for i in range(20)],['1']*20)

    def test_observer_only_bool_argument_uses_mature_sign_encoding(self):
        p,s,m=self.solve(['( = r0 0 )','( = r1 1 )'],[[1],[2]],['0 1 Bool a Int r0','0 1 Bool b Int r1'])
        self.assertEqual(p.stdout.strip(),'sat',p.stderr)
        self.assertIsInstance(m['a'],bool);self.assertIsInstance(m['b'],bool)
        self.assertNotEqual(m['a'],m['b'])
        self.assertGreater(s['uf_separation_moves'],0)

    def test_boolean_result_group_commits_watch_updates(self):
        p,s,m=self.solve(['anchor','b0','b1','b2'],[[1],[-2,3],[2,-3],[-3,4],[3,-4]],['0 1 Int x Bool '+v for v in ['anchor','b0','b1','b2']])
        self.assertEqual(p.stdout.strip(),'sat',p.stderr)
        self.assertEqual([m[v] for v in ['anchor','b0','b1','b2']],[True]*4)
        self.assertGreater(s['uf_group_moves'],0)

    def test_incidence_deduplicates_same_var_in_args_and_result(self):
        p,s,m=self.solve(['( = anchor 1 )'],[[1]],['0 2 Int x Int x Int x','0 2 Int y Int y Int anchor'])
        self.assertEqual(p.stdout.strip(),'sat',p.stderr)
        if m['x']==m['y']:self.assertEqual(m['x'],m['anchor'])
        self.assertEqual(s['uf_conflicts'],0)

    def test_same_fid_requires_same_typed_signature(self):
        for records in [['0 1 Int a Int r','0 1 Bool b Int r'],['0 1 Int a Int r','0 0 Int r']]:
            p,s,m=self.solve([],[],records,extra=('--parse-only',))
            self.assertNotEqual(p.returncode,0)
            self.assertIsNone(m)
            self.assertIn('signature',s['error'])

    def test_parse_only_registers_table_only_symbols(self):
        p,s,m=self.solve([],[],['0 1 Bool a Int r'],extra=('--parse-only',))
        self.assertEqual(p.returncode,0,p.stderr)
        parsed=json.loads(p.stdout)
        self.assertEqual(parsed['backend_version'],'mature-mpz-v2')
        self.assertEqual(parsed['vars'],2)
        self.assertEqual(parsed['uf_applications'],1)
        self.assertIsNone(m)

    def test_count_and_paws_are_real_observed_policies(self):
        for scoring in ['count','paws']:
            p,s,m=self.solve(['( = ( * x x ) 2 )'],[[1]],[],scoring=scoring)
            self.assertEqual(p.stdout.strip(),'unknown',p.stderr)
            self.assertIsNone(m)
            self.assertEqual(s['scoring'],scoring)
            if scoring=='count':self.assertEqual(s['weight_updates'],0)
            else:self.assertGreater(s['weight_updates'],0)

    def test_late_minority_argument_not_hidden_by_128_member_prefix(self):
        atoms=['( = r0 0 )','( = r1 1 )']+['( = a'+str(i)+' 0 )' for i in range(129)]
        table=['0 1 Int a'+str(i)+' Int r0' for i in range(129)]+['0 1 Int free_arg Int r1']
        p,s,m=self.solve(atoms,[[i] for i in range(1,len(atoms)+1)],table)
        self.assertEqual(p.stdout.strip(),'sat',p.stderr)
        self.assertNotEqual(m['free_arg'],'0')
        self.assertGreater(s['uf_separation_moves'],0)
        self.assertEqual(s['uf_conflicts'],0)

    def test_materialized_even_argument_needs_source_and_proxy_joint_move(self):
        # Exercise the legacy compensation route; affine views use root commits.
        p,s,m=self.solve(['( = ( + proxy ( * -2 source ) ) 0 )','( = zero 0 )','( = r0 0 )','( = r1 1 )'],[[1],[2],[3],[4]],['0 1 Int proxy Int r0','0 1 Int zero Int r1'],env=dict(os.environ,UFNIA_AFFINE_VIEWS='0'))
        self.assertEqual(p.stdout.strip(),'sat',p.stderr)
        self.assertEqual(int(m['proxy']),2*int(m['source']))
        self.assertNotEqual(m['proxy'],'0')
        self.assertGreater(s['uf_compensated_moves'],0)

    def test_materialized_negative_huge_coefficient_is_exact(self):
        coefficient=2**180+2
        # Exercise the legacy compensation route; affine views use root commits.
        p,s,m=self.solve(['( = ( + proxy ( * '+str(coefficient)+' source ) ) 0 )','( = zero 0 )','( = r0 0 )','( = r1 1 )'],[[1],[2],[3],[4]],['0 1 Int proxy Int r0','0 1 Int zero Int r1'],env=dict(os.environ,UFNIA_AFFINE_VIEWS='0'))
        self.assertEqual(p.stdout.strip(),'sat',p.stderr)
        self.assertEqual(int(m['proxy']),-coefficient*int(m['source']))
        self.assertNotEqual(m['proxy'],'0')
        self.assertGreater(s['uf_compensated_moves'],0)

    def test_enabled_observers_require_an_explicit_tail_even_for_zero_apps(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'input.native';source.write_text('1\n0\n0\n0\n')
            old=subprocess.run([str(BIN),str(source),'--parse-only'],text=True,capture_output=True)
            self.assertEqual(old.returncode,0,old.stderr)
            self.assertEqual(json.loads(old.stdout)['backend_version'],'mature-mpz-v1')
            observed=subprocess.run([str(BIN),str(source),'--uf-observers','--parse-only'],text=True,capture_output=True)
            self.assertNotEqual(observed.returncode,0)
            self.assertIn('UF',json.loads(observed.stderr.splitlines()[-1])['error'])

if __name__=='__main__':unittest.main()
