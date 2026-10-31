import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BIN=Path(os.environ.get('MATURE_BINARY',str(ROOT/'build/ufnia_ls/ufnia_mature')))

def native_text(atoms,clauses,applications):
    return '\n'.join([str(len(atoms)+1),*[f'{i} {a}' for i,a in enumerate(atoms,1)],'0',str(len(clauses)),*['( '+' '.join(map(str,c))+' )' for c in clauses],'0',f'UFNIA_OBSERVERS 1 {len(applications)}',*applications])+'\n'

class MatureUfControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work=tempfile.TemporaryDirectory();root=Path(cls.work.name)
        source=root/'trace.cpp';cls.trace=root/'trace'
        source.write_text(r'''#include "native.h"
#include <iostream>
int main(int argc,char** argv){
 nia::NativeInput input;input.read(argv[1],true);nia::ls_solver solver(0,40);solver.uf_enabled=true;solver.uf_scoring="count";input.install(solver);solver.restart_stall_limit=4;solver._cutoff=5;
 std::string action=argc>2?argv[2]:"loop";
 if(action=="loop"){bool result=solver.local_search();std::cout<<"{\"sat\":"<<(result?"true":"false")<<",\"steps\":"<<solver._step<<",\"initializations\":"<<solver.initialization_round<<",\"rebuilds\":"<<solver.uf_bucket_rebuilds<<"}\n";return 0;}
 solver.start=std::chrono::steady_clock::now();solver.initialize();solver._step=10;solver._outer_layer_step=10;
 if(action=="reweight"){
  solver.uf_scoring="paws";solver.uf_weight=2;bool moved=solver.try_uf_move(true);
  std::cout<<"{\"moved\":"<<(moved?"true":"false")<<",\"weight\":"<<solver.uf_weight<<",\"updates\":"<<solver.uf_only_weight_updates<<",\"uf\":"<<solver.uf_conflicts<<",\"cnf\":"<<solver.unsat_clauses->size()<<",\"best_total_cnf\":"<<solver.best_total_arithmetic_unsat<<",\"best_total_uf\":"<<solver.best_total_uf_conflicts<<",\"proposals\":"<<solver.uf_proposals<<",\"evaluations\":"<<solver.uf_joint_evaluations<<"}\n";return 0;
 }
 if(action=="aspire"||action=="newbest"){
  std::fill(solver.tabulist.begin(),solver.tabulist.end(),500);auto before=solver.best_found_cost;
  auto solution=solver._solution;uint64_t conflicts=solver.uf_conflicts,cnf=99,uf=99;
  auto v=solver.name2var.at("a");solver.score_uf_patch({{v,nia::Int(1)}},&cnf,&uf);
  bool restored=solution==solver._solution&&conflicts==solver.uf_conflicts&&before==solver.best_found_cost;
  bool moved=solver.try_uf_move();
  std::cout<<"{\"moved\":"<<(moved?"true":"false")<<",\"restored\":"<<(restored?"true":"false")<<",\"trial_cnf\":"<<cnf<<",\"trial_uf\":"<<uf<<",\"uf\":"<<solver.uf_conflicts<<",\"aspirations\":"<<solver.uf_aspirations<<",\"best_before\":"<<before<<",\"best_after_try\":"<<solver.best_found_cost<<"}\n";return 0;
 }
 if(action=="intclock"){
  auto x=solver.name2var.at("x");solver.tabulist[2*x]=10;bool equality_blocked=solver.uf_patch_tabu({{x,nia::Int(1)}});solver.tabulist[2*x]=11;bool later_blocked=solver.uf_patch_tabu({{x,nia::Int(1)}});
  solver.critical_move(x,1);solver._step=1000;bool reverse_blocked=solver.uf_patch_tabu({{x,nia::Int(0)}});
  std::cout<<"{\"equality_blocked\":"<<(equality_blocked?"true":"false")<<",\"later_blocked\":"<<(later_blocked?"true":"false")<<",\"reverse_blocked\":"<<(reverse_blocked?"true":"false")<<"}\n";return 0;
 }
 auto true_var=solver.name2var.at("true_arg");if(solver._solution[true_var]<0)solver.critical_move(true_var,0);solver.update_best_solution();solver._step=1000;solver._outer_layer_step=10;
 auto x=solver.name2var.at("x");solver.tabulist[2*x]=10;bool equality_blocked=solver.uf_patch_tabu({{x,nia::Int(1)}});solver._outer_layer_step=11;bool expired_blocked=solver.uf_patch_tabu({{x,nia::Int(1)}});
 if(action=="boolclock"){std::cout<<"{\"equality_blocked\":"<<(equality_blocked?"true":"false")<<",\"expired_blocked\":"<<(expired_blocked?"true":"false")<<"}\n";return 0;}
 solver._outer_layer_step=10;std::fill(solver.tabulist.begin(),solver.tabulist.end(),10000);auto initial=solver._solution;bool first=solver.try_uf_move(true);bool waiting=solver.uf_move_waiting;auto clock=solver._outer_layer_step;bool unchanged=initial==solver._solution;
 for(unsigned i=1;i<8;++i)solver.try_uf_move(true);
 std::cout<<"{\"first_moved\":"<<(first?"true":"false")<<",\"waiting\":"<<(waiting?"true":"false")<<",\"unchanged\":"<<(unchanged?"true":"false")<<",\"clock_after_wait\":"<<clock<<",\"waits\":"<<solver.uf_tabu_waits<<",\"overrides\":"<<solver.uf_tabu_overrides<<",\"weight\":"<<solver.uf_weight<<",\"weight_updates\":"<<solver.uf_only_weight_updates<<"}\n";
}''' )
        mature=ROOT/'UFNIA_LS/mature'
        flags=['-DNIA_SCORING_VERIFY','-DNIA_CANDIDATE_VERIFY','-DNIA_FAST_MOVE_VERIFY'] if os.environ.get('MATURE_VERIFY') else []
        p=subprocess.run(['g++','-O1','-std=c++17',*flags,'-I',str(mature),str(source),*[str(mature/x) for x in ['native.cpp','nia_uf.cpp','nia_ls.cpp','nia_initialize.cpp']],'-lgmpxx','-lgmp','-o',str(cls.trace)],capture_output=True,text=True)
        if p.returncode:raise AssertionError(p.stderr)
    @classmethod
    def tearDownClass(cls):cls.work.cleanup()

    def test_uf_only_zero_gain_platform_reweights_then_repairs_arithmetic(self):
        atoms=['( = ( + proxy ( * -1 a ) ( * -1 b ) ) 0 )','( = ( + proxy ( * -1 ( * u u ) ) ) 0 )','( = ( + proxy ( * -1 ( * v v ) ) ) 0 )','( = zero 0 )','( = r0 0 )','( = r1 1 )']
        applications=['0 1 Int proxy Int r0','0 1 Int zero Int r1']
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'input.native';p.write_text(native_text(atoms,[[i] for i in range(1,7)],applications));model=Path(tmp)/'model.json'
            run=subprocess.run([str(BIN),str(p),'--uf-observers','--interaction','joint','--scoring','paws','--seconds','0.2','--seed','0','--model-output',str(model)],text=True,capture_output=True)
            self.assertEqual(run.returncode,0,run.stderr)
            self.assertEqual(run.stdout.strip(),'sat',run.stderr)
            stats=json.loads(run.stderr.splitlines()[-1]);self.assertGreater(stats['uf_only_weight_updates'],0);self.assertGreater(stats['uf_weight'],1)
            self.assertEqual(stats['current_arithmetic_unsat'],0);self.assertEqual(stats['current_uf_conflicts'],0)
            self.assertEqual(stats['best_total_unsat'],stats['best_total_arithmetic_unsat']+stats['best_total_uf_conflicts']);self.assertEqual(stats['best_total_unsat'],0)
            values=json.loads(model.read_text());self.assertEqual(int(values['proxy']),int(values['a'])+int(values['b']));self.assertEqual(int(values['proxy']),int(values['u'])**2);self.assertEqual(int(values['proxy']),int(values['v'])**2)

    def test_uf_only_stall_checks_restarts_before_early_continue(self):
        atoms=['( = r0 0 )','( = r1 1 )','( = zero 0 )','( = one 1 )','( <= x 1 )','( <= ( * -1 x ) 0 )']
        apps=['0 1 Int x Int r0','0 1 Int zero Int r1','0 1 Int one Int r1']
        source=Path(self.work.name)/'restart.native';source.write_text(native_text(atoms,[[i] for i in range(1,7)],apps))
        p=subprocess.run([str(self.trace),str(source)],text=True,capture_output=True)
        self.assertEqual(p.returncode,0,p.stderr)
        result=json.loads(p.stdout)
        self.assertFalse(result['sat']);self.assertEqual(result['steps'],40)
        self.assertGreater(result['initializations'],1)
        self.assertEqual(result['rebuilds'],result['initializations'])

    def trace_case(self,atoms,apps,action):
        source=Path(self.work.name)/('trace-'+action+'.native');source.write_text(native_text(atoms,[[i] for i in range(1,len(atoms)+1)],apps))
        p=subprocess.run([str(self.trace),str(source),action],text=True,capture_output=True)
        self.assertEqual(p.returncode,0,p.stderr)
        return json.loads(p.stdout)

    def test_tabu_sat_and_new_raw_best_aspiration_share_one_trial_state(self):
        for n,action in [(2,'aspire'),(3,'newbest')]:
            result=self.trace_case(['( = r'+str(i)+' '+str(i)+' )' for i in range(n)],['0 1 Int '+chr(97+i)+' Int r'+str(i) for i in range(n)],action)
            self.assertTrue(result['restored'])
            self.assertTrue(result['moved'])
            self.assertGreater(result['aspirations'],0)
            self.assertEqual(result['trial_cnf'],0)
            self.assertEqual(result['trial_uf'],n-2)
            self.assertEqual(result['uf'],n-2)
            self.assertEqual(result['best_before'],n-1)
            self.assertEqual(result['best_after_try'],n-1)

    def test_int_tabu_expiry_and_immediate_reverse_are_separate(self):
        result=self.trace_case(['( = r0 0 )','( = r1 1 )','( = zero 0 )','( = one 1 )','( <= x 1 )','( <= ( * -1 x ) 0 )'],['0 1 Int x Int r0','0 1 Int zero Int r1','0 1 Int one Int r1'],'intclock')
        self.assertFalse(result['equality_blocked'])
        self.assertTrue(result['later_blocked'])
        self.assertTrue(result['reverse_blocked'])

    def boolean_clock_fixture(self,action):
        return self.trace_case(['false_arg','true_arg','( = r0 0 )','( = r1 1 )'],['0 1 Bool x Int r0','0 1 Bool false_arg Int r1','0 1 Bool true_arg Int r1'],action)

    def test_bool_tabu_uses_outer_clock_and_strict_expiry(self):
        # trace_case asserts all atoms positively; replace the false-arg unit.
        source=Path(self.work.name)/'boolclock.native'
        source.write_text(native_text(['false_arg','true_arg','( = r0 0 )','( = r1 1 )'],[[-1],[2],[3],[4]],['0 1 Bool x Int r0','0 1 Bool false_arg Int r1','0 1 Bool true_arg Int r1']))
        p=subprocess.run([str(self.trace),str(source),'boolclock'],text=True,capture_output=True)
        self.assertEqual(p.returncode,0,p.stderr);result=json.loads(p.stdout)
        self.assertTrue(result['equality_blocked'])
        self.assertFalse(result['expired_blocked'])

    def test_all_tabu_wait_ages_bool_clock_and_override_is_bounded(self):
        source=Path(self.work.name)/'boolwait.native'
        source.write_text(native_text(['false_arg','true_arg','( = r0 0 )','( = r1 1 )'],[[-1],[2],[3],[4]],['0 1 Bool x Int r0','0 1 Bool false_arg Int r1','0 1 Bool true_arg Int r1']))
        p=subprocess.run([str(self.trace),str(source),'wait'],text=True,capture_output=True)
        self.assertEqual(p.returncode,0,p.stderr);result=json.loads(p.stdout)
        self.assertFalse(result['first_moved']);self.assertTrue(result['waiting']);self.assertTrue(result['unchanged'])
        self.assertEqual(result['clock_after_wait'],11)
        self.assertEqual(result['waits'],8);self.assertEqual(result['overrides'],1)
        self.assertEqual(result['weight'],1);self.assertEqual(result['weight_updates'],0)

    def test_paws_weight_change_reselects_winner_and_preserves_paired_best(self):
        atoms=['( = ( + proxy ( * -1 a ) ( * -1 b ) ) 0 )','( = ( + proxy ( * -1 ( * u u ) ) ) 0 )','( = ( + proxy ( * -1 ( * v v ) ) ) 0 )','( = zero 0 )','( = r0 0 )','( = r1 1 )']
        result=self.trace_case(atoms,['0 1 Int proxy Int r0','0 1 Int zero Int r1'],'reweight')
        self.assertTrue(result['moved']);self.assertEqual(result['weight'],3);self.assertEqual(result['updates'],1)
        self.assertEqual(result['uf'],0);self.assertEqual(result['cnf'],2)
        self.assertEqual(result['best_total_cnf'],0);self.assertEqual(result['best_total_uf'],1)
        self.assertGreaterEqual(result['evaluations'],2*result['proposals'])

    def test_zero_budget_observers_never_move_or_publish_a_model(self):
        atoms=['( = a 0 )','( = b 0 )','( = r0 0 )','( = r1 1 )']
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'zero.native';model=Path(tmp)/'model.json';source.write_text(native_text(atoms,[[1],[2],[3],[4]],['0 1 Int a Int r0','0 1 Int b Int r1']))
            p=subprocess.run([str(BIN),str(source),'--uf-observers','--interaction','joint','--scoring','paws','--seconds','0','--model-output',str(model)],text=True,capture_output=True)
            self.assertEqual(p.returncode,0,p.stderr);self.assertEqual(p.stdout.strip(),'unknown');self.assertFalse(model.exists())
            stats=json.loads(p.stderr.splitlines()[-1]);self.assertFalse(stats['validated']);self.assertEqual(stats['steps'],0);self.assertEqual(stats['uf_proposals'],0)
            self.assertIsNone(stats['current_arithmetic_unsat']);self.assertIsNone(stats['current_uf_conflicts']);self.assertIsNone(stats['best_total_unsat'])

if __name__=='__main__':unittest.main()
