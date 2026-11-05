#!/usr/bin/env python3
"""Export complete SQL inequivalence calls from unmodified fixed official VeriEQL."""
import argparse
import hashlib
import json
import logging
import os
from pathlib import Path
import resource
import sys
import time
import traceback
import tempfile

CODE=Path(__file__).resolve().parent
HERE=CODE.parent
BENCHROOT=HERE.parent
UPSTREAM=CODE/"upstream/src"
sys.dont_write_bytecode=True
sys.path[:0]=[str(CODE/"vendor"),str(UPSTREAM),str(CODE)]
import z3
from capture import snapshot_text
from transform import abstract_labels
from replay import replay
from publish import publish

COMMIT="3b99928bf976c11cb59c9db6a177adba21f75798"
REPOSITORY="https://github.com/VeriEQL/VeriEQL"
KNOWN_UNSAT=frozenset(json.loads((CODE/"known_unsat.json").read_text())["instance_ids"])

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def relative(path):
    try:return str(Path(path).resolve().relative_to(BENCHROOT.resolve()))
    except ValueError:return str(Path(path).resolve())

def dump(path,data):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    Path(path).write_text(json.dumps(data,indent=2,sort_keys=True)+"\n")

def database_projection(env,task):
    tables={}
    for name,table in env.base_databases.items():
        tables[name]=[]
        for row in table.tuples:
            columns=[]
            for attr in table.attributes:
                columns.append({"column":attr.name,
                    "value_function":str(attr.VALUE.name()),
                    "null_function":"NULL",
                    "label":str(attr.__STRING_SORT__.decl().name())})
            tables[name].append({"tuple":str(row.SORT.decl().name()),"columns":columns})
    return {"schema":task["schema"],"sql":task["sql"],"tables":tables}

def _generate_snapshot(task,bound,output,input_file,timeout_ms=60000):
    from compat import prepare_z3_api
    compatibility=prepare_z3_api()
    from environment import Environment
    from constants import DIALECT
    from logger import LOGGER
    try:LOGGER.setLevel(logging.ERROR)
    except AttributeError:pass
    instance_id="verieql_"+task["task_id"]+"_rows"+str(bound)
    original=output/"raw"/(instance_id+".original.smt2")
    candidate=output/"candidates"/(instance_id+".smt2")
    evidence=output/"validation"/(instance_id+".json")
    record={"compatibility":compatibility,"instance_id":instance_id,"task_id":task["task_id"],"row_count_per_table":bound}
    for p in [original.parent,candidate.parent,evidence.parent]:p.mkdir(parents=True,exist_ok=True)
    with Environment(generate_code=False,semantics="bag",dialect=DIALECT.MYSQL) as env:
        for name,attrs in task["schema"].items():
            env.create_database(attributes=attrs,bound_size=bound,name=name)
        env.add_constraints(task.get("constraints"))
        env.save_checkpoints()
        env.solver.set(timeout=timeout_ms,threads=1)
        real_check=env.solver.check
        calls=[]
        def check(*assumptions):
            nonlocal candidate
            query_index=len(calls);calls.append(query_index)
            if query_index:
                raise RuntimeError("Expected exactly one official check call; refuse overwritten exports")
            active=list(env.solver.assertions())
            original.write_text(snapshot_text(env.solver,assumptions))
            record.update({"original_raw_file":relative(original),
                "original_hash":digest(original),"query_index":query_index,
                "active_assertion_count":len(active),"assumption_count":len(assumptions),
                "property_polarity":"environment.compare adds Not(equivalence_formulas) immediately before check",
                "export_hook":"environment.py:951-952, after final Not(equivalence_formulas) is added",
                "original_result":"not_run","transformed_result":"not_run"})
            final=active[-1]
            if not z3.is_not(final) or not z3.is_implies(final.arg(0)):
                raise RuntimeError("Official final property is not the expected negated implication")
            premise=z3.Solver(ctx=env.solver.ctx)
            premise.set(timeout=timeout_ms,threads=1)
            premise.add(*active[:-1],final.arg(0).arg(0),*assumptions)
            record["premise_result"]=str(premise.check())
            projected=database_projection(env,task)
            start=time.monotonic()
            result=real_check(*assumptions)
            record["original_result"]=str(result)
            record["original_seconds"]=time.monotonic()-start
            if result==z3.sat:
                model=env.solver.model()
                (output/"raw"/(instance_id+".original.model")).write_text(model.sexpr()+"\n")
                record["original_sql_replay"]=replay({"replay_spec":projected},model)
            elif result==z3.unknown:
                record["original_reason_unknown"]=env.solver.reason_unknown()
            assertions,transformation=abstract_labels(active+list(assumptions))
            transformed=z3.Solver(ctx=env.solver.ctx)
            transformed.set(timeout=timeout_ms,threads=1)
            transformed.add(*assertions)
            if record["premise_result"]=="unsat":
                candidate.unlink(missing_ok=True)
                candidate.with_suffix(".task.json").unlink(missing_ok=True)
                candidate=output/"excluded/vacuous_premise"/(instance_id+".smt2")
                candidate.parent.mkdir(parents=True,exist_ok=True)
                record["exclusion_reason"]="unsatisfiable_application_premise"
            candidate.write_text(snapshot_text(transformed))
            record["transformation"]=transformation
            start=time.monotonic(); translated_result=transformed.check()
            record["transformed_result"]=str(translated_result)
            record["transformed_seconds"]=time.monotonic()-start
            if translated_result==z3.sat:
                transformed_model=transformed.model()
                (output/"raw"/(instance_id+".transformed.model")).write_text(transformed_model.sexpr()+"\n")
                record["transformed_sql_replay"]=replay({"replay_spec":projected},transformed_model)
            elif translated_result==z3.unknown:
                record["transformed_reason_unknown"]=transformed.reason_unknown()
            record["paired_status_match"]=str(result)==str(translated_result)
            if result in (z3.sat,z3.unsat) and translated_result in (z3.sat,z3.unsat) and result!=translated_result:
                raise RuntimeError("Original/transformed solver disagreement: quarantine required")
            task_hash=hashlib.sha256(json.dumps(task,sort_keys=True,separators=(",",":")).encode()).hexdigest()
            metadata={
                "instance_id":instance_id,"family":"verieql_sql",
                "classification":task.get("classification","application_derived"),
                "raw_file":relative(candidate),"original_raw_file":relative(original),
                "originalhash":digest(original),"repository":REPOSITORY,"source_commit":COMMIT,
                "source_files":task.get("source_files",["environment.py","verifiers/"]),
                "task_id":task["task_id"],"property":task.get("property","Equality of two SQL result bags"),
                "query_kind":"counterexample",
                "scope_description":"Each base table contains exactly "+str(bound)+" symbolic rows using its original schema and the official native type encoder. NULL and duplicates are preserved unless input constraints restrict them. UNSAT establishes only this finite row count.",
                "bounds":{"exact_base_rows_per_table":bound,"INT_columns_only_range":[-2147483648,2147483647]},
                "native_input_schema":task["schema"],
                "input_file":relative(input_file),"input_hash":task_hash,
                "input_hash_method":"sha256-json-sortkeys-compact-ensureascii-v1",
                "input_file_sha256":digest(input_file),
                "premise_result":record["premise_result"],
                "license":"CC-BY-NC-SA-4.0; exported-formula redistribution requires review",
                "uf_meaning":"Column getters TupleSort -> Int; NULL(row,label) is SQL nullness; DELETED(row) is tuple presence. Label equality is retained.",
                "integer_nonlinearity_meaning":task.get("nonlinearity_meaning","Symbolic integer column products appearing in the SQL select, join, filter or aggregate expressions"),
                "source_ids":task.get("source_ids",["VeriEQL-v1.0","kit-S12","kit-S13","kit-S14"]),
                "split_group":task.get("split_group","verieql/"+task["task_id"]),
                "uf_interaction":"weak_or_eliminated",
                "generator_command":["env","PYTHONHASHSEED=0","OPENBLAS_NUM_THREADS=1","OMP_NUM_THREADS=1","python3","new_benchmark/verieql_sql/code/generate.py","--tasks","new_benchmark/"+relative(input_file),"--task-id",task["task_id"],"--bounds",str(bound)],
                "generator_actual_argv":[sys.executable]+sys.argv,
                "generator_source_sha256":digest(__file__),
                "source_line":task.get("source_line"),
                "source_record_sha256":task.get("source_record_sha256"),
                "limitations":["Bounded exact row count, not arbitrary-size SQL equivalence",
                    "Finite ground getter UF interaction can be weak or eliminated by preprocessing",
                    "SQLite replay covers the documented shared integer/NULL/bag subset; no MySQL server was used",
                    "No official SMT-LIB full-library novelty claim"],
                "transformation_relation":"equisatisfiable",
                "transformations":[transformation],
                "transformation_evidence":str(CODE/"TRANSFORMATION.md"),
                "generation_evidence":relative(evidence),
                "replay_module":"verieql_sql/code/replay.py","replay_spec":projected,
                "original_result":str(result),"transformed_result":str(translated_result),
            }
            dump(candidate.with_suffix(".task.json"),metadata)
            dump(evidence,record)
            return result
        env.solver.check=check
        try:
            record["application_equivalent"]=env.analyze(*task["sql"])
            if len(calls)!=1:raise RuntimeError("No complete official solver check observed")
        except Exception as error:
            record.update({"error":type(error).__name__+": "+str(error),"traceback":traceback.format_exc()})
    dump(evidence,record)
    return record

def generate_selected(tasks,bounds,output,input_file,timeout_ms=60000):
    """Return in-memory reports; retain only final files in the requested directory."""
    reports=[]
    with tempfile.TemporaryDirectory(prefix="ufnia-verieql-",dir="/tmp") as transient:
        work=Path(transient)
        for task in tasks:
            for bound in bounds:
                instance_id="verieql_"+task["task_id"]+"_rows"+str(bound)
                if instance_id in KNOWN_UNSAT:
                    report={"instance_id":instance_id,"result":"excluded","reason":"known_unsat","status":"unsat"}
                    reports.append(report)
                    print(json.dumps(report,sort_keys=True),flush=True)
                    continue
                try:
                    record=_generate_snapshot(task,bound,work,input_file,timeout_ms)
                    if "error" in record:raise RuntimeError(record["error"])
                    if record.get("exclusion_reason"):
                        report={"instance_id":instance_id,"result":"excluded","reason":record["exclusion_reason"]}
                    else:
                        report=publish(work/"candidates"/(instance_id+".smt2"),
                            Path(output)/(instance_id+".smt2"),task,record,COMMIT,REPOSITORY)
                        report["instance_id"]=instance_id
                except Exception as error:
                    report={"instance_id":instance_id,"result":"error","error":type(error).__name__+": "+str(error)}
                reports.append(report)
                print(json.dumps(report,sort_keys=True),flush=True)
    return reports

def main(argv=None):
    parser=argparse.ArgumentParser(description="Generate only final strictly audited QF_UFNIA SMT2; temporary artifacts are deleted.")
    parser.add_argument("--tasks",type=Path,default=CODE/"inputs.json")
    parser.add_argument("--output",type=Path,default=HERE/"instance",help="Final SMT2 directory; default: ../instance")
    parser.add_argument("--bounds",default="2")
    parser.add_argument("--task-id",action="append")
    parser.add_argument("--limit",type=int)
    parser.add_argument("--timeout-ms",type=int,default=60000)
    args=parser.parse_args(argv)
    if args.output.resolve()==HERE.resolve():parser.error("--output must name an instance directory, not the family root")
    resource.setrlimit(resource.RLIMIT_AS,(4*1024**3,4*1024**3))
    tasks=json.loads(args.tasks.read_text())
    if args.task_id:tasks=[t for t in tasks if t["task_id"] in args.task_id]
    if args.limit is not None:tasks=tasks[:args.limit]
    reports=generate_selected(tasks,list(map(int,args.bounds.split(","))),args.output,args.tasks,args.timeout_ms)
    return 1 if any(r["result"]=="error" for r in reports) else 0

def deterministic_process():
    if os.environ.get("PYTHONHASHSEED")!="0":
        env=dict(os.environ,PYTHONHASHSEED="0",PYTHONDONTWRITEBYTECODE="1")
        os.execve(sys.executable,[sys.executable,"-B"]+sys.argv,env)

if __name__=="__main__":
    deterministic_process()
    raise SystemExit(main())
