import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "native_campaign.py"
spec = importlib.util.spec_from_file_location("native_campaign", SCRIPT) if SCRIPT.exists() else None
module = importlib.util.module_from_spec(spec) if spec else None
if spec:
    spec.loader.exec_module(module)

ANCHOR = "                _sp1_quorum_size > 1,\n"
SOURCE = "// pinned source fixture\n" + ANCHOR + "// no fixed witness assignment\n"


def digest(data):
    return hashlib.sha256(data).hexdigest()


class NativeCampaignTest(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(module, "Native campaign runner must exist before native generation")
        self.temp = tempfile.TemporaryDirectory(prefix="ufnia-native-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.code = self.root / "code"
        (self.code / "deps").mkdir(parents=True)
        (self.code / "record.py").write_text("# existing recording boundary\n")
        (self.code / "package.json").write_text('{"dependencies":{"@informalsystems/quint":"0.33.1"}}\n')
        (self.code / "package-lock.json").write_text('{"lockfileVersion":3,"packages":{"node_modules/@informalsystems/quint":{"version":"0.33.1"}}}\n')
        sources = {"spec/slashing_proposer_machine.qnt": SOURCE,
                   "spec/types.qnt": "// independently pinned imported source\n"}
        (self.code / "source-manifest.json").write_text(json.dumps({k: digest(v.encode()) for k, v in sources.items()}))
        archives = {
            "aztec": ("source.tgz", {"source-root/" + k: v for k, v in sources.items()}),
            "node": ("node.tgz", {"node-v22.23.4-linux-x64/bin/node": "node", "node-v22.23.4-linux-x64/bin/npm": "npm"}),
            "apalache": ("backend.tgz", {"apalache/bin/apalache-mc": "backend"})
        }
        self.hashes = {}
        for kind, (filename, files) in archives.items():
            archive = self.code / "deps" / filename
            with tarfile.open(archive, "w:gz") as stream:
                for name, text in files.items():
                    data = text.encode()
                    info = tarfile.TarInfo(name)
                    info.size = len(data)
                    info.mode = 0o755
                    stream.addfile(info, io.BytesIO(data))
            self.hashes[kind] = digest(archive.read_bytes())
        self.locks = {
            "aztec": {"repository": "https://github.com/konnov/aztec-governance-formal-verification-2025q3",
                      "commit": "6fee2e4f6dbbe632cf62b411e344e1a008642cd7",
                      "archive": "deps/source.tgz", "sha256": self.hashes["aztec"]},
            "node": {"version": "22.23.4", "archive": "deps/node.tgz", "sha256": self.hashes["node"]},
            "quint": {"version": "0.33.1"},
            "apalache": {"version": "0.62.4", "archive": "deps/backend.tgz",
                         "expected_sha256": self.hashes["apalache"]}
        }
        (self.code / "dependencies.json").write_text(json.dumps(self.locks))
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(module, "PINNED_ARCHIVES", self.hashes).start()
        self.calls = []
        self.native_stdout = "Invariant signal_quorum_ex is violated.\nAn example execution:\n"
        self.native_stderr = ""
        self.native_timeout = False
        self.native_returncode = 0
        self.process = mock.patch.object(module.subprocess, "run", side_effect=self.fake_run).start()

    def fake_run(self, command, **kwargs):
        command = list(map(str, command))
        self.calls.append((command, kwargs))
        if command[-1:] == ["--version"]:
            return subprocess.CompletedProcess(command, 0, "0.33.1\n", "")
        self.assertIn("--output", command)
        output = Path(command[command.index("--output") + 1])
        output.mkdir(parents=True)
        native = "verify" in command
        if not native:
            npm = self.root / "runtime/npm"
            target = npm / "node_modules/@informalsystems/quint"
            target.mkdir(parents=True)
            (target / "package.json").write_text('{"version":"0.33.1"}')
            binary = npm / "node_modules/.bin/quint"
            binary.parent.mkdir(parents=True)
            binary.write_text("# fake pinned Quint executable\n")
            stdout, stderr, timeout, rc = "npm installed\n", "", False, 0
        else:
            stdout, stderr = self.native_stdout, self.native_stderr
            timeout, rc = self.native_timeout, self.native_returncode
            artifacts = Path(kwargs["cwd"]) / "_apalache-out/run1"
            artifacts.mkdir(parents=True)
            (artifacts / "log0.smt").write_text("(declare-fun state (Int) Int)\n(check-sat)\n")
            if "violated" in stdout:
                (artifacts / "counterexample.itf.json").write_text('{"states":[{}]}')
        (output / "stdout.log").write_text(stdout)
        (output / "stderr.log").write_text(stderr)
        (output / "execution.json").write_text(json.dumps({
            "argv": command[command.index("--") + 1:], "cwd": str(kwargs.get("cwd", Path.cwd())),
            "started_utc": "2026-11-05T00:00:00+00:00", "elapsed_seconds": 0.125,
            "returncode": rc, "timed_out": timeout, "status": "unknown"
        }))
        return subprocess.CompletedProcess(command, rc)

    def runtime(self):
        return module.prepare_runtime(self.code, self.root / "runtime")

    def profile(self, qmin=2):
        return {"id": f"sp_quorum_q{qmin}_d{qmin}", "source": "spec/slashing_proposer_machine.qnt",
                "invariant": "signal_quorum_ex", "qmin": qmin, "max_steps": qmin, "init": "init"}

    def test_six_profiles_represent_quorum_and_execution_goals(self):
        profiles = module.default_profiles()
        self.assertEqual(len(profiles), 6)
        self.assertEqual([(p["qmin"], p["max_steps"]) for p in profiles if p["invariant"] == "signal_quorum_ex"],
                         [(2, 2), (3, 3), (4, 4), (6, 6)])
        self.assertEqual([(p["qmin"], p["max_steps"]) for p in profiles if p["invariant"] == "executed_ex"],
                         [(2, 3), (3, 4)])

    def test_corrupt_archive_is_rejected_before_any_command(self):
        (self.code / "deps/source.tgz").write_bytes(b"replacement")
        with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
            self.runtime()
        self.process.assert_not_called()

    def test_runtime_reuse_installs_once_and_revalidates_every_source(self):
        runtime = self.runtime()
        self.assertEqual(self.runtime(), runtime)
        npm_commands = [c for c, _ in self.calls if "ci" in c]
        self.assertEqual(len(npm_commands), 1)
        source = next((runtime / "source").iterdir())
        (source / "spec/types.qnt").write_text("tampered imported source")
        with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
            self.runtime()

    def test_reused_runtime_with_modified_lock_is_rejected(self):
        runtime = self.runtime()
        (runtime / "npm/package-lock.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "lock"):
            self.runtime()

    def test_configuration_restriction_preserves_original_and_records_diff(self):
        runtime = self.runtime()
        original = next((runtime / "source").iterdir()) / "spec/slashing_proposer_machine.qnt"
        for qmin, classification in [(2, "faithful_export"), (3, "application_derived")]:
            with self.subTest(qmin=qmin):
                result = module.run_case(runtime, self.profile(qmin), self.root / f"q{qmin}")
                self.assertEqual(result["task"]["classification"], classification)
                effective = Path(result["artifacts"]["source_file"]).read_text()
                self.assertIn("_sp1_quorum_size > 1,", effective)
                self.assertEqual(result["source"]["original_sha256"], digest(SOURCE.encode()))
                if qmin > 2:
                    self.assertIn("_sp1_quorum_size >= 3,", effective)
                    self.assertIn("+                _sp1_quorum_size >= 3,", Path(result["artifacts"]["source_diff"]).read_text())
                else:
                    self.assertEqual(effective, SOURCE)
        self.assertEqual(original.read_text(), SOURCE)

    def test_unknown_restriction_anchor_never_runs_a_changed_model(self):
        runtime = self.runtime()
        with mock.patch.object(module, "QMIN_ANCHOR", "missing expected constructor anchor\n"):
            with self.assertRaisesRegex(ValueError, "anchor"):
                module.run_case(runtime, self.profile(3), self.root / "bad-anchor")
        self.assertFalse(any("verify" in command for command, _ in self.calls))

    def test_requested_encoding_is_recorded_without_claiming_uf_eligibility(self):
        profile = dict(self.profile(), encoding="funArrays")
        result = module.run_case(self.runtime(), profile, self.root / "fun-arrays")
        config = json.loads(Path(result["artifacts"]["config"]).read_text())
        self.assertEqual(config["checker"]["smt-encoding"], "funArrays")
        self.assertEqual(result["task"]["native_encoding"], "funArrays")
        self.assertIn("must be established", result["task"]["uf_meaning"])

    def test_zero_exit_runtime_error_is_native_error(self):
        self.native_stdout = "RuntimeError: solver returned unknown after 30 seconds\n"
        result = module.run_case(self.runtime(), self.profile(), self.root / "error")
        self.assertEqual(result["execution"]["returncode"], 0)
        self.assertEqual(result["native_outcome"]["status"], "error")

    def test_witness_records_invocation_configuration_raw_session_and_hashes(self):
        runtime = self.runtime()
        result = module.run_case(runtime, self.profile(), self.root / "sat", timeout_seconds=17)
        self.assertEqual(result["native_outcome"]["status"], "sat")
        self.assertEqual(result["native_outcome"]["meaning"], "bounded_reachability_witness")
        self.assertTrue(result["artifacts"]["raw_sessions"])
        self.assertEqual(result["bounds"]["max_steps"], 2)
        self.assertEqual(result["invocation"]["environment"]["JVM_ARGS"], "-Xmx4G")
        config = json.loads(Path(result["artifacts"]["config"]).read_text())
        self.assertEqual(config["checker"]["smt-encoding"], "oopsla19")
        native_calls = [(c, k) for c, k in self.calls if "verify" in c]
        command, kwargs = native_calls[0]
        self.assertEqual(command[command.index("--timeout") + 1], "17")
        self.assertIn("--max-steps=2", command)
        self.assertTrue(any(c.startswith("--server-endpoint=localhost:") for c in command))
        self.assertEqual(kwargs["env"]["QUINT_HOME"], str(runtime / "quint-home"))
        report = json.loads(Path(result["artifacts"]["report"]).read_text())
        self.assertEqual(report["run_sha256"], result["run_sha256"])
        for path, expected in result["artifact_hashes"].items():
            self.assertEqual(digest(Path(path).read_bytes()), expected)

    def test_recorded_timeout_takes_precedence_over_partial_witness_text(self):
        self.native_timeout = True
        self.native_returncode = 124
        result = module.run_case(self.runtime(), self.profile(), self.root / "timeout")
        self.assertEqual(result["native_outcome"]["status"], "timeout")
        self.assertTrue(result["execution"]["timed_out"])

    def test_zero_exit_without_explicit_native_result_remains_unknown(self):
        self.native_stdout = "VCGen produced 3 verification conditions.\n"
        result = module.run_case(self.runtime(), self.profile(), self.root / "unknown")
        self.assertEqual(result["native_outcome"]["status"], "unknown")


if __name__ == "__main__":
    unittest.main()
