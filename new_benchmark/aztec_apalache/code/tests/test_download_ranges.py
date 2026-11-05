import hashlib
import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "download_ranges.py"
spec = importlib.util.spec_from_file_location("download_ranges", SCRIPT) if SCRIPT.exists() else None
module = importlib.util.module_from_spec(spec) if spec else None
if spec:
    spec.loader.exec_module(module)

class Handler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", "6")
        self.end_headers()
    def do_GET(self):
        if self.path == "/range":
            start, end = map(int, self.headers["Range"].removeprefix("bytes=").split("-"))
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/6")
            self.send_header("Content-Length", str(end-start+1))
            self.end_headers()
            self.wfile.write(b"abcdef"[start:end+1])
            return
        self.send_response(200)
        self.send_header("Content-Length", "6")
        self.end_headers()
        self.wfile.write(b"abcdef")
    def log_message(self, *args):
        pass

class RangesTest(unittest.TestCase):
    def test_refuses_server_that_ignores_range(self):
        self.assertIsNotNone(module, "Range downloader must reject full-object responses before saving them")
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temp:
                target = Path(temp) / "part"
                with self.assertRaisesRegex(ValueError, "206"):
                    module.fetch_range(f"http://127.0.0.1:{server.server_port}/asset", 1, 2, 6, target, 5)
                self.assertFalse(target.exists())
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_assembles_ranges_and_rejects_wrong_archive_hash(self):
        self.assertTrue(callable(getattr(module, "download", None)), "Assembled downloads must verify the publisher SHA256")
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temp:
                target = Path(temp) / "asset"
                url = f"http://127.0.0.1:{server.server_port}/range"
                with self.assertRaisesRegex(ValueError, "SHA256"):
                    module.download(url, 6, "0"*64, target, workers=2, chunk_size=2, timeout=5, deadline=10)
                self.assertFalse(target.exists())
                module.download(url, 6, hashlib.sha256(b"abcdef").hexdigest(), target, workers=2, chunk_size=2, timeout=5, deadline=10)
                self.assertEqual(target.read_bytes(), b"abcdef")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


# Real local HTTP regressions for disconnects, resumption, proxy selection and deadlines.
import contextlib
import io
import os
import socket
import time
import urllib.parse
from unittest.mock import patch


class RobustHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.server.head_calls += 1
        if self.path.startswith("/head-broken"):
            self.close_connection = True
            self.connection.close()
            return
        self.send_response(200)
        self.send_header("Content-Length", "6")
        self.end_headers()

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        self.server.get_calls.append((path, self.headers.get("Range")))
        if path == "/proxy-failure":
            self.send_response(502)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/limited":
            self.send_response(403)
            self.send_header("X-RateLimit-Remaining", "0")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/flaky" and len(self.server.get_calls) == 1:
            self.close_connection = True
            self.connection.close()
            return
        start, end = map(int, self.headers["Range"].removeprefix("bytes=").split("-"))
        self.send_response(206)
        self.send_header("Content-Range", f"bytes {start}-{end}/6")
        self.send_header("Content-Length", str(end-start+1))
        self.end_headers()
        payload = b"abcdef"[start:end+1]
        try:
            if path == "/slow":
                for byte in payload:
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                    time.sleep(0.2)
            else:
                self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args):
        pass


@contextlib.contextmanager
def local_source():
    server = ThreadingHTTPServer(("127.0.0.1", 0), RobustHandler)
    server.head_calls = 0
    server.get_calls = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class ReliabilityTests(unittest.TestCase):
    digest = hashlib.sha256(b"abcdef").hexdigest()

    def test_head_disconnect_does_not_block_range_download(self):
        with local_source() as (server, base), tempfile.TemporaryDirectory() as temp:
            target = Path(temp)/"asset"
            try:
                module.download(base+"/head-broken", 6, self.digest, target,
                                workers=1, chunk_size=2, timeout=2, deadline=5)
            except Exception as error:
                self.fail(f"An unnecessary HEAD request blocked valid range GETs: {type(error).__name__}")
            self.assertEqual(target.read_bytes(), b"abcdef")
            self.assertEqual(server.head_calls, 0)

    def test_first_range_disconnect_is_retried(self):
        with local_source() as (server, base), tempfile.TemporaryDirectory() as temp:
            target = Path(temp)/"asset"
            try:
                module.download(base+"/flaky", 6, self.digest, target,
                                workers=1, chunk_size=6, timeout=1, deadline=5)
            except Exception as error:
                self.fail(f"Transient first GET disconnect was not recovered: {type(error).__name__}")
            self.assertEqual(target.read_bytes(), b"abcdef")
            self.assertEqual(len(server.get_calls), 2)

    def test_all_existing_parts_assemble_without_any_network(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)/"asset"
            parts=target.with_suffix(".parts")
            parts.mkdir()
            for i, value in enumerate((b"ab", b"cd", b"ef")):
                (parts/f"{i:05d}.part").write_bytes(value)
            try:
                module.download("http://127.0.0.1:1/unreachable", 6, self.digest, target,
                                workers=1, chunk_size=2, timeout=1, deadline=3)
            except Exception as error:
                self.fail(f"Complete cached parts unnecessarily needed the network: {type(error).__name__}")
            self.assertEqual(target.read_bytes(), b"abcdef")

    def test_existing_complete_archive_needs_no_network(self):
        with tempfile.TemporaryDirectory() as temp:
            target=Path(temp)/"asset"
            target.write_bytes(b"abcdef")
            try:
                module.download("http://127.0.0.1:1/unreachable", 6, self.digest, target,
                                workers=1, chunk_size=2, timeout=1, deadline=3)
            except Exception as error:
                self.fail(f"A verified archive unnecessarily needed the network: {type(error).__name__}")
            self.assertEqual(target.read_bytes(), b"abcdef")

    def test_complete_parts_are_not_requested_again(self):
        with local_source() as (server, base), tempfile.TemporaryDirectory() as temp:
            target=Path(temp)/"asset"
            parts=target.with_suffix(".parts")
            parts.mkdir()
            part=parts/"00000.part"
            part.write_bytes(b"ab")
            before=part.stat().st_mtime_ns
            module.download(base+"/range", 6, self.digest, target,
                            workers=1, chunk_size=2, timeout=1, deadline=5)
            self.assertEqual(part.stat().st_mtime_ns, before)
            self.assertEqual(sorted(x[1] for x in server.get_calls), ["bytes=2-3", "bytes=4-5"])

    def test_direct_mode_bypasses_broken_proxy(self):
        with local_source() as (server, base), local_source() as (proxy, proxy_base), tempfile.TemporaryDirectory() as temp:
            proxy_url=proxy_base+"/proxy-failure"
            env={"http_proxy":proxy_url,"HTTP_PROXY":proxy_url,
                 "https_proxy":proxy_url,"HTTPS_PROXY":proxy_url,
                 "all_proxy":"","ALL_PROXY":"","no_proxy":"","NO_PROXY":""}
            target=Path(temp)/"asset"
            with patch.dict(os.environ, env):
                try:
                    module.download(base+"/range", 6, self.digest, target,
                                    workers=1, chunk_size=2, timeout=1, deadline=5,
                                    proxy_mode="direct")
                except TypeError:
                    self.fail("Downloader has no explicit direct proxy mode")
            self.assertEqual(target.read_bytes(), b"abcdef")
            self.assertEqual(proxy.get_calls, [])

    def test_rate_limit_is_clear_and_not_retried(self):
        with local_source() as (server, base), tempfile.TemporaryDirectory() as temp:
            target=Path(temp)/"asset"
            with self.assertRaisesRegex(RuntimeError, "rate limit"):
                module.download(base+"/limited?signature=DO_NOT_LOG", 6, self.digest, target,
                                workers=1, chunk_size=6, timeout=1, deadline=5)
            self.assertEqual(len(server.get_calls), 1)
            self.assertFalse(target.exists())

    def test_deadline_stops_a_slow_continuous_response(self):
        with local_source() as (server, base), tempfile.TemporaryDirectory() as temp:
            target=Path(temp)/"asset"
            started=time.monotonic()
            with self.assertRaises(TimeoutError):
                module.download(base+"/slow", 6, self.digest, target,
                                workers=1, chunk_size=6, timeout=10, deadline=0.25)
            self.assertLess(time.monotonic()-started, 2)
            self.assertFalse(target.exists())

    def test_user_curl_configuration_cannot_enable_tracing(self):
        with local_source() as (server, base), tempfile.TemporaryDirectory() as temp:
            temp=Path(temp)
            config=temp/"curl-config"
            config.mkdir()
            trace=temp/"must-not-exist.trace"
            (config/".curlrc").write_text(f'trace-ascii = "{trace}"\n')
            with patch.dict(os.environ, {"CURL_HOME":str(config)}):
                module.download(base+"/range?signature=DO_NOT_LOG", 6, self.digest, temp/"asset",
                                workers=1, chunk_size=6, timeout=1, deadline=5)
            self.assertFalse(trace.exists(), "User .curlrc enabled a download trace outside the controlled logger")

if __name__ == "__main__":
    unittest.main()
