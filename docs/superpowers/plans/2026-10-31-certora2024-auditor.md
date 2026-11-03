# Certora2024 Auditor Implementation Plan

> **For agentic workers:** Execute the explicitly delegated design inline with test-driven development. Steps use checkbox syntax.

**Goal:** Independently audit a single complete frozen 191-case 600-second run under `result/certora2024/PROTOCOL.md`, preserving valid sub-130 baselines and reporting goal success only at 130 accepted original-model SAT. Current policy is `certora2024-node2-node3-max180-v2`: node2/node3 only, positive integer jobs=workers<=180, actual peak<=jobs, preferred new-round jobs180. The former node1/2/3 exact120 policy is historical.

**Architecture:** Create a separate entry point with fixed protocol defaults and explicit injectable manifest/hash/corpus paths for isolated fixture tests. Reuse only the legacy host-neutral `backend_binding`, `verify_sat`, path/checksum helpers; enforce this protocol independently. Produce summary.json, summary.csv and detailed.csv in a new output directory. No solver or model evaluation is invoked.

**Tech Stack:** Python standard library, unittest, existing artifact validators.

---

- [x] Create `scripts/tests/test_summarize_certora2024.py` with real file-backed mature-model fixtures derived from the existing auditor's evidence structure. Verify an explicit missing-entry-point assertion fails before production implementation.
- [x] Add `scripts/summarize_certora2024.py`. Check canonical191 manifest/hash agreement, target-family paths, source corpus coverage, current node2/3 authorization, positive integer jobs=workers<=180 and peak<=jobs, exact600 search/outer budgets, finished191 unique terminal results, snapshot/source/binary/runner stability, and bound query/model evidence. Reject invalid evidence without discarding the failure report.
- [x] Test a legal61-SAT baseline and 130-SAT success. Exercise missing/duplicate/foreign-family cases, mixed/short budgets, wrong host/concurrency, invalid SAT/model/query/native evidence, source/tool/snapshot drift, malformed files and forbidden result statuses. Every rejected artifact must leave goal_met false.
- [x] Add optional single-run baseline comparison after independent validation of each run; compare accepted sets without unioning scores. Keep node/config/hash provenance and four subfamily plus total rows.
- [x] Run `python3 -B -m unittest discover -s scripts/tests -p test_summarize_certora2024.py -v` to obtain RED then GREEN; run legacy summarizer tests to confirm helper reuse has no regressions. Inspect `--help` and diff; do not touch active baseline processes or old scripts.

CLI planned: `python3 -B scripts/summarize_certora2024.py RUN --output NEW_DIRECTORY [--compare BASELINE_RUN] [--manifest FILE --corpus-sha256 FILE --corpus DIRECTORY]`. Exit0 means the evidence is eligible for comparison, even below130; summary.json explicitly distinguishes eligible_for_comparison and goal_met. Exit1 means failed protocol/evidence checks. Existing output directories are never overwritten.

Completed evidence: initial entry-point RED and 15-test GREEN ran on server before the node-only instruction. Subsequent RED/GREEN ran on node2 and are saved under `result/certora2024/research/runner_audit`: `node2-red.log` catches auxiliary-enabled unknown evidence, `node2-short-timeout-red.log` catches a relabelled30-second timeout, and `node2-feature-identity-red.log` catches five configured/effective/guard-flag drift subcases. Final `node2-green.log` has21 passing tests (8.720s). `node2-legacy-regression.log` has15 passing existing auditor tests (1.409s). No solver or active baseline process was started or stopped. Official goal claims require root to use the default fixed protocol and real corpus; custom-source fixture results are test evidence only. Comparison reports retain both native feature configurations, core hashes and peak concurrency, and explicitly prohibit unioning same-binary on/off scores or attributing cross-node/linkage timing differences solely to the algorithm.

The evidence above records the historical policy at the time it ran. The current-policy change has a separate `node2-policy180-red.log`; its expected failures show that the old gate incorrectly refused180 jobs and accepted node1. Current fixtures default to node2/jobs180, comparisons use node2/node3, and focused tests cover both authorized nodes at120 and180, matching181 rejection, node1 rejection, positive smaller allocations and peak>jobs rejection. Full current-policy GREEN is `node2-policy180-green.log`:25 tests passed on node2 in10.223s. Existing120-job node2/node3 runs remain eligible. Historical node1 audits and every old result directory remain unchanged.

Current resource-schema example:

```json
{
  "required": {
    "hostnames": ["node2", "node3"],
    "max_jobs_per_host": 180,
    "preferred_jobs": 180,
    "worker_rule": "positive integer jobs == workers; actual peak <= jobs",
    "policy_id": "certora2024-node2-node3-max180-v2",
    "protocol_path": "/home/lbh/smt/ufnia/result/certora2024/PROTOCOL.md"
  }
}
```

The report also binds that protocol document's SHA-256 and retains the fixed manifest/input-hash paths and SHA-256 values. CLI usage remains `python3 -B scripts/summarize_certora2024.py RUN --output NEW_DIRECTORY [--compare FULL191_RUN_ON_NODE2_OR_NODE3]`; node1 cannot be used as a currently eligible comparison baseline.
