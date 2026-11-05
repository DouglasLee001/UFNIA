"""Publish only audited final SMT2; all snapshots and solver artifacts stay temporary."""
import hashlib
import json
from pathlib import Path
import z3
from common.smt_tools import audit_formula,extract_queries

def normalized_fingerprint(text):
    queries=extract_queries(text)
    if len(queries)!=1:raise ValueError("Exactly one complete query is required")
    roots=z3.parse_smt2_string(queries[0]["text"])
    canonical=json.dumps([root.sexpr() for root in roots],separators=(",",":"))
    return hashlib.sha256(canonical.encode()).hexdigest()

def publish(candidate,target,task,record,commit,repository):
    if record.get("premise_result")!="sat":
        raise ValueError("Application premise was not verified satisfiable")
    status=record.get("transformed_result")
    if status=="unsat" and record.get("original_result")=="unsat":
        return {"result":"excluded","reason":"actual_unsat","status":"unsat"}
    if status not in ("sat","unsat") or record.get("original_result")!=status:
        raise ValueError("Original and transformed queries need matching definite solver results")
    if status=="sat":
        for name in ("original_sql_replay","transformed_sql_replay"):
            if record.get(name,{}).get("status")!="pass":
                raise ValueError("Concrete SQL counterexample replay did not pass")
    queries=extract_queries(Path(candidate).read_text())
    if len(queries)!=1:raise ValueError("Expected one complete official solver call")
    query=queries[0]
    theory=audit_formula(query["text"])
    if theory["result"]!="pass" or not theory["research_eligible"]:
        raise ValueError("Strict QF_UFNIA/research audit failed: "+str(theory["errors"]+theory["research_eligibility_reasons"]))
    fingerprint=normalized_fingerprint(query["text"])
    target=Path(target)
    if target.exists():
        if normalized_fingerprint(target.read_text())!=fingerprint:
            raise FileExistsError("Refuse to overwrite a different existing instance: "+str(target))
        return {"result":"preserved_existing","instance":str(target),"normalized_sha256":fingerprint,"status":status}
    task_hash=hashlib.sha256(json.dumps(task,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    source=(repository+"@"+commit+"; "+task["task_id"]+"; "+task.get("classification","application_derived")+"; input-sha256="+task_hash).replace("|","/")
    header=(
        "; Complete native SQL counterexample query; only this final audited SMT2 is retained.\n"
        "; Label-domain relation: equisatisfiable. Portability rewrites: equivalent.\n"
        "; NULL, bag semantics and original constraints retained; exact rows per base table: "+str(record["row_count_per_table"])+".\n"
        "; Portable normalizations: "+json.dumps(query.get("normalizations",[]),sort_keys=True)+"\n"
        "(set-info :smt-lib-version 2.6)\n"
        "(set-info :source |"+source+"|)\n"
        '(set-info :license "CC-BY-NC-SA-4.0; exported-formula redistribution requires review")\n'
        '(set-info :category "'+("industrial" if task.get("classification")=="faithful_export" else "crafted")+'")\n'
        "(set-info :status "+status+")\n")
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open("x",encoding="utf-8") as writer:writer.write(header+query["text"])
    return {"result":"written","instance":str(target),"normalized_sha256":fingerprint,"status":status}
