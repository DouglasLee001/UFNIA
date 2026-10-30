"""Exercise the benchmark runner using real, short-lived solver subprocesses."""

import csv
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest


PROJECT = Path(__file__).resolve().parents[2]


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ufnia-runner-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.scripts = self.root / "scripts"
        self.scripts.mkdir()
        for filename in ("run.sh", "run_benchmarks.py"):
            source = PROJECT / "scripts" / filename
            if source.exists():
                shutil.copy2(source, self.scripts / filename)
        self.benchmarks = self.root / "benchmarks"
        self.benchmarks.mkdir()
        self.results = self.root / "results"
        self.solver = self.root / "competitors/yices2/bin/yices-smt2"
        self.solver.parent.mkdir(parents=True)
        self.solver.write_text(
            "#!" + sys.executable + "\n"
            "import fcntl, json, os, pathlib, subprocess, sys, time\n"
            "action = pathlib.Path(sys.argv[-1]).read_text().strip()\n"
            "if action == 'slow':\n"
            "    time.sleep(3)\n"
            "elif action == 'parallel':\n"
            "    counter = os.environ['FAKE_COUNTER']\n"
            "    def update(delta):\n"
            "        with open(counter, 'r+') as stream:\n"
            "            fcntl.flock(stream, fcntl.LOCK_EX)\n"
            "            state = json.load(stream)\n"
            "            state['active'] += delta\n"
            "            state['maximum'] = max(state['maximum'], state['active'])\n"
            "            stream.seek(0)\n"
            "            json.dump(state, stream)\n"
            "            stream.truncate()\n"
            "    update(1)\n"
            "    time.sleep(0.2)\n"
            "    update(-1)\n"
            "    print('sat')\n"
            "elif action == 'spawn':\n"
            "    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "    pathlib.Path(os.environ['FAKE_PIDS']).write_text(json.dumps([os.getpid(), child.pid]))\n"
            "    time.sleep(60)\n"
            "elif action == 'error':\n"
            "    print('sat')\n"
            "    print('solver failed', file=sys.stderr)\n"
            "    sys.exit(7)\n"
            "elif action == 'protocol-error':\n"
            "    print('success')\n"
            "    print('(error \"Unknown function symbol undeclared_variable\")')\n"
            "    print('sat')\n"
            "elif action == 'answer-then-error':\n"
            "    print('sat')\n"
            "    print('(error \"Unexpected trailing command\")')\n"
            "else:\n"
            "    print(action)\n"
        )
        self.solver.chmod(0o755)

    def benchmark(self, name, action):
        path = self.benchmarks / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(action + "\n")

    def run_runner(self, solver="yices2", **overrides):
        env = os.environ.copy()
        env.update(
            JOBS="2",
            TIMEOUT="2",
            LIMIT="0",
            BENCHMARK_DIR=str(self.benchmarks),
            RESULTS_DIR=str(self.results),
        )
        env.update({key: str(value) for key, value in overrides.items()})
        return subprocess.run(
            ["bash", str(self.scripts / "run.sh"), solver],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )

    def assert_success(self, process):
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)

    def latest_run(self):
        latest = self.results / "yices2/latest"
        self.assertTrue(latest.is_symlink(), "latest must point to a preserved run")
        self.assertFalse(os.path.isabs(os.readlink(latest)))
        return latest.resolve()

    def read_rows(self, run):
        with (run / "results.csv").open(newline="") as stream:
            return {row["benchmark"]: row for row in csv.DictReader(stream)}

    def test_classifies_exit_status_and_enforces_per_benchmark_timeout(self):
        expected = {
            "sat.smt2": "sat",
            "unsat.smt2": "unsat",
            "unknown.smt2": "unknown",
            "failure.smt2": "error",
            "timeout.smt2": "timeout",
        }
        for name, result in expected.items():
            self.benchmark(name, {"error": "error", "timeout": "slow"}.get(result, result))
        process = self.run_runner(TIMEOUT="0.4")
        self.assert_success(process)
        run = self.latest_run()
        rows = self.read_rows(run)
        self.assertEqual({name: row["result"] for name, row in rows.items()}, expected)
        self.assertEqual(int(rows["failure.smt2"]["exit_code"]), 7)
        for row in rows.values():
            self.assertGreaterEqual(float(row["wall_time_seconds"]), 0)
        self.assertLess(float(rows["timeout.smt2"]["wall_time_seconds"]), 2)
        self.assertEqual((run / "logs/failure.smt2.stdout").read_text(), "sat\n")
        self.assertIn("solver failed", (run / "logs/failure.smt2.stderr").read_text())
        for filename in ("summary.json", "run.json"):
            self.assertIsInstance(json.loads((run / filename).read_text()), dict)

    def test_preserves_nested_paths_spaces_and_runs(self):
        self.benchmark("nested one/shared name.smt2", "sat")
        self.benchmark("nested two/shared name.smt2", "unsat")
        self.assert_success(self.run_runner())
        first = self.latest_run()
        self.assertEqual(set(self.read_rows(first)), {
            "nested one/shared name.smt2", "nested two/shared name.smt2",
        })
        self.assertEqual((first / "logs/nested one/shared name.smt2.stdout").read_text(), "sat\n")
        self.assertEqual((first / "logs/nested two/shared name.smt2.stdout").read_text(), "unsat\n")
        self.assert_success(self.run_runner())
        second = self.latest_run()
        self.assertNotEqual(first, second)
        self.assertTrue((first / "results.csv").is_file())
        self.assertEqual(len(list((self.results / "yices2/runs").iterdir())), 2)

    def test_protocol_error_before_answer_is_not_counted_as_sat(self):
        self.benchmark("protocol-error.smt2", "protocol-error")
        self.assert_success(self.run_runner())
        row = self.read_rows(self.latest_run())["protocol-error.smt2"]
        self.assertEqual(int(row["exit_code"]), 0)
        self.assertEqual(row["result"], "error")

    def test_protocol_error_after_answer_is_not_counted_as_sat(self):
        self.benchmark("answer-then-error.smt2", "answer-then-error")
        self.assert_success(self.run_runner())
        row = self.read_rows(self.latest_run())["answer-then-error.smt2"]
        self.assertEqual(int(row["exit_code"]), 0)
        self.assertEqual(row["result"], "error")

    def test_runs_in_parallel_without_exceeding_jobs(self):
        counter = self.root / "counter.json"
        counter.write_text(json.dumps({"active": 0, "maximum": 0}))
        for number in range(6):
            self.benchmark(f"case-{number}.smt2", "parallel")
        self.assert_success(self.run_runner(JOBS=2, FAKE_COUNTER=counter))
        self.assertEqual(json.loads(counter.read_text()), {"active": 0, "maximum": 2})
        self.assertEqual(len(self.read_rows(self.latest_run())), 6)

    def test_limit_restricts_scheduled_benchmarks(self):
        for number in range(4):
            self.benchmark(f"case-{number}.smt2", "sat")
        self.assert_success(self.run_runner(LIMIT=2))
        self.assertEqual(len(self.read_rows(self.latest_run())), 2)

    def test_rejects_bad_solver_or_options_before_writing_results(self):
        self.benchmark("sat.smt2", "sat")
        cases = [
            ("not-a-solver", {}),
            ("yices2", {"JOBS": "0"}),
            ("yices2", {"TIMEOUT": "bad"}),
            ("yices2", {"LIMIT": "-1"}),
        ]
        for solver, options in cases:
            with self.subTest(solver=solver, options=options):
                process = self.run_runner(solver, **options)
                self.assertNotEqual(process.returncode, 0)
                self.assertFalse(self.results.exists(), process.stdout + process.stderr)

    def test_solver_startup_failure_returns_nonzero(self):
        self.benchmark("sat.smt2", "sat")
        self.solver.write_text("#!/path/to/nonexistent/python\n")
        process = self.run_runner()
        self.assertNotEqual(process.returncode, 0, process.stdout + process.stderr)

    def test_interrupt_terminates_solver_descendants_and_saves_completed_rows(self):
        self.benchmark("00-completed.smt2", "sat")
        self.benchmark("01-interrupted.smt2", "spawn")
        pids_path = self.root / "pids.json"
        env = os.environ.copy()
        env.update(JOBS="1", TIMEOUT="60", LIMIT="0", BENCHMARK_DIR=str(self.benchmarks),
                   RESULTS_DIR=str(self.results), FAKE_PIDS=str(pids_path))
        process = subprocess.Popen(
            ["bash", str(self.scripts / "run.sh"), "yices2"], cwd=self.root,
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, start_new_session=True,
        )
        pids = []
        try:
            deadline = time.monotonic() + 5
            while not pids_path.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(pids_path.exists(), "runner must launch the solver before interruption")
            pids = json.loads(pids_path.read_text())
            process.send_signal(signal.SIGINT)
            stdout, stderr = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 130, stdout + stderr)
            rows = self.read_rows(self.latest_run())
            self.assertEqual(rows["00-completed.smt2"]["result"], "sat")
            for pid in pids:
                stat = Path(f"/proc/{pid}/stat")
                if stat.exists():
                    state = stat.read_text().rsplit(")", 1)[1].split()[0]
                    self.assertEqual(state, "Z", f"solver descendant {pid} remains running")
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=5)
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


if __name__ == "__main__":
    unittest.main()
