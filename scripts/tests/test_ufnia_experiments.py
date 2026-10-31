"""Integration tests for validated UFNIA experiments, using real subprocesses."""
import csv
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest


RUNNER = Path(__file__).resolve().parents[1] / "run_ufnia_experiments.py"


class UfniaExperimentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ufnia-experiment-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.solver = self.root / "solver"
        (self.solver / "UFNIA_LS").mkdir(parents=True)
        (self.solver / "build/ufnia_ls/lib").mkdir(parents=True)
        (self.solver / "build/ufnia_ls/ufnia_core").write_text("fake binary")
        (self.solver / "UFNIA_LS/run.py").write_text('''
import argparse,hashlib,json,os,pathlib,signal,subprocess,sys,time
p=argparse.ArgumentParser()
p.add_argument('input');p.add_argument('--seconds',type=float,default=600);p.add_argument('--seed',type=int,default=0)
p.add_argument('--backend',choices=('core','mature'),default='core')
p.add_argument('--uf-observers',action='store_true')
p.add_argument('--interaction',default='joint');p.add_argument('--scoring',default='count')
p.add_argument('--json-report');p.add_argument('--model-output');p.add_argument('--keep-dir')
a=p.parse_args(); action=pathlib.Path(a.input).read_text().strip()
if action=='spawn':
 child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])
 pathlib.Path(os.environ['TEST_PIDS']).write_text(json.dumps([os.getpid(),child.pid]))
 time.sleep(60)
if action=='killed': os.kill(os.getpid(),signal.SIGKILL)
if action=='parallel':
 import fcntl
 def update(delta):
  with open(os.environ['TEST_COUNTER'],'r+') as f:
   fcntl.flock(f,fcntl.LOCK_EX);s=json.load(f);s['active']+=delta
   s['maximum']=max(s['active'],s['maximum']);f.seek(0);json.dump(s,f);f.truncate()
 update(1);time.sleep(.15);update(-1)
assert os.environ['OMP_NUM_THREADS']=='1'
assert str(pathlib.Path(__file__).parents[1]/'build/ufnia_ls/lib') in os.environ['LD_LIBRARY_PATH']
work=pathlib.Path(a.keep_dir)/'run-fake';work.mkdir(parents=True)
query={'index':1,'status':'sat','validated':True,'original_model_validated':True,
       'core':{'status':'sat','steps':12,'validated':True}}
if action=='unverified':query['original_model_validated']=False
if action=='fake':query['status']='unknown'
if action=='candidate-unknown':
 query.update(status='unknown',validated=False,original_model_validated=False)
 query['core'].update(status='unknown',validated=False)
 (work/'query-000001.round-0000.candidate.json').write_text(json.dumps({'status':'candidate','validated':False,'values':{'x':'0'}}))
 (work/'query-000001.round-0000.violations.json').write_text(json.dumps({'arithmetic_unsatisfied':[1]}))
backend_evidence={}
if a.backend=='mature' and action!='mature-missing-proof':
 binary=pathlib.Path(__file__).parents[1]/'build/ufnia_ls/ufnia_mature'
 requested={'backend':'mature','seconds':a.seconds,'seed':a.seed,'preprocess_timeout':120.0,
  'ackermann_limit':1000000,'uf_observers':a.uf_observers,'interaction':a.interaction,'scoring':a.scoring,
  'cnf':'on','distance':'classic','arithmetic_repair':'off','active_arithmetic_rows':'off',
  'post_lower_eqs':'off','search_policy':'legacy','search_phase':'mixed',
  'candidate_policy':'broad','dump_candidate':False}
 effective=dict(requested)
 for key in ['interaction','scoring','distance','arithmetic_repair','active_arithmetic_rows','search_policy','search_phase','candidate_policy']:effective[key]=None
 if a.uf_observers:
  effective.update(interaction=a.interaction,scoring=a.scoring)
 backend_evidence={'backend':'mature','backend_configuration':'mature_uf_observed' if a.uf_observers else 'mature_intrinsic',
  'core_binary':str(binary),'core_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),
  'requested_options':requested,'effective_options':effective}
 if action=='mature-wrong-options':
  requested['seed']+=1;effective['seed']+=1
 if action=='mature-wrong-binary':backend_evidence['core_sha256']='0'*64
 query.update(backend_evidence)
 query['core'].update(backend='mature_mpz',backend_version='mature-mpz-v1')
 if a.uf_observers:
  query['core'].update(backend_version='mature-mpz-v2',uf_observers=True,interaction=a.interaction,
   scoring=a.scoring,validation_scope='native_cnf_and_uf_congruence',uf_applications=0,uf_conflicts=0)
  if action=='mature-masked-observers':effective.update(interaction=None,scoring=None)
  if action=='mature-old-version':query['core']['backend_version']='mature-mpz-v1'
  if action=='mature-uf-conflict':query['core']['uf_conflicts']=1
  if action=='mature-uf-scope':query['core']['validation_scope']='native_cnf'
  if action=='mature-uf-ignored-scoring':query['core']['scoring']='count'
  if action=='mature-uf-unvalidated':query['core']['validated']=False
  if action=='mature-uf-missing-native':query.pop('core')
 if action=='mature-wrong-engine':query['core']['backend']='ufnia_core'
queries=[query]
if action=='multi':
 bad=dict(query,index=2,original_model_validated=False);queries=[query,bad]
for q in queries:(work/('query-%06d.report.json'%q['index'])).write_text(json.dumps(q))
(work/'query-000001.normalized.smt2').write_text('large intermediate')
(work/'query-000001.metadata.json').write_text('large metadata')
(work/'query-000001.core-model.json').write_text('ordinary intermediate model')
summary={'input':a.input,'mode':'solve','status':'complete','queries_seen':len(queries),
 'queries_processed':len(queries),'session':{'checks':len(queries)},
 'counts':{'sat':sum(q['status']=='sat' for q in queries),'unknown':sum(q['status']=='unknown' for q in queries),'errors':0},
 'complete_solver_used':False,'last_query':queries[-1],'model_output_written':True}
pathlib.Path(a.model_output).write_text('; verified model\\n')
summary.update(backend_evidence)
if a.backend=='mature':
 summary['model_output']=a.model_output
 summary['model_output_sha256']=hashlib.sha256(pathlib.Path(a.model_output).read_bytes()).hexdigest()
 if action=='mature-wrong-model':pathlib.Path(a.model_output).write_text('; swapped model\\n')
pathlib.Path(a.json_report).write_text(json.dumps(summary))
print('sat')
''')
        self.benchmarks = self.root / "benchmarks"
        self.benchmarks.mkdir()
        self.output = self.root / "output"

    def case(self, name, action="sat"):
        p = self.benchmarks / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(action)
        return p

    def command(self, *extra):
        return [sys.executable, str(RUNNER), "--solver-root", str(self.solver),
                "--benchmarks", str(self.benchmarks), "--output", str(self.output),
                "--require-host", socket.gethostname(), "--jobs", "2", "--timeout", "2", *extra]

    def run_runner(self, *extra, env=None):
        return subprocess.run(self.command(*extra), capture_output=True, text=True,
                              timeout=15, env=env)

    def successful_run(self, process):
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        runs = list(self.output.glob("run-*"))
        self.assertTrue(runs, process.stdout + process.stderr)
        return max(runs, key=lambda p:p.stat().st_mtime_ns)

    def rows(self, run):
        with (run / "results.csv").open(newline="") as stream:
            return {r["benchmark"]: r for r in csv.DictReader(stream)}

    def mature_assets(self):
        directory=self.solver/"UFNIA_LS/mature/nested"
        directory.mkdir(parents=True)
        (directory/"engine.hpp").write_text("// mature engine header\n")
        (directory.parent/"COPYING").write_text("mature source provenance\n")
        binary=self.solver/"build/ufnia_ls/ufnia_mature"
        binary.write_text("fake mature binary");binary.chmod(0o755)
        return binary

    def test_mature_assets_are_required_before_creating_a_run(self):
        self.case("family/a.smt2")
        for stage in ("no-binary","no-source"):
            if stage=="no-source":
                binary=self.solver/"build/ufnia_ls/ufnia_mature"
                binary.write_text("fake mature binary");binary.chmod(0o755)
            p=self.run_runner("--solver-args",'["--backend","mature"]')
            self.assertEqual(p.returncode,2,p.stdout+p.stderr)
            self.assertFalse(self.output.exists(),stage)

    def test_mature_records_all_assets_and_rejects_wrong_backend_options_and_model(self):
        self.mature_assets()
        (self.solver/"build/ufnia_ls/ufnia_core").unlink()
        actions=["sat","mature-wrong-engine","mature-wrong-options","mature-wrong-model", "mature-wrong-binary","mature-missing-proof"]
        for action in actions:self.case("family/"+action+".smt2",action)
        run=self.successful_run(self.run_runner("--solver-args",'["--backend=mature","--seed","7"]'))
        metadata=json.loads((run/"run.json").read_text())
        self.assertEqual(metadata.get("backend"),"mature")
        self.assertEqual(metadata.get("requested_options",{}).get("seed"),7)
        self.assertIsNone(metadata.get("effective_options",{}).get("scoring"))
        self.assertIn("UFNIA_LS/mature/nested/engine.hpp",metadata["source_sha256"])
        self.assertIn("UFNIA_LS/mature/COPYING",metadata["source_sha256"])
        self.assertIn("build/ufnia_ls/ufnia_mature",metadata["binary_sha256"])
        rows=self.rows(run)
        self.assertEqual(rows["family/sat.smt2"]["result"],"sat")
        for action in actions[1:]:self.assertEqual(rows["family/"+action+".smt2"]["result"],"error",action)

    def test_mature_rejects_an_existing_snapshot_that_omits_its_assets(self):
        self.mature_assets();self.case("family/a.smt2")
        (self.solver/"snapshot.json").write_text(json.dumps({"files":{"UFNIA_LS/run.py":hashlib.sha256((self.solver/"UFNIA_LS/run.py").read_bytes()).hexdigest()}}))
        p=self.run_runner("--solver-args",'["--backend","mature"]')
        self.assertEqual(p.returncode,2,p.stdout+p.stderr)
        self.assertFalse(self.output.exists())

    def test_mature_observers_require_v2_real_policies_and_zero_conflicts(self):
        self.mature_assets()
        actions=['sat','mature-masked-observers','mature-old-version','mature-uf-conflict',
                 'mature-uf-scope','mature-uf-ignored-scoring','mature-uf-unvalidated',
                 'mature-uf-missing-native']
        for action in actions:self.case('family/'+action+'.smt2',action)
        run=self.successful_run(self.run_runner('--solver-args',
            '["--backend","mature","--uf-observers","--interaction","soft","--scoring","paws"]'))
        metadata=json.loads((run/'run.json').read_text())
        self.assertEqual(metadata['backend_configuration'],'mature_uf_observed')
        self.assertEqual(metadata['effective_options']['interaction'],'soft')
        self.assertEqual(metadata['effective_options']['scoring'],'paws')
        self.assertIsNone(metadata['effective_options']['search_policy'])
        rows=self.rows(run)
        self.assertEqual(rows['family/sat.smt2']['result'],'sat')
        for action in actions[1:]:self.assertEqual(rows['family/'+action+'.smt2']['result'],'error',action)

    def test_rejects_wrong_host_before_creating_output(self):
        self.case("one.smt2")
        p = self.run_runner("--require-host", "definitely-not-this-host")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("host", p.stderr.lower())
        self.assertFalse(self.output.exists())

    def test_counts_only_original_verified_models_and_checks_every_query(self):
        for action in ["sat", "fake", "unverified", "multi", "killed"]:
            self.case("family/" + action + ".smt2", action)
        run = self.successful_run(self.run_runner())
        rows = self.rows(run)
        self.assertEqual(rows["family/sat.smt2"]["result"], "sat")
        for action in ["fake", "unverified", "multi", "killed"]:
            self.assertNotEqual(rows["family/" + action + ".smt2"]["result"], "sat")
        self.assertEqual(rows["family/killed.smt2"]["result"], "killed")
        self.assertEqual(json.loads((run / "summary.json").read_text())["counts"]["sat"], 1)
        for row in rows.values():
            self.assertTrue(row["benchmark_sha256"])
            self.assertIn("max_rss_kb", row)
        row = rows["family/sat.smt2"]
        for field in ["stdout", "stderr", "json_report", "model"]:
            self.assertTrue((run / row[field]).is_file(), field)
        self.assertTrue(list((run / "cases/family").rglob("*.report.json")))
        self.assertFalse(list(run.rglob("*.normalized.smt2")))

    def test_unknown_diagnostic_candidates_are_retained_without_becoming_sat(self):
        self.case("family/unknown.smt2","candidate-unknown")
        run=self.successful_run(self.run_runner())
        row=self.rows(run)["family/unknown.smt2"]
        self.assertEqual(row["result"],"unknown")
        summary=json.loads((run/"summary.json").read_text())
        self.assertEqual(summary["counts"]["sat"],0)
        self.assertEqual(summary["counts"]["unknown"],1)
        candidates=list(run.rglob("*.candidate.json"))
        violations=list(run.rglob("*.violations.json"))
        self.assertEqual(len(candidates),1)
        self.assertEqual(len(violations),1)
        self.assertEqual(json.loads(candidates[0].read_text())["status"],"candidate")
        self.assertTrue(list(run.rglob("*.report.json")))
        for suffix in ("*.normalized.smt2","*.metadata.json","*.core-model.json"):
            self.assertFalse(list(run.rglob(suffix)),suffix)

    def test_timeout_kills_the_complete_process_group(self):
        self.case("nested/spawn.smt2", "spawn")
        pids_file = self.root / "pids.json"
        env = dict(os.environ, TEST_PIDS=str(pids_file))
        p = self.run_runner("--timeout", "0.5", env=env)
        run = self.successful_run(p)
        self.assertEqual(self.rows(run)["nested/spawn.smt2"]["result"], "timeout")
        self.assertTrue(pids_file.exists())
        for pid in json.loads(pids_file.read_text()):
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                status = Path(f"/proc/{pid}/stat")
                if not status.exists() or status.read_text().split(")", 1)[1].split()[0] == "Z":
                    break
                time.sleep(.02)
            else:
                self.fail(f"process {pid} survived timeout")

    def test_completed_process_observed_after_deadline_is_not_accepted_as_sat(self):
        self.case("family/late.smt2")
        # Simulate scheduler delay after wait() has reaped an otherwise valid SAT.
        wrapper = '''
import runpy,subprocess,sys,time
entry=runpy.run_path(sys.argv[1])
original_wait=subprocess.Popen.wait
def delayed_wait(self,*args,**kwargs):
 result=original_wait(self,*args,**kwargs)
 time.sleep(.2)
 return result
subprocess.Popen.wait=delayed_wait
sys.exit(entry['main'](sys.argv[2:]))
'''
        p = subprocess.run([sys.executable, "-c", wrapper, str(RUNNER),
                            *self.command()[2:], "--timeout", "0.15"],
                           capture_output=True, text=True, timeout=15)
        run = self.successful_run(p)
        self.assertEqual(self.rows(run)["family/late.smt2"]["result"], "timeout")

    def test_query_validation_crossing_final_deadline_cannot_publish_sat(self):
        self.assert_postprocessing_deadline('query')

    def test_cleanup_crossing_final_deadline_cannot_publish_sat(self):
        self.assert_postprocessing_deadline('cleanup')

    def assert_postprocessing_deadline(self,stage):
        self.case('family/late-postprocessing.smt2')
        wrapper='''
import pathlib,runpy,sys,time
entry=runpy.run_path(sys.argv[1])
if sys.argv[2]=='query':
 original=entry['query_results']
 def delayed_query(*args,**kwargs):
  result=original(*args,**kwargs)
  time.sleep(.35)
  return result
 entry['main'].__globals__['query_results']=delayed_query
else:
 original=pathlib.Path.unlink
 def delayed_cleanup(self,*args,**kwargs):
  if self.name.endswith('.normalized.smt2'):time.sleep(.35)
  return original(self,*args,**kwargs)
 pathlib.Path.unlink=delayed_cleanup
sys.exit(entry['main'](sys.argv[3:]))
'''
        process=subprocess.run([sys.executable,'-c',wrapper,str(RUNNER),stage,
                                *self.command()[2:],'--timeout','.3'],
                               capture_output=True,text=True,timeout=15)
        run=self.successful_run(process);row=self.rows(run)['family/late-postprocessing.smt2']
        self.assertGreaterEqual(float(row['wall_time_seconds']),.3)
        self.assertEqual(row['result'],'timeout')
        self.assertEqual(json.loads((run/'summary.json').read_text())['counts']['sat'],0)

    def test_parallel_limit_manifest_hashes_and_preserved_run_directories(self):
        counter = self.root / "counter.json"
        counter.write_text(json.dumps({"active":0,"maximum":0}))
        for i in range(4):self.case(f"family/{i}.smt2", "parallel")
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps(["family/0.smt2", "family/2.smt2", "family/3.smt2"]))
        env = dict(os.environ, TEST_COUNTER=str(counter))
        one = self.successful_run(self.run_runner("--manifest", str(manifest), "--limit", "2",
                                                "--solver-args", '["--seed", "7"]', env=env))
        self.assertEqual(set(self.rows(one)), {"family/0.smt2", "family/2.smt2"})
        self.assertEqual(json.loads(counter.read_text())["maximum"], 2)
        summary = json.loads((one / "summary.json").read_text())
        self.assertIn("actual_peak_concurrent_cases", summary)
        self.assertEqual(summary["actual_peak_concurrent_cases"], 2)
        metadata = json.loads((one / "run.json").read_text())
        self.assertEqual(metadata["hostname"], socket.gethostname())
        self.assertEqual(metadata["jobs"], 2)
        self.assertEqual(metadata["timeout_seconds"], 2)
        self.assertEqual(metadata["binary_sha256"]["build/ufnia_ls/ufnia_core"],
                         hashlib.sha256(b"fake binary").hexdigest())
        self.assertIn("UFNIA_LS/run.py", metadata["source_sha256"])
        two = self.successful_run(self.run_runner("--limit", "1", env=env))
        self.assertNotEqual(one, two)
        self.assertTrue((one / "results.csv").exists())

    def test_memory_admission_wait_is_separate_from_the_case_timeout(self):
        counter=self.root/"counter.json"
        counter.write_text(json.dumps({"active":0,"maximum":0}))
        for i in range(2):self.case(f"family/{i}.smt2","parallel")
        profile=self.root/"profile.csv"
        profile.write_text("benchmark,max_rss_kb\nfamily/0.smt2,716800\nfamily/1.smt2,716800\n")
        # A synthetic 1GiB host makes each profiled case consume the full budget.
        wrapper='''
import runpy,sys
entry=runpy.run_path(sys.argv[1])
entry['run'].__globals__['memory_kb']=lambda:(1048576,1048576)
sys.exit(entry['main'](sys.argv[2:]))
'''
        p=subprocess.run([sys.executable,"-c",wrapper,str(RUNNER),*self.command()[2:],
                          "--timeout","0.35","--memory-profile",str(profile),"--memory-reserve-gb","0"],
                         env=dict(os.environ,TEST_COUNTER=str(counter)),capture_output=True,text=True,timeout=15)
        run=self.successful_run(p)
        rows=list(self.rows(run).values())
        self.assertTrue(all(row["result"]=="sat" for row in rows))
        self.assertGreater(max(float(row["admission_wait_seconds"]) for row in rows),.1)
        self.assertTrue(all(float(row["wall_time_seconds"])<.35 for row in rows))
        self.assertEqual(json.loads(counter.read_text())["maximum"],1)
        summary=json.loads((run/"summary.json").read_text())
        self.assertEqual(summary["peak_reserved_memory_kb"],1048576)
        self.assertEqual(summary["actual_peak_concurrent_cases"],1)


class MemoryAdmissionTests(unittest.TestCase):
    def module(self):
        spec=importlib.util.spec_from_file_location("ufnia_runner_memory",RUNNER)
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_estimates_profile_floor_large_sources_and_budget_cap(self):
        module=self.module()
        self.assertTrue(hasattr(module,"estimate_memory_kb"),"memory estimator is missing")
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"sample.smt2";path.write_text("sat")
            self.assertEqual(module.estimate_memory_kb(path,300*1024,32*1024**2),450*1024)
            self.assertEqual(module.estimate_memory_kb(path,None,32*1024**2),256*1024)
            with path.open("wb") as stream:stream.truncate(11*1024**2)
            self.assertEqual(module.estimate_memory_kb(path,None,32*1024**2),16*1024**2)
            self.assertEqual(module.estimate_memory_kb(path,None,4*1024**2),4*1024**2)

    def test_budget_release_wakes_waiter_and_stop_unblocks_wait(self):
        module=self.module()
        self.assertTrue(hasattr(module,"MemoryAdmission"),"memory admission is missing")
        stop=threading.Event()
        gate=module.MemoryAdmission(total_kb=1024,reserve_kb=24,available=lambda:1024,stop=stop)
        self.assertTrue(gate.acquire(700))
        acquired=threading.Event()
        def waiter():
            if gate.acquire(700):acquired.set();gate.release(700)
        thread=threading.Thread(target=waiter);thread.start()
        self.assertFalse(acquired.wait(.1))
        gate.release(700)
        self.assertTrue(acquired.wait(1));thread.join(1)
        self.assertFalse(thread.is_alive())
        unavailable=module.MemoryAdmission(total_kb=1024,reserve_kb=24,available=lambda:0,stop=stop)
        result=[]
        thread=threading.Thread(target=lambda:result.append(unavailable.acquire(700)))
        thread.start();time.sleep(.05);stop.set();unavailable.wake();thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result,[False])


if __name__ == "__main__":
    unittest.main()
