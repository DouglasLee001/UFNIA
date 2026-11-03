#!/usr/bin/env python3
"""Independently audit a completed UFNIA experiment's retained evidence.

This checks artifacts and validation records; it does not call a solver or
repeat model evaluation. Self-reported SAT totals never establish acceptance.
An exit status of 1 means the audit found integrity or validation issues.
"""
import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import tempfile


def sha256(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def contained_file(root, relative):
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe artifact path: {relative}")
    result = (root / path).resolve()
    if not result.is_relative_to(root) or not result.is_file():
        raise ValueError(f"missing or external artifact: {relative}")
    return result


def read_json(path):
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


CORE_ONLY_OPTIONS = ("interaction", "scoring", "distance", "arithmetic_repair",
                     "active_arithmetic_rows", "search_policy", "search_phase", "candidate_policy")


def requested_options(metadata):
    """Independently reconstruct the driver's effective argv, not its claims."""
    class OptionsParser(argparse.ArgumentParser):
        def error(self, message):
            raise ValueError("invalid recorded solver arguments: " + message)
    parser = OptionsParser(add_help=False, allow_abbrev=False)
    defaults = {"backend":("core",str), "seconds":(600.0,float), "seed":(0,int),
                "preprocess_timeout":(120.0,float), "ackermann_limit":(1000000,int),
                "interaction":("joint",str), "scoring":("count",str), "cnf":("on",str),
                "distance":("classic",str), "arithmetic_repair":("off",str),
                "active_arithmetic_rows":("off",str), "post_lower_eqs":("off",str),
                "search_policy":("legacy",str), "search_phase":("mixed",str),
                "candidate_policy":("broad",str)}
    for name,(default,kind) in defaults.items():
        parser.add_argument("--"+name.replace("_","-"),default=default,type=kind)
    for name in ("uf_observers","dump_candidate"):
        parser.add_argument("--"+name.replace("_","-"),action="store_true")
    argv=metadata.get("solver_args",[])
    if not isinstance(argv,list) or not all(isinstance(value,str) for value in argv):
        raise ValueError("recorded solver_args must be an argument array")
    options=vars(parser.parse_args(["--seconds",str(metadata["timeout_seconds"]),*argv]))
    if options["backend"] not in ("core","mature"):
        raise ValueError("unknown recorded backend")
    return options


def validate_backend_record(record,binding):
    fields=("backend","backend_configuration","core_binary","core_sha256","requested_options","effective_options")
    if binding["backend"]=="core" and not any(field in record for field in fields):
        if record.get("core",{}).get("backend")=="mature_mpz":
            raise ValueError("mature engine cannot be accepted as the core backend")
        return  # Historical implicit-core reports are still auditable.
    for field in fields:
        actual=record.get(field)
        if field=="core_binary" and isinstance(actual,str):actual=str(Path(actual).resolve())
        if json.dumps(actual,sort_keys=True)!=json.dumps(binding[field],sort_keys=True):
            raise ValueError("backend evidence mismatch: "+field)
    if binding["backend"]=="mature" and "core" in record:
        native=record["core"]
        observed=binding["requested_options"]["uf_observers"]
        version="mature-mpz-v2" if observed else "mature-mpz-v1"
        if native.get("backend")!="mature_mpz" or native.get("backend_version")!=version:
            raise ValueError("mature native engine/version evidence mismatch")
        if observed:
            if (native.get("uf_observers") is not True
                    or native.get("interaction")!=binding["effective_options"]["interaction"]
                    or native.get("scoring")!=binding["effective_options"]["scoring"]
                    or native.get("validation_scope")!="native_cnf_and_uf_congruence"
                    or type(native.get("uf_conflicts")) is not int or native["uf_conflicts"]<0):
                raise ValueError("mature UF observer policy/validation evidence mismatch")
            if record.get("status")=="sat" and (native.get("status")!="sat"
                    or native.get("validated") is not True or native["uf_conflicts"]!=0):
                raise ValueError("mature UF SAT lacks validated zero-conflict evidence")


def backend_binding(metadata):
    options=requested_options(metadata);backend=options["backend"]
    solver=Path(metadata["solver_root"]).resolve()
    relative="build/ufnia_ls/"+("ufnia_mature" if backend=="mature" else "ufnia_core")
    binary=contained_file(solver,relative)
    expected=metadata.get("binary_sha256",{}).get(relative)
    if not expected or sha256(binary)!=expected:
        raise ValueError("selected backend binary missing from checksums or changed")
    effective=dict(options)
    if backend=="mature":
        effective.update({name:None for name in CORE_ONLY_OPTIONS})
        if options["uf_observers"]:effective.update(interaction=options["interaction"],scoring=options["scoring"])
        assets={path.relative_to(solver).as_posix() for path in (solver/"UFNIA_LS/mature").rglob("*")
                if path.is_file() and "__pycache__" not in path.parts and path.suffix!=".pyc"}
        if not assets or not assets.issubset(metadata.get("source_sha256",{})):
            raise ValueError("mature source tree is missing or its fingerprint inventory is incomplete")
        snapshot=solver/"snapshot.json"
        if snapshot.exists():
            snapshot_files=read_json(snapshot).get("files",{})
            if not (assets|{relative}).issubset(snapshot_files):
                raise ValueError("snapshot manifest omits mature source/binary assets")
            if metadata.get("snapshot_sha256") and sha256(snapshot)!=metadata["snapshot_sha256"]:
                raise ValueError("mature snapshot manifest checksum mismatch")
    configuration=("mature_uf_observed" if options["uf_observers"] else "mature_intrinsic") if backend=="mature" else "ufnia_core"
    binding={"backend":backend,"backend_configuration":configuration,
             "core_binary":str(binary),"core_sha256":expected,
             "requested_options":options,"effective_options":effective}
    validate_backend_record(metadata,binding)
    return binding


def model_count(text):
    """Check balanced SMT-LIB model expressions, ignoring comments/strings."""
    position, depth, count, expect_head = 0, 0, 0, False
    while position < len(text):
        char = text[position]
        if char.isspace():
            position += 1
        elif char == ";":
            position = text.find("\n", position)
            if position < 0:
                break
        elif char == "(":
            if expect_head:
                raise ValueError("model expression has no head")
            if depth == 0:
                expect_head = True
            depth += 1
            position += 1
        elif char == ")":
            if depth < 1 or expect_head:
                raise ValueError("unbalanced or empty model expression")
            depth -= 1
            position += 1
        elif char in ('"', '|'):
            if depth == 0 or expect_head:
                raise ValueError("unexpected string or quoted model head")
            quote = char
            position += 1
            while position < len(text):
                if text[position] == quote:
                    position += 1
                    if quote == '"' and position < len(text) and text[position] == '"':
                        position += 1
                        continue
                    break
                position += 1
            else:
                raise ValueError("unterminated model string or quoted symbol")
        else:
            end = position
            while end < len(text) and not text[end].isspace() and text[end] not in "();":
                end += 1
            token = text[position:end]
            if depth == 0:
                raise ValueError("unexpected token outside model")
            if expect_head:
                if token != "model":
                    raise ValueError("top-level model expression required")
                expect_head = False
                count += 1
            position = end
    if depth or expect_head or not count:
        raise ValueError("no complete SMT-LIB model expressions")
    return count


def verify_sat(run, source, benchmark, row, binding=None):
    if binding is None:binding=backend_binding(read_json(run/"run.json"))
    if row.get("backend") not in (None,"",binding["backend"]):
        raise ValueError("CSV backend differs from the requested backend")
    if (row.get("exit_code") != "0" or row.get("signal") not in ("", "0")
            or row.get("validated") != "True" or row.get("original_model_validated") != "True"
            or row.get("source_unchanged") != "True"):
        raise ValueError("CSV lacks successful original-model validation")
    expected_report = "cases/" + benchmark + ".json"
    expected_model = "cases/" + benchmark + ".model.smt2"
    if row.get("json_report") != expected_report or row.get("model") != expected_model:
        raise ValueError("CSV artifacts do not match this benchmark's canonical paths")
    report_path = contained_file(run, expected_report)
    model_path = contained_file(run, expected_model)
    report = read_json(report_path)
    validate_backend_record(report,binding)
    if (Path(report.get("input", "")).resolve() != source
            or report.get("mode") != "solve" or report.get("status") != "complete"
            or report.get("complete_solver_used") is not False
            or report.get("model_output_written") is not True
            or Path(report.get("model_output", "")).resolve() != model_path):
        raise ValueError("aggregate report has wrong input, mode, model or completion status")
    count = report.get("queries_processed")
    if (type(count) is not int or count < 1 or report.get("queries_seen") != count
            or report.get("session", {}).get("checks") != count):
        raise ValueError("incomplete aggregate query coverage")
    work = run / ("cases/" + benchmark + ".work")
    if not work.resolve().is_relative_to(run):
        raise ValueError("query work directory escapes run")
    evidence = sorted(work.glob("run-*/query-*.report.json"))
    if len(evidence) != count:
        raise ValueError("missing or extra per-query validation reports")
    queries = []
    for index, path in enumerate(evidence, 1):
        path = contained_file(run, str(path.relative_to(run)))
        query = read_json(path)
        validate_backend_record(query,binding)
        if (path.name != f"query-{index:06d}.report.json" or query.get("index") != index
                or query.get("status") != "sat" or query.get("validated") is not True
                or query.get("original_model_validated") is not True
                or query.get("validation_scope") != "original_assertions"
                or query.get("core", {}).get("status") != "sat"
                or query.get("core", {}).get("validated") is not True):
            raise ValueError(f"query {index} lacks complete original-model SAT evidence")
        queries.append(query)
    counts = report.get("counts", {})
    if (counts.get("sat") != count or counts.get("unknown", 0) or counts.get("errors", 0)
            or report.get("last_query") != queries[-1]):
        raise ValueError("aggregate counts/last query disagree with independent query evidence")
    text = model_path.read_text()
    if (binding["backend"]=="mature" or "model_output_sha256" in report) and report.get("model_output_sha256")!=sha256(model_path):
        raise ValueError("published model checksum differs from the validated model")
    if (model_count(text) != count or [int(n) for n in re.findall(r"(?m)^; query (\d+)\s*$", text)] != list(range(1,count+1))):
        raise ValueError("model file does not contain one complete model per query")
    return count, sha256(model_path), sha256(report_path)


def audit_run(directory, require_host="node3"):
    if require_host not in ("node3", "node4", "node5"):
        raise ValueError("expected host must be node3, node4 or node5")
    run = directory.resolve(strict=True)
    metadata = read_json(contained_file(run, "run.json"))
    summary = read_json(contained_file(run, "summary.json"))
    manifest = json.loads(contained_file(run, "manifest.json").read_text())
    if not isinstance(manifest, list) or not all(isinstance(x, str) for x in manifest):
        raise ValueError("manifest must be an array of benchmark paths")
    issues = []
    for name in manifest:
        path = Path(name)
        if path.is_absolute() or ".." in path.parts or path.as_posix() != name or not name.endswith(".smt2"):
            raise ValueError(f"invalid benchmark in manifest: {name}")
    if len(set(manifest)) != len(manifest):
        issues.append("manifest contains duplicate benchmarks")
    with contained_file(run, "results.csv").open(newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise ValueError("CSV header contains missing/duplicate fields")
        rows = list(reader)
    names = Counter(row.get("benchmark", "") for row in rows)
    duplicates = {name for name, count in names.items() if count != 1}
    if duplicates:
        issues.append("duplicate CSV benchmarks: " + ", ".join(sorted(duplicates)))
    if set(names) != set(manifest):
        issues.append("CSV coverage differs from manifest")
    for obj, label in ((metadata,"run"),(summary,"summary")):
        if obj.get("hostname") != require_host or obj.get("jobs") != 180 or obj.get("timeout_seconds") != 600:
            issues.append(f"{label} must establish {require_host}, 180 jobs and 600-second wall timeout")
        if obj.get("selected") != len(manifest) or obj.get("status") != "finished":
            issues.append(f"{label} selection/completion is inconsistent")
    if (summary.get("recorded") != len(rows) or summary.get("not_run") != 0
            or summary.get("exit_code") != 0 or summary.get("tools_changed_during_run") is not False
            or summary.get("eligible_for_comparison") is not True):
        issues.append("summary does not establish a complete unchanged run")
    reported = summary.get("counts", {})
    raw_counts = Counter(row.get("result", "") for row in rows)
    if {k:v for k,v in reported.items() if v} != dict(raw_counts):
        issues.append("self-reported counts disagree with CSV recount")
    solver = Path(metadata["solver_root"]).resolve()
    binding=None
    try:
        binding=backend_binding(metadata)
        if summary.get("backend") not in (None,binding["backend"]):
            raise ValueError("summary backend differs from the run backend")
    except (OSError,ValueError,TypeError,KeyError) as error:
        issues.append("backend provenance: "+str(error))
    for label in ("source_sha256", "binary_sha256"):
        fingerprints = metadata.get(label)
        if not isinstance(fingerprints,dict) or not fingerprints:
            issues.append(f"missing {label}")
            continue
        for name, expected in fingerprints.items():
            try:
                if sha256(contained_file(solver,name)) != expected:
                    issues.append(f"{label} mismatch: {name}")
            except (OSError,ValueError) as error:
                issues.append(f"{label}: {error}")
    benchmark_root = Path(metadata["benchmarks"]).resolve()
    detailed = []
    accepted = set()
    hashes = {}
    for row in rows:
        name = row.get("benchmark", "")
        detail = {"benchmark":name,"family":name.split("/")[0],"reported_result":row.get("result",""),
                  "verified_sat":False,"queries":0,"wall_time_seconds":row.get("wall_time_seconds",""),
                  "benchmark_sha256":row.get("benchmark_sha256",""),"model_sha256":"", "report_sha256":"", "reason":""}
        try:
            if binding is None:
                raise ValueError("selected backend provenance is incomplete")
            if name not in manifest or name in duplicates:
                raise ValueError("benchmark is absent from manifest or duplicated in CSV")
            source = contained_file(benchmark_root,name)
            if sha256(source) != row.get("benchmark_sha256"):
                raise ValueError("original benchmark checksum mismatch")
            hashes[name] = row["benchmark_sha256"]
            elapsed = float(row["wall_time_seconds"])
            if not math.isfinite(elapsed) or elapsed < 0:
                raise ValueError("invalid wall time")
            if row.get("result") == "sat":
                if elapsed >= metadata["timeout_seconds"]:
                    raise ValueError("SAT exceeds the complete-case wall timeout")
                count, model_sha, report_sha = verify_sat(run,source,name,row,binding)
                detail.update(verified_sat=True,queries=count,model_sha256=model_sha,report_sha256=report_sha)
                accepted.add(name)
            elif row.get("result")=="unknown" and binding["backend"]=="mature":
                report=read_json(contained_file(run,row["json_report"]))
                validate_backend_record(report,binding)
                for path in (run/("cases/"+name+".work")).glob("run-*/query-*.report.json"):
                    validate_backend_record(read_json(path),binding)
        except (OSError,ValueError,TypeError,KeyError,AttributeError) as error:
            detail["reason"] = str(error)
            issues.append(name + ": " + str(error))
        detailed.append(detail)
    for name in sorted(set(manifest)-set(names)):
        detailed.append({"benchmark":name,"family":name.split("/")[0],"reported_result":"not_run",
                         "verified_sat":False,"queries":0,"wall_time_seconds":"", "benchmark_sha256":"",
                         "model_sha256":"","report_sha256":"","reason":"missing CSV row"})
    families = []
    for family in sorted({name.split("/")[0] for name in manifest}):
        members = {name for name in manifest if name.split("/")[0] == family}
        subset = [row for row in detailed if row["benchmark"] in members]
        families.append({"family":family,"selected":len(members),"recorded_unique":len(members & set(names)),
                         "reported_sat_unique":len({r["benchmark"] for r in subset if r["reported_result"]=="sat"}),
                         "verified_sat":len(members & accepted),
                         "rejected_sat_unique":len({r["benchmark"] for r in subset if r["reported_result"]=="sat" and not r["verified_sat"]}),
                         "other_unique":len({r["benchmark"] for r in subset if r["reported_result"]!="sat"})})
    audit = {"run_dir":str(run),"generated_utc":datetime.now(timezone.utc).isoformat(),
             "expected_host":require_host,
             "backend":binding["backend"] if binding else metadata.get("backend","core"),
             "backend_configuration":binding["backend_configuration"] if binding else None,
             "requested_options":binding["requested_options"] if binding else None,
             "effective_options":binding["effective_options"] if binding else None,
             "selected_unique":len(set(manifest)),"csv_rows":len(rows),"csv_unique_benchmarks":len(names),
             "reported_counts_recounted":dict(raw_counts),"verified_sat":len(accepted),
             "eligible_for_acceptance":not issues,"issues":issues,
             "audit_method":"independent artifact/checksum/query/model-structure checks; no solver called or model evaluation repeated",
             "evidence_sha256":{name:sha256(run/name) for name in ("run.json","summary.json","manifest.json","results.csv")}}
    return audit,detailed,families,accepted,hashes


def write_csv(path,rows,fields):
    with path.open("w",newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run",type=Path)
    parser.add_argument("--output",type=Path,help="new directory; existing directories are never overwritten")
    parser.add_argument("--compare",type=Path,help="baseline run audited with the same independent checks")
    parser.add_argument("--require-host",choices=("node3","node4","node5"),default="node3",help="required candidate hostname (default: node3)")
    parser.add_argument("--compare-require-host",choices=("node3","node4","node5"),help="required baseline hostname; defaults to --require-host")
    args=parser.parse_args(argv)
    try:
        if args.compare_require_host and not args.compare:
            raise ValueError("--compare-require-host requires --compare")
        audit,detailed,families,accepted,hashes=audit_run(args.run,args.require_host)
        comparison=[]
        if args.compare:
            baseline_host=args.compare_require_host or args.require_host
            base,_,_,baseline,base_hashes=audit_run(args.compare,baseline_host)
            mismatches=sorted(name for name in hashes.keys() & base_hashes.keys() if hashes[name]!=base_hashes[name])
            if not base["eligible_for_acceptance"] or mismatches:
                audit["eligible_for_acceptance"]=False
                audit["issues"].append("baseline audit failed or common benchmark contents differ")
            new,lost=sorted(accepted-baseline),sorted(baseline-accepted)
            audit["comparison"]={"baseline_run":base["run_dir"],"baseline_verified_sat":len(baseline),
                                  "baseline_expected_host":baseline_host,
                                  "baseline_eligible":base["eligible_for_acceptance"],"new_sat":new,"lost_sat":lost,
                                  "retained_sat":len(accepted & baseline),"content_mismatches":mismatches}
            comparison=[{"benchmark":name,"change":"new_sat"} for name in new]+[{"benchmark":name,"change":"lost_sat"} for name in lost]
        if args.output:
            output=args.output.resolve()
            output.mkdir(parents=True,exist_ok=False)
        else:
            output=Path(tempfile.mkdtemp(prefix="audit-",dir=args.run.resolve()))
        write_csv(output/"detailed.csv",detailed,["benchmark","family","reported_result","verified_sat","queries",
                  "wall_time_seconds","benchmark_sha256","model_sha256","report_sha256","reason"])
        write_csv(output/"family_summary.csv",families,["family","selected","recorded_unique","reported_sat_unique",
                  "verified_sat","rejected_sat_unique","other_unique"])
        if args.compare:
            write_csv(output/"comparison.csv",comparison,["benchmark","change"])
        (output/"audit.json").write_text(json.dumps(audit,ensure_ascii=False,indent=2)+"\n")
        print(json.dumps({"output":str(output),**audit},ensure_ascii=False))
        return 0 if audit["eligible_for_acceptance"] else 1
    except (OSError,ValueError,TypeError,KeyError,AttributeError) as error:
        print(f"audit error: {error}",file=sys.stderr)
        return 2


if __name__=="__main__":sys.exit(main())
