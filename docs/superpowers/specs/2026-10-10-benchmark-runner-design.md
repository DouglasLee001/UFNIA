# QF_UFNIA preliminary benchmark runner

The user explicitly requested implementation and launch commands. Implement a shell entry in scripts with one solver argument; use a Python standard-library executor to supervise at most 180 external solver processes by default. The alternative shell/xargs approach needs more helpers to collect trustworthy results and cancel subprocess trees, so use Python.

Default input: /data/shared/lbh/QF_UFNIA/non-incremental/QF_UFNIA (806 ordinary .smt2 files). Interpret /results/{solver} as the existing project-root results/{solver}. Recursively enumerate sorted SMT-LIB inputs and preserve their relative paths in logs. JOBS defaults to 180; TIMEOUT defaults to 600 seconds, as selected by the user on 2026-10-11, BENCHMARK_DIR and RESULTS_DIR may override roots, and LIMIT=0 means all inputs.

Each invocation creates results/<solver>/runs/<unique-id> and atomically updates a relative latest symlink. Preserve results.csv, run.json, summary.json, and per-input stdout/stderr. Record sat, unsat, unknown, error, timeout, interrupted, wall time and exit code; nonzero exit takes precedence over any printed answer. A normal solver unknown/error/timeout is an experimental outcome, while runner infrastructure failures return nonzero.

Use bounded task submission and process groups. Timeout/SIGINT/SIGTERM terminate child groups and save completed rows; stopped tasks are recorded as interrupted and unscheduled inputs remain unattempted. SMTInterpol gets a single visible CPU and SerialGC via JAVA_TOOL_OPTIONS; a configurable default 256 MiB maximum Java heap prevents the JVM from sizing itself against the whole machine for every worker.

Do not launch the full experiment. Verify real subprocess scheduling, classifications, timeout, duplicate basenames, run preservation, bad configuration and interruption with small fixtures, then run all four built solvers against shared six-case samples and a tiny real benchmark sample into temporary validation directories. Provide commands to the user.
