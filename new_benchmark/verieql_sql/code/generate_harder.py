#!/usr/bin/env python3
"""Reproduce four selected larger SQL SAT tasks; --probe keeps exploration under /tmp."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode=True
CODE=Path(__file__).resolve().parent
sys.path[:0]=[str(CODE/"vendor"),str(CODE)]
import generate as native
from common.smt_tools import audit_formula

def materialize(case,bound):
    task=copy.deepcopy(case["task"])
    for column in case.get("key_domains",[]):
        task.setdefault("constraints",[]).append({"between":[{"value":column},1,bound]})
    return task

def is_tmp(path):
    path=Path(path).resolve()
    return path==Path("/tmp") or Path("/tmp") in path.parents

def stages(checks):
    found={entry["stage"]:entry for entry in checks}
    original=found.get("original_counterexample",{"result":"not_run"})
    transformed=found.get("transformed_counterexample",{"result":"not_run"})
    passed=all(item.get("result")=="pass" and item.get("check_result")=="sat"
               for item in (original,transformed))
    return {"result":"pass" if passed else "not_run",
        "original_counterexample":original,"transformed_counterexample":transformed,
        "premise":found.get("premise",{"result":"not_run"}),"checks":checks}

def identify_stage(assertions):
    z3=native.z3
    complete=any(z3.is_not(term) and z3.is_implies(term.arg(0)) for term in assertions)
    if not complete:return "premise","native"
    pending=list(assertions);seen=set();sorts=set();has_string=False
    while pending:
        term=pending.pop()
        if term.get_id() in seen:continue
        seen.add(term.get_id());sort=term.sort();sorts.add(str(sort))
        has_string|=sort.kind()==z3.Z3_SEQ_SORT
        pending.extend(term.children())
    return ("original_counterexample","String") if has_string else ("transformed_counterexample","free_label")

def worker(case,bound,output,proof,args):
    task=materialize(case,bound)
    observed={};checks=[]
    original_snapshot=native._generate_snapshot
    original_publish=native.publish
    actual_check=native.z3.Solver.check
    def checked(solver,*assumptions):
        started=time.monotonic()
        result=actual_check(solver,*assumptions)
        check_seconds=time.monotonic()-started
        assertions=list(solver.assertions())
        actual_assumptions=[]
        for item in assumptions:
            if isinstance(item,(list,tuple,native.z3.AstVector)):actual_assumptions.extend(item)
            else:actual_assumptions.append(item)
        stage,domain=identify_stage(assertions)
        entry={"stage":stage,"label_domain":domain,"check_result":str(result),
            "solver_seconds":check_seconds,"active_assertions":len(assertions),
            "assumptions":len(actual_assumptions),"result":"not_applicable"}
        checks.append(entry)
        if result==native.z3.sat:
            model=solver.model();validation_started=time.monotonic()
            failed=[]
            for index,term in enumerate(assertions+actual_assumptions):
                if isinstance(term,bool):term=native.z3.BoolVal(term,ctx=solver.ctx)
                if not native.z3.is_true(model.eval(term,model_completion=True)):failed.append(index)
            entry.update(result="fail" if failed else "pass",assertions_checked=len(assertions),
                assumptions_checked=len(actual_assumptions),all_active_assertions_true=not failed,
                failed_assertion_indices=failed,validation_seconds=time.monotonic()-validation_started)
            query=native.snapshot_text(solver,actual_assumptions)
            model_text=model.sexpr()+"\n"
            query_file=proof/(stage+".query.txt");model_file=proof/(stage+".model.txt")
            query_file.write_text(query);model_file.write_text(model_text)
            entry.update(query_file=str(query_file),model_file=str(model_file),
                query_sha256=hashlib.sha256(query.encode()).hexdigest(),
                model_sha256=hashlib.sha256(model_text.encode()).hexdigest())
            if failed:raise ValueError("Actual solver model fails active assertions at "+str(failed))
        elif result==native.z3.unknown:
            entry["reason_unknown"]=solver.reason_unknown()
        return result
    def observe(*params,**kwargs):
        record=original_snapshot(*params,**kwargs)
        for key in ("original_result","transformed_result","premise_result",
                    "original_seconds","transformed_seconds","original_reason_unknown",
                    "transformed_reason_unknown","original_sql_replay","transformed_sql_replay",
                    "paired_status_match","error","exclusion_reason"):
            if key in record:observed[key]=copy.deepcopy(record[key])
        return record
    def validated_publish(*params,**kwargs):
        if stages(checks)["result"]!="pass":
            raise ValueError("Both complete counterexample models must pass every active assertion")
        return original_publish(*params,**kwargs)
    def wall_timeout(signum,frame):
        raise TimeoutError("Hard SQL worker wall budget exceeded")
    start=time.monotonic();reports=[]
    previous_handler=signal.signal(signal.SIGALRM,wall_timeout)
    signal.setitimer(signal.ITIMER_REAL,max(1,args.wall_seconds-5))
    native._generate_snapshot=observe;native.publish=validated_publish;native.z3.Solver.check=checked
    try:
        reports=native.generate_selected([task],[bound],output,CODE/"harder-inputs.json",args.timeout_ms)
    except Exception as error:
        observed["error"]=type(error).__name__+": "+str(error)
    finally:
        native._generate_snapshot=original_snapshot;native.publish=original_publish;native.z3.Solver.check=actual_check
        signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,previous_handler)
    report=dict(observed,probe=case["case_id"],bound=bound,classification="application_derived",
        input=task,arithmetic_safety=case["arithmetic_safety"],model_validation=stages(checks),
        solver_timeout_ms=args.timeout_ms,wall_budget_seconds=args.wall_seconds,
        elapsed_seconds=time.monotonic()-start,
        peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        generator_report=reports,
        native_timing_note="Native original/transformed timings include the additional model validation; model_validation.checks lists check-only timing separately.",
        probe_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        default_generator_source_sha256=hashlib.sha256((CODE/"generate.py").read_bytes()).hexdigest())
    path=output/("verieql_"+task["task_id"]+"_rows"+str(bound)+".smt2")
    accepted=any(r.get("result") in ("written","preserved_existing") for r in reports) and report.get("transformed_result")=="sat" and report["model_validation"]["result"]=="pass"
    if accepted and path.exists():
        report["candidate"]=str(path);report["bytes"]=path.stat().st_size
        report["theory"]=audit_formula(path.read_text())
        report["sha256"]=hashlib.sha256(path.read_bytes()).hexdigest()
    target=proof/(case["case_id"]+"_rows"+str(bound)+".probe.json")
    target.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    print(json.dumps({"probe":case["case_id"],"bound":bound,"evidence":str(target),
        "original_result":report.get("original_result"),"transformed_result":report.get("transformed_result"),
        "model_validation":report["model_validation"]["result"],"bytes":report.get("bytes"),
        "elapsed_seconds":report["elapsed_seconds"]},sort_keys=True),flush=True)
    return 0 if accepted and path.exists() else 1

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case");parser.add_argument("--bound",type=int)
    parser.add_argument("--output",type=Path)
    parser.add_argument("--probe",action="store_true",help="Explicit exploration; output must be under /tmp")
    parser.add_argument("--timeout-ms",type=int,default=None)
    parser.add_argument("--wall-seconds",type=int,default=120)
    parser.add_argument("--list",action="store_true")
    parser.add_argument("--worker",action="store_true",help=argparse.SUPPRESS)
    parser.add_argument("--proof-dir",type=Path,help=argparse.SUPPRESS)
    args=parser.parse_args()
    config=json.loads((CODE/"harder-inputs.json").read_text())
    choices={case["case_id"]:case for case in config["cases"]}
    defaults=config["default_probes"]
    if args.list:
        print(json.dumps({"defaults":defaults,"available":[{"case":c["case_id"],"bounds":c["bounds"]} for c in choices.values()]},indent=2));return 0
    if not 6<=args.wall_seconds<=120:parser.error("--wall-seconds must be between 6 and 120")
    args.timeout_ms=args.timeout_ms if args.timeout_ms is not None else (10000 if args.probe else 30000)
    if args.case:
        if args.case not in choices:parser.error("--case must name one of "+", ".join(choices))
        if args.bound is None:
            matching=next((p for p in defaults if p["case_id"]==args.case),None)
            bound=matching["bound"] if matching and not args.probe else choices[args.case]["bounds"][0]
        else:bound=args.bound
        if not 1<=bound<=config["max_probe_bound"]:parser.error("Invalid row bound")
        if not args.probe and {"case_id":args.case,"bound":bound} not in defaults:
            parser.error("Nondefault exploration requires --probe and /tmp output")
        selected=[{"case_id":args.case,"bound":bound}]
    else:
        if args.probe or args.bound is not None:parser.error("Exploration requires --case")
        selected=defaults
    output=args.output.resolve() if args.output else (Path(tempfile.mkdtemp(prefix="ufnia-sql-harder-",dir="/tmp")) if args.probe else CODE.parent/"instance")
    if args.probe and not is_tmp(output):parser.error("Harder probe output is restricted to /tmp")
    output.mkdir(parents=True,exist_ok=True)
    proof=args.proof_dir.resolve() if args.proof_dir else (output if is_tmp(output) and args.worker else Path(tempfile.mkdtemp(prefix="ufnia-sql-hard-proof-",dir="/tmp")))
    if not is_tmp(proof):parser.error("All proof output is restricted to /tmp")
    proof.mkdir(parents=True,exist_ok=True)
    resource.setrlimit(resource.RLIMIT_AS,(4*1024**3,4*1024**3))
    if args.worker:
        if len(selected)!=1:parser.error("Worker requires one selected case")
        job=selected[0]
        return worker(choices[job["case_id"]],job["bound"],output,proof,args)
    summaries=[]
    env=dict(os.environ,PYTHONHASHSEED="0",PYTHONDONTWRITEBYTECODE="1",
        OPENBLAS_NUM_THREADS="1",OMP_NUM_THREADS="1",MKL_NUM_THREADS="1",NUMEXPR_NUM_THREADS="1")
    for job in selected:
        name=job["case_id"]+"_rows"+str(job["bound"]);private=proof/name;private.mkdir(exist_ok=True)
        argv=[sys.executable,"-B",str(Path(__file__).resolve()),"--worker",
            "--case",job["case_id"],"--bound",str(job["bound"]),"--output",str(output),
            "--proof-dir",str(private),"--timeout-ms",str(args.timeout_ms),
            "--wall-seconds",str(args.wall_seconds)]
        if args.probe:argv.append("--probe")
        log=private/"run.log"
        with log.open("w") as writer:
            try:result=subprocess.run(argv,env=env,stdout=writer,stderr=subprocess.STDOUT,timeout=args.wall_seconds);rc=result.returncode
            except subprocess.TimeoutExpired:rc="wall_timeout"
        source=private/(name+".probe.json")
        if source.exists():
            detail=json.loads(source.read_text())
            summary={"case":job["case_id"],"bound":job["bound"],"returncode":rc,
                "candidate":detail.get("candidate"),"evidence":str(source),
                "model_validation":detail.get("model_validation",{}).get("result")}
        else:summary={"case":job["case_id"],"bound":job["bound"],"returncode":rc,"error":"No completed evidence","log":str(log)}
        summaries.append(summary);print(json.dumps(summary,sort_keys=True),flush=True)
    (proof/"campaign.json").write_text(json.dumps(summaries,indent=2)+"\n")
    return 0 if all(r["returncode"]==0 for r in summaries) else 1

if __name__=="__main__":
    native.deterministic_process()
    raise SystemExit(main())
