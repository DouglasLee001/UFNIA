import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

CODE = Path(__file__).resolve().parents[1]
SCRIPT = CODE / "resume_backend.py"
spec = importlib.util.spec_from_file_location("resume_backend", SCRIPT) if SCRIPT.exists() else None
module = importlib.util.module_from_spec(spec) if spec else None
if spec:
    sys.path.insert(0, str(CODE))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)

API = "https://api.github.com/repos/apalache-mc/apalache/releases/assets/633378609"
PUBLIC = "https://github.com/apalache-mc/apalache/releases/download/v0.62.4/apalache.tgz"
SIGNED = "https://release-assets.githubusercontent.com/github-production-release-asset/asset?signature=DO_NOT_LOG"
SECRET = "https://proxy-user:DO_NOT_LOG@proxy.invalid:1234"


class ResumeBackendTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(module, "A one-resolution backend resume wrapper is required")

    def fake_head(self, status=302, location=SIGNED, returncode=0):
        def run(command, **kwargs):
            header_path = Path(command[command.index("--dump-header") + 1])
            self.assertEqual(header_path.parent.stat().st_mode & 0o777, 0o700)
            lines = [f"HTTP/1.1 {status} response"]
            if location is not None:
                lines.append("Location: " + location)
            header_path.write_text("\r\n".join(lines) + "\r\n\r\n")
            return subprocess.CompletedProcess(command, returncode, str(status), SECRET + SIGNED)
        return run

    def metadata(self, code):
        pins = {"apalache": {"official_api_asset": API, "url": PUBLIC,
                              "expected_size": 6,
                              "expected_sha256": hashlib.sha256(b"abcdef").hexdigest(),
                              "archive": "deps/apalache-0.62.4.tgz"}}
        (code / "dependencies.json").write_text(json.dumps(pins))
        return pins["apalache"]

    def test_resolves_one_bounded_private_head_without_following_redirect(self):
        with patch.object(module.subprocess, "run", side_effect=self.fake_head()) as run:
            self.assertEqual(module.resolve_asset(API, timeout=12, proxy_mode="direct"), SIGNED)
        run.assert_called_once()
        command = run.call_args.args[0]
        self.assertEqual(command[:2], ["curl", "--disable"])
        self.assertIn("--head", command)
        self.assertIn("--ipv4", command)
        self.assertNotIn("--location", command)
        self.assertEqual(command[command.index("--proto") + 1], "=https")
        self.assertEqual(command[command.index("--max-time") + 1], "12")
        self.assertEqual(command[command.index("--noproxy") + 1], "*")
        self.assertIn("Accept: application/octet-stream", command)
        self.assertLessEqual(run.call_args.kwargs["timeout"], 13)
        self.assertFalse(Path(command[command.index("--dump-header") + 1]).exists())

    def test_rejects_wrong_status_or_untrusted_redirect_without_echoing_location(self):
        cases = [(200, SIGNED), (404, SIGNED), (302, None),
                 (302, "http://release-assets.githubusercontent.com/asset"),
                 (302, "https://release-assets.githubusercontent.com.evil.invalid/asset"),
                 (302, "https://proxy-user:DO_NOT_LOG@release-assets.githubusercontent.com/asset")]
        for status, location in cases:
            with self.subTest(status=status, location=location), patch.object(
                    module.subprocess, "run", side_effect=self.fake_head(status, location)):
                with self.assertRaises(module.ResolutionError) as caught:
                    module.resolve_asset(API)
                self.assertNotIn("DO_NOT_LOG", str(caught.exception))
                self.assertNotIn("https://", str(caught.exception))

    def test_curl_failure_reports_only_exit_number(self):
        with patch.object(module.subprocess, "run", side_effect=self.fake_head(returncode=28)):
            with self.assertRaisesRegex(module.ResolutionError, "28") as caught:
                module.resolve_asset(API)
        self.assertNotIn("DO_NOT_LOG", str(caught.exception))
        self.assertNotIn("https://", str(caught.exception))

    def test_resolves_once_then_delegates_to_fixed_resume_path_and_preserves_parts(self):
        with tempfile.TemporaryDirectory() as temp:
            code = Path(temp)
            pins = self.metadata(code)
            parts = code / "deps/apalache-0.62.4.tgz.parts"
            parts.mkdir(parents=True)
            part = parts / "00001.part"
            part.write_bytes(b"existing cached bytes")
            before = part.stat().st_mtime_ns
            output = io.StringIO()
            with patch.object(module.subprocess, "run", side_effect=self.fake_head()) as head, \
                    patch.object(module.download_ranges, "download") as download, \
                    patch.object(module.time, "monotonic", return_value=100), \
                    contextlib.redirect_stdout(output):
                module.resume_backend(code, workers=2, request_timeout=120, retries=3,
                                      deadline=900, proxy_mode="direct")
            head.assert_called_once()
            download.assert_called_once_with(
                SIGNED, 6, pins["expected_sha256"], code / pins["archive"],
                workers=2, timeout=120, retries=3, deadline=900, proxy_mode="direct")
            self.assertEqual(part.read_bytes(), b"existing cached bytes")
            self.assertEqual(part.stat().st_mtime_ns, before)
            self.assertNotIn("DO_NOT_LOG", output.getvalue())

    def test_verified_archive_skips_api_and_transfer(self):
        with tempfile.TemporaryDirectory() as temp:
            code = Path(temp)
            pins = self.metadata(code)
            target = code / pins["archive"]
            target.parent.mkdir()
            target.write_bytes(b"abcdef")
            output = io.StringIO()
            with patch.object(module.subprocess, "run") as head, \
                    patch.object(module.download_ranges, "download") as download, \
                    contextlib.redirect_stdout(output):
                module.resume_backend(code)
            head.assert_not_called()
            download.assert_not_called()
            self.assertEqual(json.loads(output.getvalue())["event"], "verified_existing_archive")

    def test_wrong_hash_existing_archive_still_resolves_and_downloads(self):
        with tempfile.TemporaryDirectory() as temp:
            code = Path(temp)
            pins = self.metadata(code)
            target = code / pins["archive"]
            target.parent.mkdir()
            target.write_bytes(b"wrong!")
            with patch.object(module.subprocess, "run", side_effect=self.fake_head()) as head, \
                    patch.object(module.download_ranges, "download") as download:
                module.resume_backend(code)
            head.assert_called_once()
            download.assert_called_once()

    def test_one_deadline_covers_existing_hash_head_and_transfer(self):
        with tempfile.TemporaryDirectory() as temp:
            code = Path(temp)
            pins = self.metadata(code)
            target = code / pins["archive"]
            target.parent.mkdir()
            target.write_bytes(b"wrong!")
            with patch.object(module.time, "monotonic", side_effect=[100, 105, 112]), \
                    patch.object(module.download_ranges, "file_digest", return_value="0" * 64) as digest, \
                    patch.object(module, "resolve_asset", return_value=SIGNED) as resolve, \
                    patch.object(module.download_ranges, "download") as download:
                module.resume_backend(code, deadline=20)
            digest.assert_called_once_with(target, 120)
            resolve.assert_called_once_with(API, timeout=15, proxy_mode="direct")
            self.assertEqual(download.call_args.kwargs["deadline"], 8)

    def test_expired_deadline_prevents_next_network_stage(self):
        for times, head_calls in [([100, 121], 0), ([100, 101, 121], 1)]:
            with self.subTest(times=times), tempfile.TemporaryDirectory() as temp:
                code = Path(temp)
                self.metadata(code)
                with patch.object(module.time, "monotonic", side_effect=times), \
                        patch.object(module, "resolve_asset", return_value=SIGNED) as resolve, \
                        patch.object(module.download_ranges, "download") as download:
                    with self.assertRaises(TimeoutError):
                        module.resume_backend(code, deadline=20)
                self.assertEqual(resolve.call_count, head_calls)
                download.assert_not_called()

    def test_cli_defaults_and_options_are_forwarded(self):
        for argv, expected in [([], {"workers": 4, "request_timeout": 180, "retries": 5,
                                    "deadline": 3600, "proxy_mode": "direct"}),
                               (["--workers", "1", "--request-timeout", "120", "--retries", "2",
                                 "--deadline", "600", "--proxy-mode", "direct"],
                                {"workers": 1, "request_timeout": 120, "retries": 2,
                                 "deadline": 600, "proxy_mode": "direct"})]:
            with self.subTest(argv=argv), patch.object(module, "resume_backend") as resume:
                self.assertEqual(module.main(argv), 0)
                resume.assert_called_once_with(CODE, **expected)

    def test_unexpected_exception_does_not_leak_signed_url_or_proxy(self):
        output = io.StringIO()
        with patch.object(module, "resume_backend", side_effect=OSError(SIGNED + SECRET)), \
                contextlib.redirect_stdout(output):
            self.assertEqual(module.main([]), 1)
        report = json.loads(output.getvalue())
        self.assertEqual(report["event"], "failed")
        self.assertNotIn("DO_NOT_LOG", output.getvalue())
        self.assertNotIn("https://", output.getvalue())


if __name__ == "__main__":
    unittest.main()
