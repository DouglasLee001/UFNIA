#!/usr/bin/env python3
"""Audit one complete Certora2024 run without invoking a solver.

Exit 0 means the retained evidence satisfies the 191-case/600-second protocol,
including a baseline below 130 SAT. Only summary.json's goal_met establishes
the 130-SAT goal. Exit 1 means evidence or protocol checks failed.

Current resource policy: node2/node3 only, positive jobs == workers <= 180,
with 180 preferred for new rounds. Existing 120-job rounds remain eligible.
Historical node1 reports retain their historical policy and are not current
comparison baselines for this entry point.
"""
import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sys
import tempfile

sys.dont_write_bytecode = True
import summarize_ufnia_experiment as evidence


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "result/certora2024"
CORPUS = Path("/data/shared/lbh/QF_UFNIA/sat/QF_UFNIA")
FAMILY = "20240410-certora"
SUBFAMILIES = ("lia", "lia-splits", "nia", "nia-splits")
HOSTS = ("node2", "node3")
MAX_JOBS = 180
PREFERRED_JOBS = 180
POLICY_ID = "certora2024-node2-node3-max180-v2"
TIMEOUT_SCOPE = "complete process tree: parse, preprocess, search, validate"
TERMINAL = ("sat", "unknown", "timeout")
EVIDENCE_FILES = ("run.json", "summary.json", "manifest.json", "results.csv")
DETAIL_FIELDS = ("benchmark", "subfamily", "reported_result", "verified_sat", "queries",
                 "wall_time_seconds", "benchmark_sha256", "model_sha256", "report_sha256", "reason")
SUMMARY_FIELDS = ("subfamily", "selected", "recorded_unique", "reported_sat", "verified_sat",
                  "rejected_sat", "unknown", "timeout", "other")


def valid_name(name):
    if not isinstance(name, str):
        return False
    path = Path(name)
    return (not path.is_absolute() and ".." not in path.parts and path.as_posix() == name
            and len(path.parts) >= 3 and path.parts[0] == FAMILY
            and path.parts[1] in SUBFAMILIES and path.suffix == ".smt2")


def load_protocol(manifest_path, hashes_path, corpus):
    manifest = json.loads(manifest_path.read_text())
    hashes = evidence.read_json(hashes_path)
    if (not isinstance(manifest, list) or len(manifest) != 191
            or not all(valid_name(name) for name in manifest) or len(set(manifest)) != 191):
        raise ValueError("protocol manifest must contain exactly 191 unique 20240410-certora paths")
    if set(hashes) != set(manifest) or not all(
            isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) for value in hashes.values()):
        raise ValueError("corpus_sha256 must bind exactly the protocol's 191 benchmark paths")
    discovered = {p.relative_to(corpus).as_posix() for p in (corpus / FAMILY).rglob("*.smt2") if p.is_file()}
    if discovered != set(manifest):
        raise ValueError("current target corpus coverage differs from the fixed 191-case manifest")
    for name in manifest:
        if evidence.sha256(evidence.contained_file(corpus, name)) != hashes[name]:
            raise ValueError("fixed original input checksum mismatch: " + name)
    return {"manifest": manifest, "hashes": hashes, "corpus": corpus,
            "paths": {"manifest": str(manifest_path), "corpus_sha256": str(hashes_path), "corpus": str(corpus)},
            "sha256": {"manifest": evidence.sha256(manifest_path), "corpus_sha256": evidence.sha256(hashes_path)}}


def native_configuration(native):
    # Only configuration fields enter this signature; search statistics vary by
    # input. Include future feature flags/policies with the established suffixes.
    names = {"backend_version", "interaction", "scoring", "auxiliary_solvers", "elite_apply_seed",
             "polynomial_linking", "neighborhood_policy", "uf_search_policy"}
    names.update(name for name in native if name.endswith(("_enabled", "_policy", "_effective", "_configured"))
                 or name.startswith("guard_cells_"))
    return {name: native.get(name) for name in sorted(names)}


def empty_report(run):
    policy_file = PROTOCOL / "PROTOCOL.md"
    return {"run_dir": str(run), "generated_utc": datetime.now(timezone.utc).isoformat(),
            "eligible_for_comparison": False, "goal_met": False, "verified_sat": 0,
            "required": {"family": FAMILY, "cases": 191, "hostnames": list(HOSTS),
                         "max_jobs_per_host": MAX_JOBS, "preferred_jobs": PREFERRED_JOBS,
                         "worker_rule": "positive integer jobs == workers; actual peak <= jobs",
                         "policy_id": POLICY_ID, "protocol_path": str(policy_file),
                         "protocol_sha256": evidence.sha256(policy_file) if policy_file.is_file() else None,
                         "search_seconds": 600, "outer_seconds": 600, "original_validated_sat": 130},
            "issues": [], "accepted_benchmarks": [],
            "audit_method": "independent artifact/checksum/query/model-structure checks; no solver called or model evaluation repeated",
            "checker_sha256": evidence.sha256(Path(__file__)),
            "evidence_helper_sha256": evidence.sha256(Path(evidence.__file__))}


def summarize_groups(manifest, rows, accepted):
    summaries = []
    for family in (*SUBFAMILIES, "total"):
        members = set(manifest) if family == "total" else {n for n in manifest if n.split("/")[1] == family}
        selected_rows = rows if family == "total" else [r for r in rows if r.get("benchmark") in members]
        reported_sat = {r.get("benchmark") for r in selected_rows if r.get("result") == "sat"}
        summaries.append({"subfamily": family, "selected": len(members),
                          "recorded_unique": len({r.get("benchmark") for r in selected_rows} & members),
                          "reported_sat": len(reported_sat), "verified_sat": len(members & accepted),
                          "rejected_sat": len(reported_sat - accepted),
                          "unknown": sum(r.get("result") == "unknown" for r in selected_rows),
                          "timeout": sum(r.get("result") == "timeout" for r in selected_rows),
                          "other": sum(r.get("result") not in TERMINAL for r in selected_rows)})
    return summaries


def audit_run(directory, protocol):
    run = directory.resolve()
    result = empty_report(run)
    issues, detailed, rows, accepted = result["issues"], [], [], set()
    manifest = protocol["manifest"]

    def require(condition, message):
        if not condition:
            issues.append(message)

    try:
        before = {name: evidence.sha256(evidence.contained_file(run, name)) for name in EVIDENCE_FILES}
        metadata = evidence.read_json(run / "run.json")
        summary = evidence.read_json(run / "summary.json")
        run_manifest = json.loads((run / "manifest.json").read_text())
        with (run / "results.csv").open(newline="") as stream:
            reader = csv.DictReader(stream)
            if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
                raise ValueError("CSV header contains missing or duplicate fields")
            rows = list(reader)
        names = Counter(row.get("benchmark", "") for row in rows)
        duplicates = {name for name, count in names.items() if count != 1}
        require(isinstance(run_manifest, list) and all(valid_name(name) for name in run_manifest)
                and len(run_manifest) == len(set(run_manifest)) == 191 and set(run_manifest) == set(manifest),
                "run manifest differs from the fixed complete 191-case manifest")
        require(len(rows) == 191 and len(names) == 191 and not duplicates and set(names) == set(manifest),
                "CSV must have exactly one terminal result for each of the fixed 191 cases")
        require(not duplicates, "duplicate CSV benchmarks: " + ", ".join(sorted(duplicates)))
        host = metadata.get("hostname")
        result.update(hostname=host, jobs=metadata.get("jobs"), workers=metadata.get("workers"),
                      actual_peak_concurrent_cases=summary.get("actual_peak_concurrent_cases"),
                      timeout_seconds=metadata.get("timeout_seconds"),
                      selected_unique=len(set(manifest)), csv_rows=len(rows), csv_unique_benchmarks=len(names),
                      protocol_paths=protocol["paths"], protocol_sha256=protocol["sha256"], evidence_sha256=before)
        require(host in HOSTS and summary.get("hostname") == host, "host must consistently establish node2 or node3 under the current policy")
        for obj, label in ((metadata, "run"), (summary, "summary")):
            require(type(obj.get("jobs")) is int and 1 <= obj["jobs"] <= MAX_JOBS,
                    label + " jobs must be a positive integer at most 180")
            require(type(obj.get("workers")) is int and 1 <= obj["workers"] <= MAX_JOBS
                    and obj["workers"] == obj.get("jobs"),
                    label + " workers must be a positive integer equal to jobs and at most 180")
            require(obj.get("timeout_seconds") == 600, label + " must establish the 600-second outer budget")
            require(obj.get("selected") == 191 and obj.get("status") == "finished",
                    label + " does not establish a finished full191 run")
            require(obj.get("exit_code") == 0, label + " must establish successful runner completion")
        peak = summary.get("actual_peak_concurrent_cases")
        require(metadata.get("jobs") == summary.get("jobs") and metadata.get("workers") == summary.get("workers"),
                "run and summary concurrency configuration must agree")
        require(type(peak) is int and type(metadata.get("jobs")) is int and 1 <= peak <= metadata["jobs"],
                "actual peak concurrency must be positive and at most the declared jobs")
        require(metadata.get("timeout_scope") == TIMEOUT_SCOPE, "timeout scope must include parsing, preprocessing, search and validation")
        require(summary.get("recorded") == 191 and summary.get("not_run") == 0,
                "summary must establish all 191 terminal records without unrun cases")
        require(bool(metadata.get("finished_utc")) and metadata.get("finished_utc") == summary.get("finished_utc"),
                "run and summary completion timestamps must agree")
        require(summary.get("tools_changed_during_run") is False and summary.get("eligible_for_comparison") is True
                and summary.get("infrastructure_errors") == 0, "summary reports tool drift or infrastructure failure")
        counts = dict(Counter(row.get("result", "") for row in rows))
        require(all(status in TERMINAL for status in counts), "only sat, unknown and timeout are permitted terminal results")
        require({k: v for k, v in summary.get("counts", {}).items() if v} == counts,
                "summary counts disagree with the CSV recount")
        result["counts"] = counts
        require(Path(metadata["benchmarks"]).resolve() == protocol["corpus"], "run benchmark root differs from the fixed corpus root")

        binding = None
        try:
            binding = evidence.backend_binding(metadata)
            requested = binding["requested_options"]
            require(binding["backend"] == "mature" and requested["uf_observers"] is True,
                    "protocol requires the observed mature SLS backend")
            require(requested["seconds"] == requested["preprocess_timeout"] == 600,
                    "search and preprocessing budgets must both be 600 seconds")
            require(type(requested["seed"]) is int and 0 <= requested["seed"] <= 2**32 - 1,
                    "seed must be an unsigned 32-bit integer")
            require(summary.get("backend") == binding["backend"]
                    and summary.get("backend_configuration") == binding["backend_configuration"],
                    "summary backend identity differs from the bound configuration")
            solver = Path(metadata["solver_root"]).resolve()
            snapshot_file = evidence.contained_file(solver, "snapshot.json")
            snapshot = evidence.read_json(snapshot_file)
            files = snapshot.get("files")
            if not isinstance(files, dict) or not files:
                raise ValueError("snapshot has no complete files checksum inventory")
            if snapshot.get("source_stable_during_copy") is not True:
                raise ValueError("snapshot does not establish stable frozen sources")
            if snapshot.get("files_sha256", files) != files:
                raise ValueError("snapshot checksum inventories disagree")
            if metadata.get("snapshot_sha256") != evidence.sha256(snapshot_file):
                raise ValueError("frozen snapshot manifest checksum mismatch")
            fingerprints = {}
            for label in ("source_sha256", "binary_sha256"):
                recorded = metadata.get(label)
                if not isinstance(recorded, dict) or not recorded:
                    raise ValueError("missing " + label)
                for name, expected in recorded.items():
                    if files.get(name) != expected or evidence.sha256(evidence.contained_file(solver, name)) != expected:
                        raise ValueError("frozen source/binary checksum mismatch: " + name)
                fingerprints.update(recorded)
            for name, expected in files.items():
                if evidence.sha256(evidence.contained_file(solver, name)) != expected:
                    raise ValueError("snapshot artifact checksum mismatch: " + name)
            runner = "scripts/run_ufnia_experiments.py"
            if not metadata.get("runner_sha256") or metadata["runner_sha256"] != fingerprints.get(runner) or files.get(runner) != metadata["runner_sha256"]:
                raise ValueError("runner checksum is not bound to the frozen snapshot")
            result.update(backend=binding["backend"], backend_configuration=binding["backend_configuration"],
                          requested_options=requested, effective_options=binding["effective_options"],
                          core_sha256=binding["core_sha256"], snapshot_sha256=metadata["snapshot_sha256"],
                          solver_root=str(solver), runner_sha256=metadata["runner_sha256"])
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
            binding = None
            issues.append("frozen backend provenance: " + str(error))

        configurations = set()
        for row in rows:
            name = row.get("benchmark", "")
            detail = {"benchmark": name, "subfamily": name.split("/")[1] if valid_name(name) else "invalid",
                      "reported_result": row.get("result", ""), "verified_sat": False, "queries": 0,
                      "wall_time_seconds": row.get("wall_time_seconds", ""),
                      "benchmark_sha256": row.get("benchmark_sha256", ""),
                      "model_sha256": "", "report_sha256": "", "reason": ""}
            try:
                if name not in protocol["hashes"] or name in duplicates or not valid_name(name):
                    raise ValueError("benchmark is outside the fixed manifest or duplicated")
                source = evidence.contained_file(protocol["corpus"], name)
                if row.get("benchmark_sha256") != protocol["hashes"][name] or evidence.sha256(source) != protocol["hashes"][name]:
                    raise ValueError("original benchmark checksum differs from corpus_sha256")
                if row.get("source_unchanged") != "True":
                    raise ValueError("original source stability evidence is missing")
                if row.get("result") not in TERMINAL:
                    raise ValueError("forbidden terminal result")
                elapsed = float(row.get("wall_time_seconds", ""))
                if not math.isfinite(elapsed) or elapsed < 0:
                    raise ValueError("invalid case wall time")
                if row["result"] in ("sat", "unknown") and elapsed >= 600:
                    raise ValueError("completed case exceeds the end-to-end 600-second budget")
                if row["result"] == "timeout" and elapsed < 600:
                    raise ValueError("timeout occurred before the declared 600-second case budget")
                if binding is None:
                    raise ValueError("frozen backend provenance is incomplete")
                if row.get("backend") != binding["backend"]:
                    raise ValueError("CSV backend differs from the single frozen configuration")
                work = run / ("cases/" + name + ".work")
                queries = []
                for path in sorted(work.glob("run-*/query-*.report.json")):
                    query = evidence.read_json(evidence.contained_file(run, path.relative_to(run).as_posix()))
                    evidence.validate_backend_record(query, binding)
                    native = query.get("core")
                    if isinstance(native, dict):
                        if native.get("auxiliary_solvers") != "disabled":
                            raise ValueError("auxiliary solvers must be disabled for every native search result")
                        configurations.add(json.dumps(native_configuration(native), sort_keys=True))
                    if query.get("status") == "sat":
                        if (query.get("original_model_validated") is not True or query.get("validated") is not True
                                or query.get("validation_scope") != "original_assertions" or not isinstance(native, dict)
                                or native.get("current_arithmetic_unsat") != 0 or type(native.get("current_arithmetic_unsat")) is not int
                                or native.get("current_uf_conflicts") != 0 or type(native.get("current_uf_conflicts")) is not int
                                or native.get("auxiliary_solvers") != "disabled"):
                            raise ValueError("SAT query lacks exact original/native current-state validation")
                    queries.append(query)
                if row["result"] == "sat":
                    count, model_sha, report_sha = evidence.verify_sat(run, source, name, row, binding)
                    detail.update(verified_sat=True, queries=count, model_sha256=model_sha, report_sha256=report_sha)
                    accepted.add(name)
                elif row["result"] == "unknown":
                    if row.get("exit_code") != "0" or row.get("signal") not in ("", "0"):
                        raise ValueError("unknown must have a successful completed driver")
                    if row.get("json_report") != "cases/" + name + ".json":
                        raise ValueError("unknown report path is not canonical")
                    report = evidence.read_json(evidence.contained_file(run, row["json_report"]))
                    evidence.validate_backend_record(report, binding)
                    expected = report.get("queries_processed")
                    statuses = [query.get("status") for query in queries]
                    if (Path(report.get("input", "")).resolve() != source or report.get("mode") != "solve"
                            or report.get("status") != "complete" or report.get("complete_solver_used") is not False
                            or type(expected) is not int or expected < 1 or len(queries) != expected
                            or report.get("queries_seen") != expected or report.get("session", {}).get("checks") != expected
                            or [query.get("index") for query in queries] != list(range(1, expected + 1))
                            or "unknown" not in statuses or any(status not in ("sat", "unknown") for status in statuses)
                            or report.get("counts", {}).get("sat", 0) != statuses.count("sat")
                            or report.get("counts", {}).get("unknown", 0) != statuses.count("unknown")
                            or report.get("counts", {}).get("errors", 0)):
                        raise ValueError("unknown lacks complete consistent query coverage")
                    detail["queries"] = len(queries)
            except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
                detail["reason"] = str(error)
                issues.append(name + ": " + str(error))
            detailed.append(detail)
        for name in sorted(set(manifest) - set(names)):
            detailed.append({"benchmark": name, "subfamily": name.split("/")[1], "reported_result": "not_run",
                             "verified_sat": False, "queries": 0, "wall_time_seconds": "", "benchmark_sha256": "",
                             "model_sha256": "", "report_sha256": "", "reason": "missing terminal CSV row"})
        require(len(configurations) <= 1, "native feature/policy configuration differs between cases in this single run")
        result["native_configurations"] = [json.loads(value) for value in sorted(configurations)]
        require(before == {name: evidence.sha256(evidence.contained_file(run, name)) for name in EVIDENCE_FILES},
                "run evidence changed during the audit")
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        issues.append("readable complete evidence: " + str(error))
    result.update(verified_sat=len(accepted), accepted_benchmarks=sorted(accepted), eligible_for_comparison=not issues)
    result["goal_met"] = result["eligible_for_comparison"] and len(accepted) >= 130
    return result, detailed, summarize_groups(manifest, rows, accepted)


def write_csv(path, rows, fields):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path, help="one complete full191 run directory")
    parser.add_argument("--output", type=Path, help="new directory; existing directories are never overwritten")
    parser.add_argument("--compare", type=Path, help="independently audited full191 baseline; scores are never unioned")
    parser.add_argument("--manifest", type=Path, default=PROTOCOL / "manifest191.json")
    parser.add_argument("--corpus-sha256", type=Path, default=PROTOCOL / "corpus_sha256.json")
    parser.add_argument("--corpus", type=Path, default=CORPUS)
    args = parser.parse_args(argv)
    try:
        if args.output:
            output = args.output.resolve()
            output.mkdir(parents=True, exist_ok=False)
        else:
            output = Path(tempfile.mkdtemp(prefix="certora2024-audit-", dir=args.run.resolve()))
    except OSError as error:
        print("output error: " + str(error), file=sys.stderr)
        return 2
    try:
        protocol = load_protocol(args.manifest.resolve(), args.corpus_sha256.resolve(), args.corpus.resolve())
        report, detailed, groups = audit_run(args.run, protocol)
        if args.compare:
            baseline, _, _ = audit_run(args.compare, protocol)
            accepted, previous = set(report["accepted_benchmarks"]), set(baseline["accepted_benchmarks"])
            report["comparison"] = {"baseline_run": baseline["run_dir"],
                                    "baseline_hostname": baseline.get("hostname"),
                                    "baseline_eligible": baseline["eligible_for_comparison"],
                                    "baseline_verified_sat": baseline["verified_sat"],
                                    "baseline_goal_met": baseline["goal_met"],
                                    "baseline_issues": baseline["issues"],
                                    "baseline_snapshot_sha256": baseline.get("snapshot_sha256"),
                                    "baseline_core_sha256": baseline.get("core_sha256"),
                                    "baseline_actual_peak_concurrent_cases": baseline.get("actual_peak_concurrent_cases"),
                                    "baseline_native_configurations": baseline.get("native_configurations"),
                                    "baseline_requested_options": baseline.get("requested_options"),
                                    "baseline_evidence_sha256": baseline.get("evidence_sha256"),
                                    "new_sat": sorted(accepted - previous), "lost_sat": sorted(previous - accepted),
                                    "retained_sat": len(accepted & previous),
                                    "scope": "separate full191 runs; scores are never unioned, including same-binary runs with different feature settings",
                                    "timing_limit": "different nodes, concurrency, toolchains or GMP linkage can affect timing; wall-time differences alone do not isolate algorithm effects"}
            if not baseline["eligible_for_comparison"]:
                report["issues"].append("comparison baseline failed its independent protocol/evidence audit")
                report.update(eligible_for_comparison=False, goal_met=False)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        report, detailed = empty_report(args.run.resolve()), []
        report["issues"].append("fixed protocol evidence: " + str(error))
        groups = summarize_groups([], [], set())
    report["subfamilies"] = groups
    write_csv(output / "summary.csv", groups, SUMMARY_FIELDS)
    write_csv(output / "detailed.csv", detailed, DETAIL_FIELDS)
    (output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"output": str(output), "eligible_for_comparison": report["eligible_for_comparison"],
                      "goal_met": report["goal_met"], "verified_sat": report["verified_sat"], "issues": report["issues"]}, ensure_ascii=False))
    return 0 if report["eligible_for_comparison"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
