# Benchmark Runner Implementation Plan

**Goal:** Deliver scripts/run.sh {solver}, default 180 concurrent solver processes, and reproducible results under project results/{solver}.

**Architecture:** A Bash launcher resolves its location and execs a Python standard-library runner. ThreadPoolExecutor supervises independent external solver process groups; the main thread writes CSV and JSON summaries.

**Tech Stack:** Bash, Python 3 stdlib, four locally built SMT solvers.

- [x] Inspect scripts/results, local instructions, and external input files; confirm 806 .smt2 inputs.
- [x] Define outputs, timeout/concurrency configuration, interruption and Java settings in docs/superpowers/specs/2026-10-10-benchmark-runner-design.md.
- [x] Write meaningful subprocess tests in scripts/tests/test_run.py; run `python3 -m unittest discover -s scripts/tests -p 'test_*.py' -v` and confirm missing-runner failures.
- [x] Create scripts/run.sh and scripts/run_benchmarks.py. Accept exactly one of yices2/cvc5/z3/smtinterpol. Validate environment values and input/executable paths before creating results.
- [x] Schedule at most JOBS inputs at a time; start process groups, enforce wall timeout, save stdout/stderr and write results.csv incrementally with benchmark/result/wall_time_seconds/exit_code fields. Preserve runs and a relative latest symlink.
- [x] Capture signals using an event, stop submission, kill active groups, flush rows and write final summary.json/run.json. Keep SMTInterpol JVM at one CPU with a configurable 256 MiB heap.
- [x] Run the subprocess tests until they pass; run `bash -n scripts/run.sh` and Python compilation checks.
- [x] Independently review code and fix material findings. Add targeted regression tests first for any new correctness failures.
- [x] Smoke-run all four solvers with BENCHMARK_DIR=competitors/tests JOBS=2 TIMEOUT=5 RESULTS_DIR=<temporary directory>; run LIMIT=2 on the real external benchmark with JOBS=2 and short timeouts. Validate CSV/JSON, default180 in metadata with a small run, and output paths.
- [x] Write scripts/README.md with exact commands, defaults, result layout and interpretation; update checklist with evidence and deliver commands without starting the full experiment.

Final evidence: 9 subprocess integration tests pass; four real solvers x 6 fixtures validated; default JOBS=180 metadata verified; real dataset discovery=806 with LIMIT=2 verified. Independent review checked bounded concurrency and process-group cleanup including SIGTERM and leftover descendants. SMTInterpol exit0 protocol errors are correctly classified as error. Validation runs are stored in temporary directories referenced by scripts/validation.json.
