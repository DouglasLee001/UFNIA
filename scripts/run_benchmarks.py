#!/usr/bin/env python3
"""Run independent SMT-LIB inputs with bounded solver-process concurrency."""

import csv
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parent.parent
BENCHMARK_DEFAULT = "/data/shared/lbh/QF_UFNIA/non-incremental/QF_UFNIA"
ENTRIES = {"yices2": "yices-smt2", "cvc5": "cvc5", "z3": "z3", "smtinterpol": "smtinterpol"}
LOCAL_TIME = timezone(timedelta(hours=8))
FIELDS = ("benchmark", "result", "wall_time_seconds", "exit_code", "stdout", "stderr", "error_message")
RESULT_TYPES = ("sat", "unsat", "unknown", "timeout", "error", "interrupted")


def usage():
    return ("用法: ./run.sh {yices2|cvc5|z3|smtinterpol}\n"
            "环境变量: JOBS=180 TIMEOUT=600 LIMIT=0\n"
            f"  BENCHMARK_DIR={BENCHMARK_DEFAULT}\n"
            f"  RESULTS_DIR={ROOT / 'results'}\n"
            "  SMTINTERPOL_HEAP_MB=256 (每个 Java 进程最大堆)\n"
            "TIMEOUT 为每题秒数；LIMIT=0 运行全部；每次运行保留独立目录。")


def integer_option(name, default, minimum):
    value = int(os.environ.get(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} 必须 >= {minimum}")
    return value


def write_json(path, value):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def kill_group(process, gently=False):
    """Also kill descendants when the group leader has already exited."""
    if gently:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def smtinterpol_preflight(executable, environment):
    """Check the same Java environment before creating any experiment results."""
    command = [str(executable), "-version"]

    def on_sigterm(signum, _frame):
        raise SystemExit(128 + signum)

    previous_sigterm = signal.signal(signal.SIGTERM, on_sigterm)
    process = None
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, env=environment, start_new_session=True)
        try:
            stdout, stderr = process.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            raise ValueError("SMTInterpol 启动预检超时（15 秒）；请检查 Java 运行时和 JVM 参数")
    finally:
        try:
            if process is not None:
                kill_group(process)
                process.stdout.close()
                process.stderr.close()
        finally:
            signal.signal(signal.SIGTERM, previous_sigterm)
    output = stdout.decode(errors="replace").strip()
    error = stderr.decode(errors="replace").strip()
    if process.returncode != 0:
        diagnostic = error or output or "没有诊断输出"
        raise ValueError(f"SMTInterpol 启动预检失败（退出码 {process.returncode}）: {diagnostic}")
    return {"command": command, "stdout": output, "stderr": error,
            "java_tool_options": environment["JAVA_TOOL_OPTIONS"]}


def run_batch(solver, jobs, timeout, limit, benchmarks, results, executable, heap_mb):
    inputs = sorted(p for p in benchmarks.rglob("*.smt2") if p.is_file())
    if not inputs:
        raise ValueError(f"没有找到 .smt2 文件: {benchmarks}")
    discovered = len(inputs)
    if limit:
        inputs = inputs[:limit]
    workers = min(jobs, len(inputs))
    command = [str(executable)] + (["-q"] if solver == "smtinterpol" else [])
    environment = os.environ.copy()
    preflight = None
    if solver == "smtinterpol":
        java_options = f"-XX:ActiveProcessorCount=1 -XX:+UseSerialGC -Xms16m -Xmx{heap_mb}m"
        environment["JAVA_TOOL_OPTIONS"] = (environment.get("JAVA_TOOL_OPTIONS", "") + " " + java_options).strip()
        preflight = smtinterpol_preflight(executable, environment)
    solver_dir = results / solver
    runs_dir = solver_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(LOCAL_TIME)
    run_dir = Path(tempfile.mkdtemp(prefix=started_at.strftime("%Y%m%dT%H%M%S-"), dir=runs_dir))
    temporary_link = solver_dir / (".latest-" + run_dir.name)
    temporary_link.symlink_to(Path("runs") / run_dir.name, target_is_directory=True)
    temporary_link.replace(solver_dir / "latest")
    metadata = {"solver": solver, "solver_command": command, "benchmark_dir": str(benchmarks),
                "results_dir": str(run_dir), "jobs": jobs, "workers": workers,
                "timeout_seconds": timeout, "limit": limit, "discovered": discovered,
                "selected": len(inputs), "started_at": started_at.isoformat(), "status": "running"}
    if solver == "smtinterpol":
        metadata["smtinterpol_heap_mb"] = heap_mb
        metadata["smtinterpol_preflight"] = preflight
    release_file = ROOT / "competitors" / solver / "release.json"
    if release_file.is_file():
        metadata["solver_release"] = json.loads(release_file.read_text())
    write_json(run_dir / "run.json", metadata)
    print(f"solver={solver} inputs={len(inputs)} jobs={jobs} workers={workers} timeout={timeout:g}s", flush=True)
    print(f"results={run_dir}", flush=True)
    stop = threading.Event()
    received_signal = []

    def on_signal(signum, _frame):
        if not received_signal:
            received_signal.append(signum)
        stop.set()

    previous_handlers = {s: signal.signal(s, on_signal) for s in (signal.SIGINT, signal.SIGTERM)}

    def run_one(path):
        if stop.is_set():
            return None
        relative = path.relative_to(benchmarks).as_posix()
        stdout_path = run_dir / "logs" / (relative + ".stdout")
        stderr_path = run_dir / "logs" / (relative + ".stderr")
        row = {"benchmark": relative, "result": "error", "exit_code": "", "error_message": "",
               "stdout": stdout_path.relative_to(run_dir).as_posix(),
               "stderr": stderr_path.relative_to(run_dir).as_posix(), "infrastructure_error": False}
        process = None
        started = time.perf_counter()
        try:
            stdout_path.parent.mkdir(parents=True, exist_ok=True)
            with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
                process = subprocess.Popen(command + [str(path)], stdout=out, stderr=err,
                                           stdin=subprocess.DEVNULL, env=environment,
                                           start_new_session=True)
                while process.poll() is None:
                    if stop.is_set():
                        row["result"] = "interrupted"
                        kill_group(process, gently=True)
                        break
                    remaining = timeout - (time.perf_counter() - started)
                    if remaining <= 0:
                        row["result"] = "timeout"
                        kill_group(process, gently=True)
                        break
                    try:
                        process.wait(timeout=min(0.1, remaining))
                    except subprocess.TimeoutExpired:
                        pass
                row["exit_code"] = process.returncode
            if row["result"] not in ("timeout", "interrupted") and process.returncode == 0:
                answer = None
                with stdout_path.open(errors="replace") as output:
                    for line in output:
                        response = line.strip()
                        if re.match(r"^\(error(?:\s|\)|$)", response):
                            row["error_message"] = response
                            break
                        if answer is None and response in ("sat", "unsat", "unknown"):
                            answer = response
                if answer is not None and not row["error_message"]:
                    row["result"] = answer
        except OSError as error:
            row["infrastructure_error"] = True
            row["error_message"] = str(error)
        finally:
            if process is not None:
                kill_group(process)
        row["wall_time_seconds"] = round(time.perf_counter() - started, 6)
        return row

    counts = {name: 0 for name in RESULT_TYPES}
    recorded = 0
    infrastructure_errors = 0
    failure = None
    batch_started = time.perf_counter()
    pool = ThreadPoolExecutor(max_workers=workers)
    pending = set()
    todo = iter(inputs)
    try:
        with (run_dir / "results.csv").open("w", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=FIELDS, extrasaction="ignore")
            writer.writeheader()
            output.flush()
            for _ in range(workers):
                if stop.is_set():
                    break
                pending.add(pool.submit(run_one, next(todo)))
            while pending:
                finished, pending = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
                for future in finished:
                    row = future.result()
                    if row is not None:
                        writer.writerow(row)
                        output.flush()
                        counts[row["result"]] += 1
                        recorded += 1
                        if row["infrastructure_error"]:
                            infrastructure_errors += 1
                            stop.set()
                        if recorded % 20 == 0 or recorded == len(inputs):
                            print(f"[{recorded}/{len(inputs)}] " + " ".join(f"{k}={v}" for k, v in counts.items()), flush=True)
                    if not stop.is_set():
                        path = next(todo, None)
                        if path is not None:
                            pending.add(pool.submit(run_one, path))
    except Exception as error:
        failure = str(error)
        stop.set()
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
    exit_code = 128 + received_signal[0] if received_signal else (1 if failure or infrastructure_errors else 0)
    status = "interrupted" if received_signal else ("failed" if exit_code else "finished")
    summary = {"solver": solver, "status": status, "selected": len(inputs), "recorded": recorded,
               "not_run": len(inputs) - recorded, "counts": counts, "infrastructure_errors": infrastructure_errors,
               "wall_time_seconds": round(time.perf_counter() - batch_started, 6),
               "finished_at": datetime.now(LOCAL_TIME).isoformat(), "exit_code": exit_code}
    if failure:
        summary["runner_error"] = failure
    write_json(run_dir / "summary.json", summary)
    metadata.update({"status": status, "finished_at": summary["finished_at"], "exit_code": exit_code})
    write_json(run_dir / "run.json", metadata)
    print(f"{status}: recorded={recorded}/{len(inputs)} " + " ".join(f"{k}={v}" for k, v in counts.items()), flush=True)
    print(f"CSV: {run_dir / 'results.csv'}", flush=True)
    if failure:
        print(f"runner error: {failure}", file=sys.stderr)
    return exit_code


def main():
    if sys.argv[1:] in (["--help"], ["-h"]):
        print(usage())
        return 0
    if len(sys.argv) != 2 or sys.argv[1] not in ENTRIES:
        print(usage(), file=sys.stderr)
        return 2
    solver = sys.argv[1]
    jobs = integer_option("JOBS", 180, 1)
    limit = integer_option("LIMIT", 0, 0)
    timeout = float(os.environ.get("TIMEOUT", "600"))
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("TIMEOUT 必须是有限的正数（秒）")
    heap_mb = integer_option("SMTINTERPOL_HEAP_MB", 256, 16) if solver == "smtinterpol" else None
    benchmarks = Path(os.environ.get("BENCHMARK_DIR", BENCHMARK_DEFAULT)).expanduser().resolve()
    results = Path(os.environ.get("RESULTS_DIR", str(ROOT / "results"))).expanduser().resolve()
    executable = ROOT / "competitors" / solver / "bin" / ENTRIES[solver]
    if not benchmarks.is_dir():
        raise ValueError(f"benchmark 目录不存在: {benchmarks}")
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ValueError(f"求解器不存在或不可执行: {executable}")
    return run_batch(solver, jobs, timeout, limit, benchmarks, results, executable, heap_mb)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError) as error:
        print(f"配置/文件错误: {error}", file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        sys.exit(130)
