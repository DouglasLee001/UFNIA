#!/usr/bin/env python3
"""Run frozen UFNIA SLS snapshots and count only original-model-validated SAT.

Example (run on node3):
  python3 scripts/run_ufnia_experiments.py --solver-root SNAPSHOT \
    --benchmarks /data/shared/lbh/QF_UFNIA/sat/QF_UFNIA --output RESULTS \
    --jobs 180 --timeout 600 --solver-args '["--seed", "0"]'

The outer timeout includes parsing, preprocessing, search and model validation.
Every invocation creates a new run directory. No existing result is replaced.
"""
import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time


RESULTS = ("sat", "unknown", "timeout", "error", "killed", "resource_error", "interrupted")
FIELDS = ("benchmark", "result", "wall_time_seconds", "exit_code", "signal",
          "validated", "original_model_validated", "queries", "query_statuses",
          "benchmark_sha256", "source_unchanged", "stdout", "stderr", "json_report",
          "model", "max_rss_kb", "max_vm_size_kb", "max_processes", "max_threads",
          "sampled_cpu_seconds", "oom_kill_delta", "error_message")
FIELDS += ("estimated_memory_kb", "admission_wait_seconds", "backend")

CORE_ONLY_OPTIONS = ("interaction", "scoring", "distance", "arithmetic_repair",
                     "active_arithmetic_rows", "search_policy", "search_phase", "candidate_policy")


def requested_configuration(solver_args, timeout):
    """Decode the exact driver argv, including last-option-wins semantics."""
    class OptionsParser(argparse.ArgumentParser):
        def error(self, message):
            raise ValueError("invalid solver arguments: " + message)
    parser = OptionsParser(add_help=False, allow_abbrev=False)
    options = {
        "backend": ("core", str, ("core", "mature")),
        "seconds": (600.0, float, None), "seed": (0, int, None),
        "preprocess_timeout": (120.0, float, None), "ackermann_limit": (1000000, int, None),
        "interaction": ("joint", str, ("none", "soft", "joint")),
        "scoring": ("count", str, ("count", "paws")), "cnf": ("on", str, ("on", "off")),
        "distance": ("classic", str, ("classic", "structural")),
        "arithmetic_repair": ("off", str, ("off", "linear", "quotient", "joint")),
        "active_arithmetic_rows": ("off", str, ("off", "on")),
        "post_lower_eqs": ("off", str, ("off", "on")),
        "search_policy": ("legacy", str, ("legacy", "greedy")),
        "search_phase": ("mixed", str, ("mixed", "alternating")),
        "candidate_policy": ("broad", str, ("broad", "critical")),
    }
    for name, (default, kind, choices) in options.items():
        parser.add_argument("--" + name.replace("_", "-"), default=default, type=kind, choices=choices)
    for name in ("uf_observers", "dump_candidate"):
        parser.add_argument("--" + name.replace("_", "-"), action="store_true")
    result = vars(parser.parse_args(["--seconds", str(timeout), *solver_args]))
    if any(not math.isfinite(result[key]) or not 0 <= result[key] <= 4294967 for key in ("seconds", "preprocess_timeout")):
        raise ValueError("solver time options are outside the supported range")
    if not 0 <= result["seed"] <= 4294967295 or result["ackermann_limit"] < 1:
        raise ValueError("solver seed or Ackermann limit is outside the supported range")
    return result


def effective_configuration(requested):
    effective = dict(requested)
    if requested["backend"] == "mature":
        effective.update({name: None for name in CORE_ONLY_OPTIONS})
        if requested["uf_observers"]:
            effective.update(interaction=requested["interaction"], scoring=requested["scoring"])
    return effective


def mature_source_files(root):
    return {path.relative_to(root).as_posix() for path in (root / "UFNIA_LS/mature").rglob("*")
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"}


def validate_backend_record(record, binding):
    fields = ("backend", "backend_configuration", "core_binary", "core_sha256", "requested_options", "effective_options")
    if binding["backend"] == "core" and not any(field in record for field in fields):
        if record.get("core", {}).get("backend") == "mature_mpz":
            raise ValueError("mature engine reported for the core backend")
        return  # Archived implicit-core reports predate backend evidence.
    for field in fields:
        actual, expected = record.get(field), binding[field]
        if field == "core_binary":
            actual = str(Path(actual).resolve()) if isinstance(actual, str) else actual
        if json.dumps(actual, sort_keys=True) != json.dumps(expected, sort_keys=True):
            raise ValueError(f"backend evidence mismatch: {field}")
    if binding["backend"] == "mature" and record.get("status") == "sat" and not isinstance(record.get("core"), dict):
        raise ValueError("mature SAT is missing native backend evidence")
    if binding["backend"] == "mature" and "core" in record:
        native = record["core"]
        observed = binding["requested_options"]["uf_observers"]
        version = "mature-mpz-v2" if observed else "mature-mpz-v1"
        if native.get("backend") != "mature_mpz" or native.get("backend_version") != version:
            raise ValueError("mature native engine/version evidence is missing or incorrect")
        if observed:
            if (native.get("uf_observers") is not True
                    or native.get("interaction") != binding["effective_options"]["interaction"]
                    or native.get("scoring") != binding["effective_options"]["scoring"]
                    or native.get("validation_scope") != "native_cnf_and_uf_congruence"
                    or type(native.get("uf_conflicts")) is not int or native["uf_conflicts"] < 0):
                raise ValueError("mature UF observer policy/validation evidence is missing or incorrect")
            if record.get("status") == "sat" and (native.get("status") != "sat"
                    or native.get("validated") is not True or native["uf_conflicts"] != 0):
                raise ValueError("mature UF SAT lacks validated zero-conflict evidence")


def memory_kb():
    values = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, _, value = line.partition(":")
        if key in ("MemTotal", "MemAvailable"):
            values[key] = int(value.split()[0])
    return values["MemTotal"], values["MemAvailable"]


def estimate_memory_kb(path, profiled_peak_kb, budget_kb):
    floor = 256 * 1024
    if profiled_peak_kb is not None:
        estimate = max(floor, math.ceil(profiled_peak_kb * 1.5))
    else:
        size = path.stat().st_size
        estimate = max(floor, math.ceil(size * 256 / 1024))
        if size >= 10 * 1024**2:
            estimate = max(estimate, 16 * 1024**2)
    return min(estimate, budget_kb)


class MemoryAdmission:
    """Reserve estimated tree memory before launch; never limit a solver heap."""
    def __init__(self, total_kb, reserve_kb, available, stop):
        self.budget_kb = total_kb - reserve_kb
        if self.budget_kb <= 0:
            raise ValueError("memory reserve must be smaller than host MemTotal")
        self.reserve_kb = reserve_kb
        self.available = available
        self.stop = stop
        self.condition = threading.Condition()
        self.reserved_kb = 0
        self.peak_reserved_kb = 0
        self.active = 0

    def acquire(self, estimate_kb):
        if not 0 < estimate_kb <= self.budget_kb:
            raise ValueError("case memory estimate exceeds the admission budget")
        with self.condition:
            while not self.stop.is_set():
                # A lone case can consume the whole capped budget. Requiring
                # MemAvailable >= reserve + its full estimate would deadlock
                # such cases because the OS itself also occupies memory.
                if (self.reserved_kb + estimate_kb <= self.budget_kb
                        and self.available() >= self.reserve_kb):
                    self.reserved_kb += estimate_kb
                    self.active += 1
                    self.peak_reserved_kb = max(self.peak_reserved_kb, self.reserved_kb)
                    return True
                self.condition.wait(timeout=.2)
        return False

    def release(self, estimate_kb):
        with self.condition:
            self.reserved_kb -= estimate_kb
            self.active -= 1
            self.condition.notify_all()

    def wake(self):
        with self.condition:
            self.condition.notify_all()


def memory_profile(path):
    peaks = {}
    if path is not None:
        with path.open(newline="") as stream:
            reader = csv.DictReader(stream)
            if not {"benchmark", "max_rss_kb"}.issubset(reader.fieldnames or ()):
                raise ValueError("memory profile CSV needs benchmark and max_rss_kb columns")
            for row in reader:
                if not row.get("max_rss_kb"):
                    continue
                peak = float(row["max_rss_kb"])
                if not math.isfinite(peak) or peak < 0:
                    raise ValueError("memory profile peaks must be finite and nonnegative")
                peaks[row["benchmark"]] = max(peak, peaks.get(row["benchmark"], 0))
    return peaks


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path, data):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def host_resources():
    result = {"load_average": os.getloadavg(), "cpu_count": os.cpu_count()}
    if hasattr(os, "sched_getaffinity"):
        result["cpu_affinity_count"] = len(os.sched_getaffinity(0))
    try:
        result["meminfo"] = Path("/proc/meminfo").read_text()
        result["oom_kill"] = next(int(line.split()[1]) for line in
                                  Path("/proc/vmstat").read_text().splitlines()
                                  if line.startswith("oom_kill "))
    except (OSError, StopIteration, ValueError):
        result["oom_kill"] = None
    return result


def oom_count():
    try:
        for line in Path("/proc/vmstat").read_text().splitlines():
            if line.startswith("oom_kill "):
                return int(line.split()[1])
    except (OSError, ValueError):
        pass
    return None


def sample_process_tree(pid, peaks):
    """Sample this process and descendants without scanning the whole host."""
    todo, seen = [pid], set()
    rss = vm = threads = 0
    cpu = 0.0
    ticks = os.sysconf("SC_CLK_TCK")
    while todo:
        current = todo.pop()
        if current in seen:
            continue
        seen.add(current)
        proc = Path(f"/proc/{current}")
        try:
            status = {}
            for line in (proc / "status").read_text().splitlines():
                key, _, value = line.partition(":")
                if key in ("VmRSS", "VmSize", "Threads"):
                    status[key] = int(value.split()[0])
            rss += status.get("VmRSS", 0)
            vm += status.get("VmSize", 0)
            threads += status.get("Threads", 0)
            stat = (proc / "stat").read_text().rsplit(")", 1)[1].split()
            cpu += (int(stat[11]) + int(stat[12])) / ticks
            todo.extend(int(p) for p in (proc / "task" / str(current) / "children").read_text().split())
        except (OSError, ValueError, IndexError):
            continue
    for key, value in (("max_rss_kb", rss), ("max_vm_size_kb", vm),
                       ("max_processes", len(seen)), ("max_threads", threads),
                       ("sampled_cpu_seconds", round(cpu, 6))):
        peaks[key] = max(peaks.get(key, 0), value)


def kill_group(process, gentle=False):
    if gentle:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=.25)
        except subprocess.TimeoutExpired:
            pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def query_results(report_path, work, binding=None, model_path=None, input_path=None):
    """Inspect every query report; stdout alone never establishes success."""
    report = json.loads(report_path.read_text())
    paths = sorted(work.glob("run-*/query-*.report.json"))
    queries = [json.loads(path.read_text()) for path in paths]
    if binding is not None:
        validate_backend_record(report, binding)
        for query in queries:
            validate_backend_record(query, binding)
        if binding["backend"] == "mature":
            if Path(report.get("input", "")).resolve() != input_path:
                raise ValueError("mature report belongs to another input")
            if any(query.get("status") == "sat" for query in queries) and report.get("model_output_written") is not True:
                raise ValueError("mature SAT has no published original model")
        if report.get("model_output_written") is True and (binding["backend"] == "mature" or "model_output_sha256" in report):
            if (model_path is None or not model_path.is_file()
                    or Path(report.get("model_output", "")).resolve() != model_path
                    or report.get("model_output_sha256") != sha256(model_path)):
                raise ValueError("published model path/checksum does not match its validation report")
    expected = report.get("queries_processed")
    if (report.get("mode") != "solve" or report.get("status") != "complete"
            or report.get("complete_solver_used") is not False
            or type(expected) is not int or expected < 1
            or len(queries) != expected or report.get("session", {}).get("checks") != expected
            or report.get("queries_seen") != expected
            or [q.get("index") for q in queries] != list(range(1, expected + 1))):
        raise ValueError("incomplete or inconsistent per-query reports")
    counts = report.get("counts", {})
    statuses = [q.get("status") for q in queries]
    if counts.get("errors", 0) or counts.get("sat") != statuses.count("sat"):
        raise ValueError("aggregate query counts disagree with per-query reports")
    for query in queries:
        if query.get("status") == "sat" and not (
                query.get("validated") is True
                and query.get("original_model_validated") is True):
            raise ValueError(f"query {query.get('index')} SAT has no validated original model")
    if any(status not in ("sat", "unknown") for status in statuses):
        raise ValueError("unexpected query status")
    return ("sat" if all(status == "sat" for status in statuses) else "unknown"), queries


def fingerprints(root):
    source, binary = {}, {}
    for directory in ("UFNIA_LS", "scripts"):
        for path in sorted((root / directory).rglob("*")):
            is_mature = path.is_relative_to(root / "UFNIA_LS/mature")
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc" and (is_mature or path.suffix in (".py", ".cpp", ".h") or path.name == "Makefile"):
                source[path.relative_to(root).as_posix()] = sha256(path)
    for path in sorted((root / "build/ufnia_ls").rglob("*")):
        if path.is_file():
            binary[path.relative_to(root).as_posix()] = sha256(path)
    return source, binary


def inputs_for(args):
    discovered = sorted(path for path in args.benchmarks.rglob("*.smt2") if path.is_file())
    if args.manifest:
        text = args.manifest.read_text()
        entries = json.loads(text) if text.lstrip().startswith("[") else [
            line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
        if not isinstance(entries, list) or not all(isinstance(x, str) for x in entries):
            raise ValueError("manifest must be a JSON array or newline list of relative paths")
        selected = []
        for entry in entries:
            path = (args.benchmarks / entry).resolve()
            if not path.is_relative_to(args.benchmarks) or not path.is_file() or path.suffix != ".smt2":
                raise ValueError(f"invalid manifest benchmark: {entry}")
            selected.append(path)
        if len(set(selected)) != len(selected):
            raise ValueError("manifest contains duplicate benchmarks")
    else:
        selected = discovered
    if args.limit:
        selected = selected[:args.limit]
    if not selected:
        raise ValueError("no .smt2 benchmarks selected")
    return discovered, selected


def arguments(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--solver-root", required=True, type=Path)
    parser.add_argument("--benchmarks", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path, help="parent for a newly created run directory")
    parser.add_argument("--jobs", type=int, default=180)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--require-host", default="node3")
    parser.add_argument("--solver-args", default="[]", help="JSON array of extra run.py arguments")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--manifest", type=Path, help="JSON array or newline list of relative input paths")
    parser.add_argument("--memory-profile", type=Path, help="prior results.csv; reserve 1.5 times each case's sampled peak")
    parser.add_argument("--memory-reserve-gb", type=float, default=24, help="host memory kept unreserved, in GiB (default: 24)")
    parser.add_argument("--order", choices=("path", "memory"), default="path", help="dispatch paths alphabetically or by estimated memory ascending")
    args = parser.parse_args(argv)
    actual_host = socket.gethostname()
    if actual_host != args.require_host:
        raise ValueError(f"host must be {args.require_host!r}, actual host is {actual_host!r}")
    if args.jobs < 1 or args.limit < 0 or not math.isfinite(args.timeout) or args.timeout <= 0:
        raise ValueError("jobs/timeout must be positive and limit must be nonnegative")
    if not math.isfinite(args.memory_reserve_gb) or args.memory_reserve_gb < 0:
        raise ValueError("memory reserve must be finite and nonnegative")
    if args.memory_profile:
        args.memory_profile = args.memory_profile.expanduser().resolve(strict=True)
    args.solver_args = json.loads(args.solver_args)
    if not isinstance(args.solver_args, list) or not all(isinstance(x, str) for x in args.solver_args):
        raise ValueError("--solver-args must be a JSON array of strings")
    reserved = {"--parse-only", "--query", "--json-report", "--model-output", "--keep-dir"}
    if any(value.split("=", 1)[0] in reserved for value in args.solver_args):
        raise ValueError("solver arguments cannot replace query selection, reports, model or work paths")
    args.requested_options = requested_configuration(args.solver_args, args.timeout)
    args.effective_options = effective_configuration(args.requested_options)
    args.backend = args.requested_options["backend"]
    for name in ("solver_root", "benchmarks", "output"):
        setattr(args, name, getattr(args, name).expanduser().resolve())
    if not (args.solver_root / "UFNIA_LS/run.py").is_file():
        raise ValueError("solver-root has no UFNIA_LS/run.py")
    args.core_binary = args.solver_root / "build/ufnia_ls" / ("ufnia_mature" if args.backend == "mature" else "ufnia_core")
    if not args.core_binary.is_file():
        raise ValueError(f"solver-root has no selected backend binary: {args.core_binary}")
    if args.backend == "mature" and (not os.access(args.core_binary, os.X_OK) or not mature_source_files(args.solver_root)):
        raise ValueError("mature requires its executable binary and complete UFNIA_LS/mature source tree")
    if not args.benchmarks.is_dir():
        raise ValueError("benchmarks must name a directory")
    return args


def run(args):
    discovered, inputs = inputs_for(args)
    stop = threading.Event()
    total_kb, _ = memory_kb()
    reserve_kb = math.ceil(args.memory_reserve_gb * 1024**2)
    admission = MemoryAdmission(total_kb, reserve_kb, lambda: memory_kb()[1], stop)
    profile = memory_profile(args.memory_profile)
    estimates = {path: estimate_memory_kb(path, profile.get(path.relative_to(args.benchmarks).as_posix()),
                                        admission.budget_kb) for path in inputs}
    inputs = sorted(inputs, key=lambda path: (estimates[path], path) if args.order == "memory" else (path,))
    source_sha, binary_sha = fingerprints(args.solver_root)
    core_relative = args.core_binary.relative_to(args.solver_root).as_posix()
    configuration = ("mature_uf_observed" if args.requested_options["uf_observers"] else "mature_intrinsic") if args.backend == "mature" else "ufnia_core"
    binding = {"backend": args.backend, "backend_configuration": configuration,
               "core_binary": str(args.core_binary.resolve()), "core_sha256": binary_sha[core_relative],
               "requested_options": args.requested_options, "effective_options": args.effective_options}
    snapshot = args.solver_root / "snapshot.json"
    if snapshot.exists():
        snapshot_files = json.loads(snapshot.read_text()).get("files", {})
        if args.backend == "mature" and not (mature_source_files(args.solver_root) | {core_relative}).issubset(snapshot_files):
            raise ValueError("snapshot manifest omits mature backend source or binary assets")
        for name, expected in snapshot_files.items():
            path = args.solver_root / name
            if not path.is_file() or sha256(path) != expected:
                raise ValueError(f"snapshot checksum mismatch: {name}")
    args.output.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%SZ-"), dir=args.output))
    workers = min(args.jobs, len(inputs))
    metadata = {"hostname": socket.gethostname(), "started_utc": datetime.now(timezone.utc).isoformat(),
                "solver_root": str(args.solver_root), "benchmarks": str(args.benchmarks),
                "jobs": args.jobs, "workers": workers, "timeout_seconds": args.timeout,
                "timeout_scope": "complete process tree: parse, preprocess, search, validate",
                "solver_args": args.solver_args, "discovered": len(discovered), "selected": len(inputs),
                **binding,
                "source_sha256": source_sha, "binary_sha256": binary_sha,
                "snapshot_sha256": sha256(snapshot) if snapshot.exists() else None,
                "runner_sha256": sha256(Path(__file__)), "resources_before": host_resources(),
                "resource_sampling_interval_seconds": .1,
                "resource_sampling_note": "sampled process-tree peaks; OOM kill deltas are host-wide, not attributed to a single case",
                "memory_admission": {"budget_kb": admission.budget_kb, "reserve_kb": reserve_kb,
                                     "profile": str(args.memory_profile) if args.memory_profile else None,
                                     "profile_sha256": sha256(args.memory_profile) if args.memory_profile else None,
                                     "profile_peak_multiplier": 1.5, "minimum_estimate_kb": 256 * 1024,
                                     "order": args.order},
                "status": "running"}
    atomic_json(run_dir / "run.json", metadata)
    atomic_json(run_dir / "manifest.json", [p.relative_to(args.benchmarks).as_posix() for p in inputs])
    print(f"results={run_dir}\nhost={metadata['hostname']} selected={len(inputs)} jobs={args.jobs} workers={workers} timeout={args.timeout:g}s", flush=True)
    received = []
    concurrency_lock = threading.Lock()
    concurrency = {"active": 0, "peak": 0}

    def on_signal(signum, _frame):
        if not received:
            received.append(signum)
        stop.set()
        admission.wake()

    handlers = {sig: signal.signal(sig, on_signal) for sig in (signal.SIGINT, signal.SIGTERM)}
    environment = os.environ.copy()
    environment.update(OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1",
                       NUMEXPR_NUM_THREADS="1", PYTHONDONTWRITEBYTECODE="1")
    libraries = str(args.solver_root / "build/ufnia_ls/lib")
    environment["LD_LIBRARY_PATH"] = libraries + (os.pathsep + environment["LD_LIBRARY_PATH"] if environment.get("LD_LIBRARY_PATH") else "")

    def one(path):
        if stop.is_set():
            return None
        relative = path.relative_to(args.benchmarks).as_posix()
        base = run_dir / "cases" / relative
        paths = {"stdout": Path(str(base) + ".stdout"), "stderr": Path(str(base) + ".stderr"),
                 "json_report": Path(str(base) + ".json"), "model": Path(str(base) + ".model.smt2")}
        work = Path(str(base) + ".work")
        row = dict.fromkeys(FIELDS, "")
        row.update(benchmark=relative, result="error", validated=False,
                   original_model_validated=False, queries=0, error_message="", infrastructure_error=False, backend=args.backend)
        row.update({key: str(p.relative_to(run_dir)) for key, p in paths.items()})
        for key in ("max_rss_kb", "max_vm_size_kb", "max_processes", "max_threads", "sampled_cpu_seconds"):
            row[key] = 0
        row["estimated_memory_kb"] = estimates[path]
        waiting_since = time.monotonic()
        if not admission.acquire(estimates[path]):
            return None
        row["admission_wait_seconds"] = round(time.monotonic() - waiting_since, 6)
        started, before_oom, process = time.monotonic(), oom_count(), None
        registered = False
        try:
            base.parent.mkdir(parents=True, exist_ok=True)
            row["benchmark_sha256"] = sha256(path)
            command = [sys.executable, str(args.solver_root / "UFNIA_LS/run.py"), str(path),
                       "--seconds", str(args.timeout), *args.solver_args,
                       "--json-report", str(paths["json_report"]), "--model-output", str(paths["model"]),
                       "--keep-dir", str(work)]
            with paths["stdout"].open("wb") as out, paths["stderr"].open("wb") as err:
                process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                           env=environment, start_new_session=True, cwd=args.solver_root)
                with concurrency_lock:
                    concurrency["active"] += 1
                    concurrency["peak"] = max(concurrency["peak"], concurrency["active"])
                    registered = True
                while process.poll() is None:
                    sample_process_tree(process.pid, row)
                    if stop.is_set() or time.monotonic() - started >= args.timeout:
                        row["result"] = "interrupted" if stop.is_set() else "timeout"
                        kill_group(process, gentle=True)
                        break
                    try:
                        process.wait(timeout=min(.1, max(.001, args.timeout - (time.monotonic() - started))))
                    except subprocess.TimeoutExpired:
                        pass
                row["exit_code"] = process.returncode
            # wait()/poll() can observe completion after the deadline. Such a
            # result is never accepted as SAT, including during termination grace.
            if row["result"] not in ("timeout", "interrupted") and time.monotonic() - started >= args.timeout:
                row["result"] = "timeout"
            if process.returncode is not None and process.returncode < 0:
                row["signal"] = -process.returncode
            after_oom = oom_count()
            row["oom_kill_delta"] = max(0, after_oom - before_oom) if before_oom is not None and after_oom is not None else ""
            if row["result"] not in ("timeout", "interrupted"):
                if process.returncode != 0:
                    row["result"] = "killed" if process.returncode == -signal.SIGKILL else "error"
                    row["error_message"] = f"solver exited with {process.returncode}"
                else:
                    try:
                        result, queries = query_results(paths["json_report"], work, binding, paths["model"], path)
                        row.update(result=result, queries=len(queries), query_statuses=json.dumps([q["status"] for q in queries]))
                        row["validated"] = all(q.get("validated") is True for q in queries)
                        row["original_model_validated"] = all(q.get("original_model_validated") is True for q in queries)
                        if result == "sat" and not paths["model"].is_file():
                            raise ValueError("verified SAT model file is missing")
                        if result == "unknown" and row["oom_kill_delta"]:
                            row.update(result="resource_error", error_message="host OOM kill observed during this case; attribution is shared")
                    except (OSError, ValueError, TypeError, KeyError) as error:
                        row.update(result="error", error_message=f"invalid validation report: {error}")
            row["source_unchanged"] = sha256(path) == row["benchmark_sha256"]
            if not row["source_unchanged"]:
                row.update(result="error", error_message="benchmark changed during execution")
        except OSError as error:
            row.update(result="error", error_message=str(error), infrastructure_error=True)
        finally:
            if process is not None:
                kill_group(process)
            if registered:
                with concurrency_lock:
                    concurrency["active"] -= 1
            admission.release(estimates[path])
            # Retain validation evidence and explicitly requested UNKNOWN diagnostics.
            for artifact in work.rglob("*") if work.exists() else ():
                if artifact.is_file() and not artifact.name.endswith((".report.json", ".candidate.json", ".violations.json")):
                    try:
                        artifact.unlink()
                    except OSError:
                        pass
        row["wall_time_seconds"] = round(time.monotonic() - started, 6)
        if row["result"] == "sat" and row["wall_time_seconds"] >= args.timeout:
            row.update(result="timeout", error_message="complete case reached the wall timeout during finalization")
        return row

    counts = dict.fromkeys(RESULTS, 0)
    recorded = infrastructure_errors = 0
    failure = None
    started = time.monotonic()
    pool = ThreadPoolExecutor(max_workers=workers)
    pending, todo = set(), iter(inputs)
    try:
        with (run_dir / "results.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS, extrasaction="ignore")
            writer.writeheader()
            stream.flush()
            for _ in range(workers):
                path = next(todo, None)
                if path is not None and not stop.is_set():
                    pending.add(pool.submit(one, path))
            while pending:
                finished, pending = wait(pending, timeout=.2, return_when=FIRST_COMPLETED)
                for future in finished:
                    row = future.result()
                    if row is not None:
                        writer.writerow(row)
                        stream.flush()
                        os.fsync(stream.fileno())
                        counts[row["result"]] += 1
                        recorded += 1
                        if row["infrastructure_error"]:
                            infrastructure_errors += 1
                            stop.set()
                            admission.wake()
                        if recorded % 20 == 0 or recorded == len(inputs):
                            print(f"[{recorded}/{len(inputs)}] " + " ".join(f"{key}={value}" for key,value in counts.items()), flush=True)
                    if not stop.is_set():
                        path = next(todo, None)
                        if path is not None:
                            pending.add(pool.submit(one, path))
    except Exception as error:
        failure = f"{type(error).__name__}: {error}"
        stop.set()
        admission.wake()
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
    source_after, binary_after = fingerprints(args.solver_root)
    tools_changed = source_after != source_sha or binary_after != binary_sha
    exit_code = 128 + received[0] if received else (1 if failure or infrastructure_errors or tools_changed else 0)
    summary = {"hostname": socket.gethostname(), "status": "interrupted" if received else ("failed" if exit_code else "finished"),
               "backend": args.backend, "backend_configuration": binding["backend_configuration"],
               "selected": len(inputs), "recorded": recorded, "not_run": len(inputs) - recorded,
               "jobs": args.jobs, "workers": workers, "timeout_seconds": args.timeout,
               "actual_peak_concurrent_cases": concurrency["peak"],
               "peak_reserved_memory_kb": admission.peak_reserved_kb,
               "memory_budget_kb": admission.budget_kb,
               "counts": counts, "infrastructure_errors": infrastructure_errors,
               "tools_changed_during_run": tools_changed, "eligible_for_comparison": not tools_changed and exit_code == 0,
               "wall_time_seconds": round(time.monotonic() - started, 6), "exit_code": exit_code,
               "finished_utc": datetime.now(timezone.utc).isoformat(), "resources_after": host_resources()}
    if failure:
        summary["runner_error"] = failure
    atomic_json(run_dir / "summary.json", summary)
    metadata.update(status=summary["status"], finished_utc=summary["finished_utc"], exit_code=exit_code)
    atomic_json(run_dir / "run.json", metadata)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return exit_code


def main(argv=None):
    try:
        return run(arguments(argv))
    except (OSError, ValueError) as error:
        print(f"configuration error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
