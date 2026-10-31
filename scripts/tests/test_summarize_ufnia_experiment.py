"""Independent artifact acceptance must not trust a runner's SAT totals."""
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "summarize_ufnia_experiment.py"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class SummarizerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ufnia-audit-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.inputs = self.root / "inputs"
        self.inputs.mkdir()
        self.solver = self.root / "solver"
        (self.solver / "UFNIA_LS").mkdir(parents=True)
        (self.solver / "build/ufnia_ls").mkdir(parents=True)
        (self.solver / "UFNIA_LS/run.py").write_text("# frozen driver\n")
        (self.solver / "build/ufnia_ls/ufnia_core").write_bytes(b"frozen binary")

    def make_run(self, name, cases):
        run = self.root / name
        run.mkdir()
        fields = ["benchmark", "result", "exit_code", "signal", "wall_time_seconds", "validated",
                  "original_model_validated", "source_unchanged", "benchmark_sha256", "json_report", "model"]
        rows = []
        for benchmark, action in cases.items():
            source = self.inputs / benchmark
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text("(set-logic QF_UFNIA)\n(assert true)\n(check-sat)\n")
            base = run / "cases" / benchmark
            base.parent.mkdir(parents=True, exist_ok=True)
            work = Path(str(base) + ".work/run-fixture")
            work.mkdir(parents=True)
            query = {"index":1, "status":"sat", "validated":True,
                     "original_model_validated":True, "validation_scope":"original_assertions",
                     "core":{"status":"sat", "validated":True}}
            queries = [query]
            if action == "unverified":query["original_model_validated"] = False
            if action == "multi":queries.append(dict(query,index=2,original_model_validated=False))
            for q in queries:
                (work / f"query-{q['index']:06d}.report.json").write_text(json.dumps(q))
            model = Path(str(base) + ".model.smt2")
            model.write_text("".join(f"; query {q['index']}\n(model)\n" for q in queries))
            report = {"input":str(source), "mode":"solve", "status":"complete",
                      "queries_seen":len(queries), "queries_processed":len(queries),
                      "session":{"checks":len(queries)}, "counts":{"sat":len(queries),"unknown":0,"errors":0},
                      "complete_solver_used":False, "last_query":queries[-1],
                      "model_output_written":True, "model_output":str(model)}
            Path(str(base) + ".json").write_text(json.dumps(report))
            if action == "missing-model":model.unlink()
            if action == "fake-model":model.write_text("; no actual model\n")
            if action == "partial-query":(work / "query-000001.report.json").unlink()
            row = dict(benchmark=benchmark, result="sat", exit_code="0", signal="", wall_time_seconds="0.5",
                       validated="True", original_model_validated="True", source_unchanged="True",
                       benchmark_sha256=digest(source), json_report=str(Path(str(base) + ".json").relative_to(run)),
                       model=str(model.relative_to(run)))
            if action == "timeout":row.update(result="timeout",wall_time_seconds="600.1")
            rows.append(row)
        metadata = {"hostname":"node3", "jobs":180, "workers":180, "timeout_seconds":600,
                    "selected":len(rows), "discovered":len(rows), "status":"finished",
                    "solver_root":str(self.solver), "benchmarks":str(self.inputs),
                    "source_sha256":{"UFNIA_LS/run.py":digest(self.solver / "UFNIA_LS/run.py")},
                    "binary_sha256":{"build/ufnia_ls/ufnia_core":digest(self.solver / "build/ufnia_ls/ufnia_core")}}
        summary = {"hostname":"node3", "jobs":180, "timeout_seconds":600, "selected":len(rows),
                   "recorded":len(rows),"not_run":0,"status":"finished", "exit_code":0,
                   "tools_changed_during_run":False,"eligible_for_comparison":True,
                   "counts":{"sat":sum(r['result']=='sat' for r in rows), "timeout":sum(r['result']=='timeout' for r in rows)}}
        (run / "run.json").write_text(json.dumps(metadata))
        (run / "summary.json").write_text(json.dumps(summary))
        (run / "manifest.json").write_text(json.dumps(list(cases)))
        with (run / "results.csv").open("w",newline="") as f:
            writer = csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(rows)
        return run

    def audit(self, run, name="audit", *extra):
        output = self.root / name
        p = subprocess.run([sys.executable,"-B",str(SCRIPT),str(run),"--output",str(output),*extra],
                           capture_output=True,text=True,timeout=15)
        self.assertIn(p.returncode, (0,1),p.stdout+p.stderr)
        self.assertTrue((output / "audit.json").exists(),p.stdout+p.stderr)
        return p, json.loads((output / "audit.json").read_text()), output

    def test_recounts_only_unique_complete_evidence_and_real_models(self):
        run = self.make_run("run", {f"family/{action}.smt2":action for action in
                            ["sat","unverified","multi","missing-model","fake-model","partial-query","timeout"]})
        p,audit,out = self.audit(run)
        self.assertEqual(audit["verified_sat"],1)
        self.assertFalse(audit["eligible_for_acceptance"])
        self.assertEqual(p.returncode,1)
        with (out / "detailed.csv").open() as f: rows=list(csv.DictReader(f))
        self.assertEqual(sum(r["verified_sat"]=="True" for r in rows),1)
        with (out / "family_summary.csv").open() as f: family=next(csv.DictReader(f))
        self.assertEqual(family["verified_sat"],"1")

    def test_rejects_duplicate_rows_and_forged_summary_counts(self):
        run = self.make_run("run", {"family/a.smt2":"sat"})
        path = run / "results.csv"
        lines=path.read_text().splitlines();path.write_text("\n".join(lines+[lines[-1]])+"\n")
        summary=json.loads((run/"summary.json").read_text());summary["counts"]["sat"]=400
        (run/"summary.json").write_text(json.dumps(summary))
        p,audit,_=self.audit(run)
        self.assertEqual(p.returncode,1)
        self.assertEqual(audit["verified_sat"],0)
        self.assertTrue(any("duplicate" in issue for issue in audit["issues"]))
        self.assertTrue(any("count" in issue for issue in audit["issues"]))

    def test_detects_tampered_sources_and_never_follows_artifacts_outside_run(self):
        run=self.make_run("run", {"family/a.smt2":"sat"})
        (self.inputs/"family/a.smt2").write_text("changed source")
        p,audit,_=self.audit(run,"changed-source")
        self.assertEqual(p.returncode,1)
        self.assertEqual(audit["verified_sat"],0)
        self.assertTrue(any("checksum" in issue for issue in audit["issues"]))
        run=self.make_run("external-run", {"family/a.smt2":"sat"})
        report=run/"cases/family/a.smt2.json";report.unlink();report.symlink_to(self.root/"external.json")
        (self.root/"external.json").write_text("{}")
        p,audit,_=self.audit(run,"external-artifact")
        self.assertEqual(p.returncode,1)
        self.assertEqual(audit["verified_sat"],0)
        self.assertTrue(any("external" in issue for issue in audit["issues"]))

    def test_compares_verified_sets_and_rejects_different_input_content(self):
        baseline=self.make_run("baseline", {"family/common.smt2":"sat","family/lost.smt2":"sat"})
        candidate=self.make_run("candidate", {"family/common.smt2":"sat","family/new.smt2":"sat"})
        p,audit,_=self.audit(candidate,"comparison","--compare",str(baseline))
        self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertTrue(audit["eligible_for_acceptance"])
        self.assertEqual(audit["comparison"]["new_sat"],["family/new.smt2"])
        self.assertEqual(audit["comparison"]["lost_sat"],["family/lost.smt2"])
        metadata=json.loads((baseline/"run.json").read_text())
        metadata["jobs"]=1
        (baseline/"run.json").write_text(json.dumps(metadata))
        p,audit,_=self.audit(candidate,"invalid-baseline","--compare",str(baseline))
        self.assertEqual(p.returncode,1)
        self.assertFalse(audit["comparison"]["baseline_eligible"])

    def set_host(self,run,host):
        for filename in ("run.json","summary.json"):
            path=run/filename;metadata=json.loads(path.read_text());metadata["hostname"]=host
            path.write_text(json.dumps(metadata))

    def mature_evidence(self,run,observed=False):
        mature=self.solver/"UFNIA_LS/mature/nested";mature.mkdir(parents=True,exist_ok=True)
        (mature/"engine.hpp").write_text("mature header")
        binary=self.solver/"build/ufnia_ls/ufnia_mature";binary.write_bytes(b"mature binary")
        requested={"backend":"mature","seconds":600.0,"seed":7,"preprocess_timeout":120.0,
                   "ackermann_limit":1000000,"uf_observers":observed,"interaction":"joint","scoring":"paws" if observed else "count",
                   "cnf":"on","distance":"classic","arithmetic_repair":"off","active_arithmetic_rows":"off",
                   "post_lower_eqs":"off","search_policy":"legacy","search_phase":"mixed",
                   "candidate_policy":"broad","dump_candidate":False}
        effective=dict(requested)
        for key in ["interaction","scoring","distance","arithmetic_repair","active_arithmetic_rows","search_policy","search_phase","candidate_policy"]:effective[key]=None
        if observed:effective.update(interaction=requested['interaction'],scoring=requested['scoring'])
        binding={"backend":"mature","backend_configuration":"mature_uf_observed" if observed else "mature_intrinsic","core_binary":str(binary),
                 "core_sha256":digest(binary),"requested_options":requested,"effective_options":effective}
        metadata=json.loads((run/"run.json").read_text());metadata.update(binding)
        metadata["solver_args"]=["--backend","mature","--seed","7"]
        if observed:metadata["solver_args"]+=['--uf-observers','--scoring','paws']
        metadata["source_sha256"]["UFNIA_LS/mature/nested/engine.hpp"]=digest(mature/"engine.hpp")
        metadata["binary_sha256"]["build/ufnia_ls/ufnia_mature"]=digest(binary)
        (run/"run.json").write_text(json.dumps(metadata))
        for path in (run/"cases").rglob("*.report.json"):
            query=json.loads(path.read_text());query.update(binding)
            query["core"].update(backend="mature_mpz",backend_version="mature-mpz-v1")
            if observed:
                query['core'].update(backend_version='mature-mpz-v2',uf_observers=True,
                    interaction='joint',scoring='paws',validation_scope='native_cnf_and_uf_congruence',
                    uf_applications=0,uf_conflicts=0)
            path.write_text(json.dumps(query))
        for path in (run/"cases").rglob("*.smt2.json"):
            report=json.loads(path.read_text());report.update(binding)
            work=Path(str(path)[:-5]+".work/run-fixture")
            report["last_query"]=json.loads(sorted(work.glob("*.report.json"))[-1].read_text())
            report["model_output_sha256"]=digest(Path(report["model_output"]))
            path.write_text(json.dumps(report))

    def test_mature_accepts_complete_binding_and_rejects_omitted_source_inventory(self):
        run=self.make_run("mature-run",{"family/a.smt2":"sat"});self.mature_evidence(run)
        process,result,_=self.audit(run,"mature-valid")
        self.assertEqual(process.returncode,0,process.stdout+process.stderr)
        self.assertEqual(result.get("backend"),"mature")
        self.assertEqual(result["verified_sat"],1)
        metadata=json.loads((run/"run.json").read_text());metadata["source_sha256"].pop("UFNIA_LS/mature/nested/engine.hpp")
        (run/"run.json").write_text(json.dumps(metadata))
        process,result,_=self.audit(run,"mature-missing-source")
        self.assertEqual(process.returncode,1)
        self.assertEqual(result["verified_sat"],0)

    def test_mature_rejects_wrong_engine_ignored_parameters_and_changed_model(self):
        for action in ("engine","options","model","binary"):
            with self.subTest(action=action):
                run=self.make_run("mature-"+action,{"family/a.smt2":"sat"});self.mature_evidence(run)
                report_path=run/"cases/family/a.smt2.json";report=json.loads(report_path.read_text())
                if action in ("engine","options"):
                    query_path=next(run.rglob("*.report.json"));query=json.loads(query_path.read_text())
                    if action=="engine":query["core"]["backend"]="ufnia_core"
                    else:query["requested_options"]["seed"]=99;query["effective_options"]["seed"]=99
                    query_path.write_text(json.dumps(query));report["last_query"]=query
                    report_path.write_text(json.dumps(report))
                elif action=="model":Path(report["model_output"]).write_text("; query 1\n(model (define-fun x () Int 1))\n")
                else:(self.solver/"build/ufnia_ls/ufnia_mature").unlink()
                process,result,_=self.audit(run,"reject-"+action)
                self.assertEqual(process.returncode,1)
                self.assertEqual(result["verified_sat"],0)

    def test_mature_observer_accepts_v2_even_with_no_applications(self):
        run=self.make_run('observed',{'family/a.smt2':'sat'});self.mature_evidence(run,observed=True)
        process,result,_=self.audit(run,'observed-valid')
        self.assertEqual(process.returncode,0,process.stdout+process.stderr)
        self.assertEqual(result['verified_sat'],1)
        self.assertEqual(result['backend_configuration'],'mature_uf_observed')
        self.assertEqual(result['effective_options']['scoring'],'paws')

    def test_mature_observer_rejects_masked_policies_version_scope_or_uf_conflicts(self):
        for action in ('masked','version','scope','conflicts','policy','flag'):
            with self.subTest(action=action):
                run=self.make_run('observed-'+action,{'family/a.smt2':'sat'});self.mature_evidence(run,observed=True)
                query_path=next(run.rglob('*.report.json'));query=json.loads(query_path.read_text())
                if action=='masked':query['effective_options'].update(interaction=None,scoring=None)
                elif action=='version':query['core']['backend_version']='mature-mpz-v1'
                elif action=='scope':query['core']['validation_scope']='native_cnf'
                elif action=='conflicts':query['core']['uf_conflicts']=1
                elif action=='policy':query['core']['scoring']='count'
                else:query['core']['uf_observers']=False
                query_path.write_text(json.dumps(query))
                report_path=run/'cases/family/a.smt2.json';report=json.loads(report_path.read_text())
                report['last_query']=query;report_path.write_text(json.dumps(report))
                process,result,_=self.audit(run,'reject-observed-'+action)
                self.assertEqual(process.returncode,1)
                self.assertEqual(result['verified_sat'],0)

    def test_node4_requires_explicit_matching_expected_host(self):
        run=self.make_run("node4-run",{"family/a.smt2":"sat"})
        self.set_host(run,"node4")
        process,audit,_=self.audit(run,"default-host")
        self.assertEqual(process.returncode,1)
        self.assertFalse(audit["eligible_for_acceptance"])
        process,audit,_=self.audit(run,"node4-host","--require-host","node4")
        self.assertEqual(process.returncode,0,process.stdout+process.stderr)
        self.assertTrue(audit["eligible_for_acceptance"])
        self.assertEqual(audit["verified_sat"],1)
        self.assertEqual(audit["expected_host"],"node4")
        process,audit,_=self.audit(run,"wrong-host","--require-host","node3")
        self.assertEqual(process.returncode,1)
        self.assertFalse(audit["eligible_for_acceptance"])

    def test_cross_host_comparison_accepts_same_snapshot_without_combining_sat(self):
        baseline=self.make_run("node3-baseline",{"family/common.smt2":"sat","family/baseline-only.smt2":"sat"})
        candidate=self.make_run("node4-candidate",{"family/common.smt2":"sat","family/candidate-only.smt2":"sat"})
        self.set_host(candidate,"node4")
        process,audit,_=self.audit(candidate,"cross-host","--require-host","node4",
                                   "--compare",str(baseline),"--compare-require-host","node3")
        self.assertEqual(process.returncode,0,process.stdout+process.stderr)
        self.assertTrue(audit["comparison"]["baseline_eligible"])
        self.assertEqual(audit["verified_sat"],2)
        self.assertEqual(audit["comparison"]["baseline_expected_host"],"node3")
        self.assertEqual(audit["comparison"]["new_sat"],["family/candidate-only.smt2"])
        self.assertEqual(audit["comparison"]["lost_sat"],["family/baseline-only.smt2"])


if __name__ == "__main__":unittest.main()
