"""File-backed evidence tests for the fixed 191-case Certora2024 protocol."""
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "summarize_certora2024.py"
SUBFAMILIES = (("lia", 61), ("lia-splits", 46), ("nia", 47), ("nia-splits", 37))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


class EntryPointTests(unittest.TestCase):
    def test_independent_protocol_entry_point_exists(self):
        self.assertTrue(SCRIPT.is_file(), "Certora2024 requires an independent auditor entry point")


@unittest.skipUnless(SCRIPT.is_file(), "entry point not implemented yet")
class Certora2024Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="certora2024-audit-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.corpus = self.root / "inputs"
        self.names = [f"20240410-certora/{family}/{i:04d}.smt2"
                      for family, count in SUBFAMILIES for i in range(count)]
        self.names.sort()
        for name in self.names:
            source = self.corpus / name
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text("(set-logic QF_UFNIA)\n(assert true)\n(check-sat)\n")
        self.manifest = self.root / "manifest191.json"
        self.hashes = self.root / "corpus_sha256.json"
        write_json(self.manifest, self.names)
        write_json(self.hashes, {name: digest(self.corpus / name) for name in self.names})
        self.solver = self.root / "solver"
        assets = {"UFNIA_LS/run.py": b"# frozen driver\n",
                  "UFNIA_LS/mature/engine.hpp": b"// frozen mature engine\n",
                  "scripts/run_ufnia_experiments.py": b"# frozen experiment runner\n",
                  "build/ufnia_ls/ufnia_mature": b"frozen mature binary"}
        for name, content in assets.items():
            path = self.solver / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        self.files = {name: digest(self.solver / name) for name in assets}
        write_json(self.solver / "snapshot.json", {"files": self.files, "files_sha256": self.files,
                    "source_stable_during_copy": True, "experiment_host": "node3", "jobs": 180})

    def make_run(self, name="run", sat_names=None, host="node2", jobs=180, workers=None, peak=None):
        # This is the existing mature fixture's retained-report structure, with
        # 191 original source files and genuine model/checksum/query linkage.
        run = self.root / name
        run.mkdir()
        sat_names = set(self.names[:61] if sat_names is None else sat_names)
        requested = {"backend": "mature", "seconds": 600.0, "seed": 0,
                     "preprocess_timeout": 600.0, "ackermann_limit": 1000000,
                     "interaction": "joint", "scoring": "paws", "cnf": "on",
                     "distance": "classic", "arithmetic_repair": "off",
                     "active_arithmetic_rows": "off", "post_lower_eqs": "off",
                     "search_policy": "legacy", "search_phase": "mixed",
                     "candidate_policy": "broad", "uf_observers": True, "dump_candidate": False}
        effective = dict(requested)
        effective.update({k: None for k in ("distance", "arithmetic_repair", "active_arithmetic_rows",
                                           "search_policy", "search_phase", "candidate_policy")})
        binding = {"backend": "mature", "backend_configuration": "mature_uf_observed",
                   "core_binary": str(self.solver / "build/ufnia_ls/ufnia_mature"),
                   "core_sha256": self.files["build/ufnia_ls/ufnia_mature"],
                   "requested_options": requested, "effective_options": effective}
        rows = []
        for benchmark in self.names:
            row = {"benchmark": benchmark, "result": "timeout", "exit_code": "-15", "signal": "15",
                   "wall_time_seconds": "600.1", "validated": "False", "original_model_validated": "False",
                   "source_unchanged": "True", "benchmark_sha256": digest(self.corpus / benchmark),
                   "json_report": f"cases/{benchmark}.json", "model": f"cases/{benchmark}.model.smt2",
                   "backend": "mature"}
            if benchmark in sat_names:
                base = run / "cases" / benchmark
                base.parent.mkdir(parents=True, exist_ok=True)
                work = Path(str(base) + ".work/run-fixture")
                work.mkdir(parents=True)
                native = {"backend": "mature_mpz", "backend_version": "mature-mpz-v2", "status": "sat",
                          "validated": True, "uf_observers": True, "interaction": "joint", "scoring": "paws",
                          "uf_conflicts": 0, "current_arithmetic_unsat": 0, "current_uf_conflicts": 0,
                          "validation_scope": "native_cnf_and_uf_congruence", "auxiliary_solvers": "disabled",
                          "uf_search_policy": "adaptive_tabu_v12"}
                query = {**binding, "index": 1, "status": "sat", "validated": True,
                         "original_model_validated": True, "validation_scope": "original_assertions", "core": native}
                write_json(work / "query-000001.report.json", query)
                model = Path(str(base) + ".model.smt2")
                model.write_text("; query 1\n(model)\n")
                report = {**binding, "input": str(self.corpus / benchmark), "mode": "solve", "status": "complete",
                          "queries_seen": 1, "queries_processed": 1, "session": {"checks": 1},
                          "counts": {"sat": 1, "unknown": 0, "errors": 0}, "last_query": query,
                          "complete_solver_used": False, "model_output_written": True,
                          "model_output": str(model), "model_output_sha256": digest(model)}
                write_json(Path(str(base) + ".json"), report)
                row.update(result="sat", exit_code="0", signal="", wall_time_seconds="0.5",
                           validated="True", original_model_validated="True")
            rows.append(row)
        workers = jobs if workers is None else workers
        peak = min(jobs, 93) if peak is None else peak
        metadata = {**binding, "hostname": host, "jobs": jobs, "workers": workers, "timeout_seconds": 600,
                    "timeout_scope": "complete process tree: parse, preprocess, search, validate",
                    "selected": 191, "discovered": 572, "status": "finished", "exit_code": 0,
                    "started_utc": "2026-10-31T10:00:00+00:00", "finished_utc": "2026-10-31T10:20:00+00:00",
                    "solver_root": str(self.solver), "benchmarks": str(self.corpus),
                    "source_sha256": {n: h for n, h in self.files.items() if not n.startswith("build/")},
                    "binary_sha256": {n: h for n, h in self.files.items() if n.startswith("build/")},
                    "snapshot_sha256": digest(self.solver / "snapshot.json"),
                    "runner_sha256": self.files["scripts/run_ufnia_experiments.py"],
                    "solver_args": ["--backend", "mature", "--uf-observers", "--interaction", "joint",
                                    "--scoring", "paws", "--seconds", "600", "--seed", "0",
                                    "--preprocess-timeout", "600"]}
        summary = {"hostname": host, "jobs": jobs, "workers": workers, "timeout_seconds": 600,
                   "selected": 191, "recorded": 191, "not_run": 0, "status": "finished", "exit_code": 0,
                   "finished_utc": metadata["finished_utc"], "actual_peak_concurrent_cases": peak,
                   "backend": "mature", "backend_configuration": "mature_uf_observed",
                   "infrastructure_errors": 0, "tools_changed_during_run": False, "eligible_for_comparison": True,
                   "counts": {"sat": len(sat_names), "timeout": 191 - len(sat_names)}}
        write_json(run / "run.json", metadata)
        write_json(run / "summary.json", summary)
        write_json(run / "manifest.json", self.names)
        self.write_rows(run, rows)
        return run

    def rows(self, run):
        with (run / "results.csv").open() as stream:
            return list(csv.DictReader(stream))

    def write_rows(self, run, rows):
        with (run / "results.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    def change_json(self, path, **updates):
        data = json.loads(path.read_text())
        data.update(updates)
        write_json(path, data)

    def audit(self, run, output="audit", compare=None):
        target = self.root / output
        argv = [sys.executable, "-B", str(SCRIPT), str(run), "--output", str(target),
                "--manifest", str(self.manifest), "--corpus-sha256", str(self.hashes), "--corpus", str(self.corpus)]
        if compare is not None:
            argv += ["--compare", str(compare)]
        process = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        self.assertIn(process.returncode, (0, 1), process.stdout + process.stderr)
        for filename in ("summary.csv", "detailed.csv", "summary.json"):
            self.assertTrue((target / filename).is_file(), process.stdout + process.stderr)
        return process, json.loads((target / "summary.json").read_text()), target

    def assert_rejected(self, run, output="audit"):
        process, report, target = self.audit(run, output)
        self.assertEqual(process.returncode, 1)
        self.assertFalse(report["eligible_for_comparison"])
        self.assertFalse(report["goal_met"])
        self.assertTrue(report["issues"])
        return report, target

    def test_legal_61_sat_baseline_is_comparable_below_goal(self):
        process, report, output = self.audit(self.make_run())
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertTrue(report["eligible_for_comparison"])
        self.assertFalse(report["goal_met"])
        self.assertEqual(report["verified_sat"], 61)
        with (output / "summary.csv").open() as stream:
            summaries = list(csv.DictReader(stream))
        self.assertEqual([r["subfamily"] for r in summaries], [n for n, _ in SUBFAMILIES] + ["total"])
        self.assertEqual([int(r["selected"]) for r in summaries], [61, 46, 47, 37, 191])
        with (output / "detailed.csv").open() as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 191)
        self.assertEqual(sum(r["verified_sat"] == "True" for r in rows), 61)

    def test_130_valid_sat_meets_goal_without_combining_baseline(self):
        baseline = self.make_run("baseline")
        candidate = self.make_run("candidate", self.names[60:190], host="node3")
        process, report, _ = self.audit(candidate, compare=baseline)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertTrue(report["goal_met"])
        self.assertEqual(report["verified_sat"], 130)
        self.assertEqual(report["comparison"]["baseline_verified_sat"], 61)
        self.assertEqual(len(report["comparison"]["new_sat"]), 129)
        self.assertEqual(len(report["comparison"]["lost_sat"]), 60)
        self.assertEqual(report["comparison"]["baseline_hostname"], "node2")
        self.assertEqual(report["hostname"], "node3")

    def test_current_node_policy_allows_180_and_120_on_node2_and_node3(self):
        for host in ("node2", "node3"):
            for jobs in (120, 180):
                with self.subTest(host=host, jobs=jobs):
                    run = self.make_run(host + "-" + str(jobs), host=host, jobs=jobs, peak=jobs)
                    process, report, _ = self.audit(run, "audit-" + run.name)
                    self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
                    self.assertTrue(report["eligible_for_comparison"])
                    self.assertEqual(report["required"]["hostnames"], ["node2", "node3"])
                    self.assertEqual(report["required"]["max_jobs_per_host"], 180)
                    self.assertEqual(report["required"]["preferred_jobs"], 180)
                    self.assertEqual(report["required"]["policy_id"], "certora2024-node2-node3-max180-v2")
                    self.assertTrue(Path(report["required"]["protocol_path"]).is_absolute())
                    self.assertTrue(report["required"]["protocol_path"].endswith("/result/certora2024/PROTOCOL.md"))

    def test_historical_node1_is_ineligible_under_current_node_policy(self):
        run = self.make_run(host="node1", jobs=120)
        report, _ = self.assert_rejected(run)
        self.assertEqual(report["verified_sat"], 61)

    def test_181_jobs_are_rejected_even_when_worker_and_peak_counts_match(self):
        run = self.make_run(jobs=181, workers=181, peak=181)
        self.assert_rejected(run)

    def test_positive_smaller_job_limit_is_valid_and_peak_cannot_exceed_it(self):
        run = self.make_run(jobs=1, workers=1, peak=1)
        process, report, _ = self.audit(run, "one-worker")
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertTrue(report["eligible_for_comparison"])
        self.change_json(run / "summary.json", actual_peak_concurrent_cases=2)
        self.assert_rejected(run, "peak-over-jobs")

    def test_129_valid_sat_does_not_meet_goal(self):
        _, report, _ = self.audit(self.make_run(sat_names=self.names[:129]))
        self.assertTrue(report["eligible_for_comparison"])
        self.assertFalse(report["goal_met"])

    def test_timeout_without_model_or_report_is_a_valid_terminal_result(self):
        run = self.make_run(sat_names=[])
        self.assertFalse((run / "cases").exists())
        process, report, output = self.audit(run)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertTrue(report["eligible_for_comparison"])
        self.assertEqual(report["counts"], {"timeout": 191})
        self.assertEqual(report["verified_sat"], 0)
        self.assertFalse(report["goal_met"])
        with (output / "detailed.csv").open() as stream:
            self.assertTrue(all(row["reason"] == "" for row in csv.DictReader(stream)))

    def test_timeout_at_30_seconds_cannot_be_relabelled_as_a_600_second_attempt(self):
        run = self.make_run(sat_names=[])
        rows = self.rows(run)
        rows[0]["wall_time_seconds"] = "30.1"
        self.write_rows(run, rows)
        self.assert_rejected(run)

    def convert_sat_to_unknown(self, run, auxiliary="disabled"):
        rows = self.rows(run)
        for row in rows:
            if row["result"] != "sat":
                continue
            name = row["benchmark"]
            query_path = next((run / ("cases/" + name + ".work")).rglob("*.report.json"))
            query = json.loads(query_path.read_text())
            query.update(status="unknown", validated=False, original_model_validated=False)
            query["core"].update(status="unknown", validated=False, auxiliary_solvers=auxiliary,
                                 current_arithmetic_unsat=1)
            write_json(query_path, query)
            aggregate = run / ("cases/" + name + ".json")
            self.change_json(aggregate, counts={"sat": 0, "unknown": 1, "errors": 0},
                             last_query=query, model_output_written=False)
            (run / ("cases/" + name + ".model.smt2")).unlink()
            row.update(result="unknown", validated="False", original_model_validated="False")
        self.write_rows(run, rows)
        self.change_json(run / "summary.json", counts={"unknown": 61, "timeout": 130})

    def test_unknown_without_published_model_is_valid_with_complete_query_evidence(self):
        run = self.make_run()
        self.convert_sat_to_unknown(run)
        process, report, _ = self.audit(run)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(report["counts"], {"unknown": 61, "timeout": 130})
        self.assertEqual(report["verified_sat"], 0)

    def test_auxiliary_solver_is_forbidden_even_for_unknown_results(self):
        run = self.make_run()
        self.convert_sat_to_unknown(run, auxiliary="enabled")
        self.assert_rejected(run)

    def test_existing_output_is_never_overwritten(self):
        run = self.make_run()
        _, _, output = self.audit(run)
        marker = output / "marker"
        marker.write_text("preserve")
        process = subprocess.run([sys.executable, "-B", str(SCRIPT), str(run), "--output", str(output)],
                                 capture_output=True, text=True, timeout=30)
        self.assertEqual(process.returncode, 2)
        self.assertEqual(marker.read_text(), "preserve")

    def test_missing_duplicate_and_foreign_family_cases_fail_closed(self):
        for action in ("missing", "duplicate", "foreign"):
            with self.subTest(action=action):
                run = self.make_run(action)
                rows = self.rows(run)
                if action == "missing":
                    rows.pop()
                elif action == "duplicate":
                    rows[-1] = dict(rows[0])
                else:
                    rows[-1]["benchmark"] = "20231115-certora/lia/0000.smt2"
                self.write_rows(run, rows)
                self.assert_rejected(run, "audit-" + action)

    def test_wrong_hosts_concurrency_and_incomplete_run_are_rejected(self):
        cases = [("run.json", "hostname", "node4"), ("summary.json", "hostname", "node5"),
                 ("run.json", "jobs", 181), ("summary.json", "jobs", 119),
                 ("summary.json", "actual_peak_concurrent_cases", 181),
                 ("run.json", "workers", 121), ("summary.json", "not_run", 1),
                 ("run.json", "status", "running"), ("summary.json", "infrastructure_errors", 1)]
        run = self.make_run()
        for i, (filename, field, value) in enumerate(cases):
            with self.subTest(field=field, value=value):
                path = run / filename
                before = path.read_text()
                self.change_json(path, **{field: value})
                self.assert_rejected(run, f"audit-{i}")
                path.write_text(before)

    def test_short_or_mixed_budgets_and_options_are_rejected(self):
        run = self.make_run()
        cases = [("timeout_seconds", 30), ("timeout_scope", "search only")]
        path = run / "run.json"
        before = path.read_text()
        for i, (field, value) in enumerate(cases):
            self.change_json(path, **{field: value})
            self.assert_rejected(run, f"budget-{i}")
            path.write_text(before)
        for option in ("--seconds", "--preprocess-timeout"):
            record = json.loads(before)
            record["solver_args"][record["solver_args"].index(option) + 1] = "30"
            write_json(path, record)
            self.assert_rejected(run, option[2:])
            path.write_text(before)
        query_path = next((run / "cases").rglob("*.report.json"))
        query = json.loads(query_path.read_text())
        query["requested_options"]["seed"] = 1
        query["effective_options"]["seed"] = 1
        write_json(query_path, query)
        self.assert_rejected(run, "mixed-seed")

    def test_unvalidated_missing_or_changed_models_do_not_count(self):
        for action in ("unvalidated", "missing-model", "changed-model", "partial-query", "late"):
            with self.subTest(action=action):
                run = self.make_run(action)
                first = self.names[0]
                if action == "unvalidated":
                    query = next((run / ("cases/" + first + ".work")).rglob("*.report.json"))
                    self.change_json(query, original_model_validated=False)
                elif action == "partial-query":
                    next((run / ("cases/" + first + ".work")).rglob("*.report.json")).unlink()
                elif action == "late":
                    rows = self.rows(run)
                    rows[0]["wall_time_seconds"] = "600"
                    self.write_rows(run, rows)
                else:
                    model = run / ("cases/" + first + ".model.smt2")
                    if action == "missing-model":
                        model.unlink()
                    else:
                        model.write_text("; query 1\n(model (define-fun x () Int 1))\n")
                report, _ = self.assert_rejected(run, "audit-" + action)
                self.assertEqual(report["verified_sat"], 60)

    def test_native_current_state_and_configuration_drift_are_rejected(self):
        for field, value in (("current_uf_conflicts", 1), ("current_arithmetic_unsat", 1),
                             ("auxiliary_solvers", "enabled"), ("uf_search_policy", "another_policy")):
            with self.subTest(field=field):
                run = self.make_run(field)
                first = self.names[0]
                query_path = next((run / ("cases/" + first + ".work")).rglob("*.report.json"))
                query = json.loads(query_path.read_text())
                query["core"][field] = value
                write_json(query_path, query)
                aggregate = run / ("cases/" + first + ".json")
                self.change_json(aggregate, last_query=query)
                self.assert_rejected(run, "audit-" + field)

    def test_configured_effective_and_guard_flags_cannot_vary_between_cases(self):
        for field in ("guard_cells_configured", "guard_cells_effective", "future_feature_configured",
                      "future_feature_effective", "guard_cells_mode"):
            with self.subTest(field=field):
                run = self.make_run(field)
                query_paths = sorted((run / "cases").rglob("*.report.json"))
                for i, path in enumerate(query_paths):
                    query = json.loads(path.read_text())
                    query["core"][field] = ("off" if i == 0 else "on") if field.endswith("mode") else i != 0
                    write_json(path, query)
                    benchmark = path.relative_to(run / "cases").as_posix().split(".work/", 1)[0]
                    self.change_json(run / ("cases/" + benchmark + ".json"), last_query=query)
                report, _ = self.assert_rejected(run, "audit-" + field)
                self.assertTrue(any("configuration differs" in issue for issue in report["issues"]))

    def test_input_snapshot_binary_and_runner_hash_drift_are_rejected(self):
        run = self.make_run()
        paths = [self.corpus / self.names[0], self.solver / "snapshot.json",
                 self.solver / "build/ufnia_ls/ufnia_mature", self.solver / "scripts/run_ufnia_experiments.py"]
        for i, path in enumerate(paths):
            with self.subTest(path=path):
                before = path.read_bytes()
                path.write_bytes(before + b"\nchanged\n")
                self.assert_rejected(run, f"drift-{i}")
                path.write_bytes(before)
        before = (run / "run.json").read_text()
        self.change_json(run / "run.json", runner_sha256="0" * 64)
        self.assert_rejected(run, "forged-runner")
        (run / "run.json").write_text(before)

    def test_fixed_manifest_hash_inventory_and_corpus_coverage_are_required(self):
        run = self.make_run()
        hashes = json.loads(self.hashes.read_text())
        before = self.hashes.read_text()
        hashes.pop(self.names[-1])
        write_json(self.hashes, hashes)
        self.assert_rejected(run, "missing-hash")
        self.hashes.write_text(before)
        manifest_before = self.manifest.read_text()
        write_json(self.manifest, self.names[:-1] + ["other-family/lia/a.smt2"])
        self.assert_rejected(run, "foreign-manifest")
        self.manifest.write_text(manifest_before)
        source = self.corpus / "20240410-certora/nia/extra.smt2"
        source.write_text("(check-sat)\n")
        self.assert_rejected(run, "extra-corpus")

    def test_forbidden_terminal_status_and_forged_count_are_rejected(self):
        run = self.make_run()
        rows = self.rows(run)
        rows[-1]["result"] = "unsat"
        self.write_rows(run, rows)
        self.assert_rejected(run, "unsat")
        rows[-1]["result"] = "error"
        self.write_rows(run, rows)
        self.assert_rejected(run, "error")
        rows[-1]["result"] = "timeout"
        self.write_rows(run, rows)
        self.change_json(run / "summary.json", counts={"sat": 130, "timeout": 61})
        self.assert_rejected(run, "forged-count")

    def test_ineligible_comparison_baseline_cannot_establish_goal(self):
        baseline = self.make_run("baseline")
        self.change_json(baseline / "run.json", jobs=181)
        candidate = self.make_run("candidate", self.names[:130], host="node3")
        process, report, _ = self.audit(candidate, compare=baseline)
        self.assertEqual(process.returncode, 1)
        self.assertFalse(report["comparison"]["baseline_eligible"])
        self.assertFalse(report["goal_met"])
        self.assertEqual(report["verified_sat"], 130)

    def test_malformed_json_still_emits_failure_report(self):
        run = self.make_run()
        (run / "summary.json").write_text("{")
        self.assert_rejected(run)

    def test_external_artifact_symlink_is_rejected(self):
        run = self.make_run()
        model = run / ("cases/" + self.names[0] + ".model.smt2")
        external = self.root / "external.smt2"
        external.write_bytes(model.read_bytes())
        model.unlink()
        model.symlink_to(external)
        self.assert_rejected(run)


if __name__ == "__main__":
    unittest.main()
