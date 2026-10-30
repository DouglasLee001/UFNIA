"""Check SMTInterpol's Java selection and fail-fast runner preflight."""

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


class LauncherRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="smtinterpol-runtime-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project with spaces"
        self.solver = self.root / "competitors/smtinterpol"
        self.launcher = self.solver / "bin/smtinterpol"
        self.launcher.parent.mkdir(parents=True)
        shutil.copy2(PROJECT / "competitors/smtinterpol/bin/smtinterpol", self.launcher)
        self.jar = self.solver / "install/smtinterpol.jar"
        self.jar.parent.mkdir()
        self.jar.touch()
        self.capture = self.root / "java invocation.json"
        self.make_java(self.solver / "runtime/bin/java", "bundled")

    def make_java(self, path, label):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "#!" + sys.executable + "\n"
            "import json, os, pathlib, sys\n"
            "pathlib.Path(os.environ['JAVA_CAPTURE']).write_text(json.dumps({\n"
            f"    'label': {label!r}, 'args': sys.argv[1:],\n"
            "    'java_tool_options': os.environ.get('JAVA_TOOL_OPTIONS', '')}))\n"
            "print('fake Java runtime')\n"
        )
        path.chmod(0o755)
        return path

    def launch(self, *arguments, **overrides):
        env = os.environ.copy()
        env.pop("JAVA_BIN", None)
        env.pop("JAVA_HOME", None)
        env.update(PATH="/nonexistent", JAVA_CAPTURE=str(self.capture),
                   JAVA_TOOL_OPTIONS="-Duser.flag=kept")
        env.update({key: str(value) for key, value in overrides.items()})
        return subprocess.run([str(self.launcher), *arguments], env=env,
                              capture_output=True, text=True, timeout=5)

    def test_bundled_runtime_works_without_path_and_preserves_arguments(self):
        benchmark = "input with spaces.smt2"
        process = self.launch("-q", benchmark)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        invocation = json.loads(self.capture.read_text())
        self.assertEqual(invocation["label"], "bundled")
        self.assertEqual(invocation["args"], ["-jar", str(self.jar), "-q", benchmark])
        self.assertEqual(invocation["java_tool_options"], "-Duser.flag=kept")

    def test_java_bin_override_takes_priority_over_java_home(self):
        binary = self.make_java(self.root / "custom binary/java command", "JAVA_BIN")
        home = self.root / "custom java home"
        self.make_java(home / "bin/java", "JAVA_HOME")
        process = self.launch("-version", JAVA_BIN=binary, JAVA_HOME=home)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        invocation = json.loads(self.capture.read_text())
        self.assertEqual(invocation["label"], "JAVA_BIN")
        self.assertEqual(invocation["args"], ["-jar", str(self.jar), "-version"])

    def test_java_home_override_takes_priority_over_bundled_runtime(self):
        home = self.root / "custom java home"
        self.make_java(home / "bin/java", "JAVA_HOME")
        process = self.launch("-version", JAVA_HOME=home)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(json.loads(self.capture.read_text())["label"], "JAVA_HOME")

    def test_invalid_explicit_override_fails_without_falling_back(self):
        for name in ("JAVA_BIN", "JAVA_HOME"):
            missing = self.root / f"missing {name} override"
            with self.subTest(variable=name):
                process = self.launch("-version", **{name: missing})
                self.assertNotEqual(process.returncode, 0, process.stdout + process.stderr)
                self.assertIn(name, process.stderr)
                self.assertIn(str(missing), process.stderr)
                self.assertFalse(self.capture.exists(), "invalid overrides must not use bundled Java")

    def test_empty_explicit_override_fails_without_falling_back(self):
        for name in ("JAVA_BIN", "JAVA_HOME"):
            with self.subTest(variable=name):
                process = self.launch("-version", **{name: ""})
                self.assertNotEqual(process.returncode, 0, process.stdout + process.stderr)
                self.assertIn(name, process.stderr)
                self.assertFalse(self.capture.exists())


class RunnerPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="smtinterpol-preflight-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project with spaces"
        scripts = self.root / "scripts"
        scripts.mkdir(parents=True)
        for filename in ("run.sh", "run_benchmarks.py"):
            shutil.copy2(PROJECT / "scripts" / filename, scripts / filename)
        self.runner = scripts / "run.sh"
        self.benchmarks = self.root / "benchmarks"
        self.benchmarks.mkdir()
        for answer in ("sat", "unsat", "unknown"):
            (self.benchmarks / f"{answer}.smt2").write_text(answer + "\n")
        self.results = self.root / "results"
        self.calls = self.root / "launcher calls.jsonl"
        self.child_pid = self.root / "preflight child.pid"
        self.addCleanup(self.cleanup_child)
        launcher = self.root / "competitors/smtinterpol/bin/smtinterpol"
        launcher.parent.mkdir(parents=True)
        launcher.write_text(
            "#!" + sys.executable + "\n"
            "import json, os, pathlib, subprocess, sys, time\n"
            "with open(os.environ['FAKE_SMT_CALLS'], 'a') as stream:\n"
            "    stream.write(json.dumps({'args': sys.argv[1:], 'pid': os.getpid(),\n"
            "        'java_tool_options': os.environ.get('JAVA_TOOL_OPTIONS', '')}) + '\\n')\n"
            "if sys.argv[1:] == ['-version']:\n"
            "    mode = os.environ.get('FAKE_VERSION_MODE', 'success')\n"
            "    if mode == 'fail':\n"
            "        print('Java runtime unavailable', file=sys.stderr)\n"
            "        sys.exit(127)\n"
            "    if mode == 'timeout':\n"
            "        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "        pathlib.Path(os.environ['FAKE_SMT_CHILD_PID']).write_text(str(child.pid))\n"
            "        time.sleep(40)\n"
            "    print('SMTInterpol fake stable version', file=sys.stderr)\n"
            "else:\n"
            "    print(pathlib.Path(sys.argv[-1]).read_text().strip())\n"
        )
        launcher.chmod(0o755)

    def run_runner(self, **overrides):
        env = os.environ.copy()
        env.update(JOBS="2", TIMEOUT="2", LIMIT="0", SMTINTERPOL_HEAP_MB="192",
                   BENCHMARK_DIR=str(self.benchmarks), RESULTS_DIR=str(self.results),
                   FAKE_SMT_CALLS=str(self.calls), FAKE_SMT_CHILD_PID=str(self.child_pid),
                   JAVA_TOOL_OPTIONS="-Duser.flag=kept")
        env.update({key: str(value) for key, value in overrides.items()})
        return subprocess.run(["/bin/bash", str(self.runner), "smtinterpol"], cwd=self.root,
                              env=env, capture_output=True, text=True, timeout=25)

    def read_calls(self):
        return [json.loads(line) for line in self.calls.read_text().splitlines()]

    def cleanup_child(self):
        if self.child_pid.exists():
            try:
                os.kill(int(self.child_pid.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass

    def test_preflight_uses_version_and_same_java_options_as_solving(self):
        process = self.run_runner()
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        calls = self.read_calls()
        self.assertEqual(calls[0]["args"], ["-version"])
        self.assertEqual(len(calls), 4)
        options = calls[0]["java_tool_options"]
        for required in ("-Duser.flag=kept", "-XX:ActiveProcessorCount=1", "-XX:+UseSerialGC", "-Xmx192m"):
            self.assertIn(required, options)
        for call in calls[1:]:
            self.assertEqual(call["java_tool_options"], options)
            self.assertEqual(call["args"][0], "-q")
        with (self.results / "smtinterpol/latest/results.csv").open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual({row["result"] for row in rows}, {"sat", "unsat", "unknown"})

    def test_failed_preflight_does_not_create_results(self):
        process = self.run_runner(FAKE_VERSION_MODE="fail")
        self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
        self.assertIn("SMTInterpol", process.stderr)
        self.assertIn("Java runtime unavailable", process.stderr)
        self.assertFalse(self.results.exists())
        self.assertEqual([call["args"] for call in self.read_calls()], [["-version"]])

    def test_failed_preflight_preserves_existing_latest(self):
        previous = self.results / "smtinterpol/runs/previous"
        previous.mkdir(parents=True)
        (previous / "marker").write_text("keep previous experiment\n")
        latest = self.results / "smtinterpol/latest"
        latest.symlink_to("runs/previous", target_is_directory=True)
        process = self.run_runner(FAKE_VERSION_MODE="fail")
        self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
        self.assertEqual(os.readlink(latest), "runs/previous")
        self.assertEqual(list(previous.parent.iterdir()), [previous])
        self.assertEqual((previous / "marker").read_text(), "keep previous experiment\n")

    def test_hanging_preflight_aborts_in_about_fifteen_seconds(self):
        started = time.monotonic()
        process = self.run_runner(FAKE_VERSION_MODE="timeout")
        elapsed = time.monotonic() - started
        self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
        self.assertIn("SMTInterpol", process.stderr)
        self.assertTrue(any(word in process.stderr.lower() for word in ("timeout", "超时")), process.stderr)
        self.assertGreaterEqual(elapsed, 10)
        self.assertLess(elapsed, 22)
        self.assertFalse(self.results.exists())
        self.assertEqual([call["args"] for call in self.read_calls()], [["-version"]])
        pid = int(self.child_pid.read_text())
        stat = Path(f"/proc/{pid}/stat")
        if stat.exists():
            state = stat.read_text().rsplit(")", 1)[1].split()[0]
            self.assertEqual(state, "Z", f"preflight descendant {pid} remains running")

    def check_preflight_signal_cleanup(self, signum, expected_exit):
        env = os.environ.copy()
        env.update(JOBS="2", TIMEOUT="2", LIMIT="0", SMTINTERPOL_HEAP_MB="192",
                   BENCHMARK_DIR=str(self.benchmarks), RESULTS_DIR=str(self.results),
                   FAKE_SMT_CALLS=str(self.calls), FAKE_SMT_CHILD_PID=str(self.child_pid),
                   FAKE_VERSION_MODE="timeout", JAVA_TOOL_OPTIONS="-Duser.flag=kept")
        process = subprocess.Popen(
            ["/bin/bash", str(self.runner), "smtinterpol"], cwd=self.root, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True,
        )
        leader_pid = None
        try:
            deadline = time.monotonic() + 5
            while process.poll() is None and time.monotonic() < deadline:
                if self.child_pid.exists() and self.child_pid.read_text().strip():
                    break
                time.sleep(0.02)
            self.assertTrue(self.child_pid.exists(), "preflight must start before interruption")
            leader_pid = self.read_calls()[0]["pid"]
            descendant_pid = int(self.child_pid.read_text())
            process.send_signal(signum)
            stdout, stderr = process.communicate(timeout=5)
            with self.subTest(check="exit status"):
                self.assertEqual(process.returncode, expected_exit, stdout + stderr)
            self.assertFalse(self.results.exists())
            self.assertEqual([call["args"] for call in self.read_calls()], [["-version"]])
            for pid in (leader_pid, descendant_pid):
                with self.subTest(check="child cleanup", pid=pid):
                    stat = Path(f"/proc/{pid}/stat")
                    if stat.exists():
                        state = stat.read_text().rsplit(")", 1)[1].split()[0]
                        self.assertEqual(state, "Z", f"preflight process {pid} remains running")
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            if leader_pid is not None:
                try:
                    os.killpg(leader_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.communicate(timeout=5)

    def test_sigterm_during_preflight_cleans_children_without_creating_results(self):
        self.check_preflight_signal_cleanup(signal.SIGTERM, 143)

    def test_sigint_during_preflight_cleans_children_without_creating_results(self):
        self.check_preflight_signal_cleanup(signal.SIGINT, 130)


if __name__ == "__main__":
    unittest.main()
