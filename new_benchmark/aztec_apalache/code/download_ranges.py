#!/usr/bin/env python3
"""Resumable, strict range downloads; never log proxy credentials or redirect URLs."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeout
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time
import urllib.parse


class DownloadError(RuntimeError):
    def __init__(self, message, *, retryable=False, http_status=None):
        super().__init__(message)
        self.retryable = retryable
        self.http_status = http_status


def emit(**fields):
    print(json.dumps(fields, sort_keys=True), flush=True)


def final_headers(data):
    result = {}
    for block in re.split(rb"\r?\n\r?\n", data):
        lines = block.splitlines()
        if lines and lines[0].startswith(b"HTTP/"):
            result = {}
            for line in lines[1:]:
                if b":" in line:
                    key, value = line.split(b":", 1)
                    result[key.decode("ascii", errors="ignore").lower()] = value.strip().decode("latin1")
    return result


def fetch_range(url, start, end, total, target, timeout, proxy_mode="env"):
    """Fetch one bounded range; only verified bytes can replace a completed part."""
    if proxy_mode not in ("env", "direct"):
        raise ValueError("proxy_mode must be env or direct")
    if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        raise ValueError("Only HTTP(S) download URLs are accepted")
    size = end-start+1
    if not 0 <= start <= end < total or timeout <= 0:
        raise ValueError("Invalid byte range or request timeout")
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    paths = []
    try:
        for suffix in (".body.partial", ".headers.partial"):
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=target.name+".", suffix=suffix, delete=False) as f:
                paths.append(Path(f.name))
        body, headers = paths
        command = [
            "curl", "--disable", "--silent", "--location", "--ipv4",
            "--proto", "=http,https",
            "--proto-redir", "=https" if url.startswith("https:") else "=http,https",
            "--connect-timeout", str(min(10, timeout)),
            "--max-time", str(timeout),
            "--max-filesize", str(size),
            "--header", "Accept: application/octet-stream",
            "--header", "Cache-Control: no-cache",
            "--header", "User-Agent: qf-ufnia-pilot",
            "--range", f"{start}-{end}",
            "--dump-header", str(headers), "--output", str(body),
            "--write-out", "%{http_code}",
        ]
        if proxy_mode == "direct":
            command += ["--noproxy", "*"]
        command += ["--url", url]
        try:
            run = subprocess.run(command, capture_output=True, text=True, timeout=timeout+0.5)
        except FileNotFoundError:
            raise DownloadError("curl is required for bounded IPv4 downloads") from None
        except subprocess.TimeoutExpired:
            raise DownloadError("Request deadline exceeded", retryable=True) from None
        info = final_headers(headers.read_bytes())
        status = int(run.stdout.strip()) if run.stdout.strip().isdigit() else 0
        if status >= 400:
            if status == 403 and info.get("x-ratelimit-remaining") == "0":
                raise DownloadError("GitHub API rate limit exhausted; wait for reset or use the official release URL",
                                    http_status=status)
            raise DownloadError(f"HTTP {status}", http_status=status,
                                retryable=status in (403, 408, 429) or status >= 500)
        # A small 200 response and an oversized 200 response are both invalid ranges.
        if status and status != 206:
            raise ValueError("Expected HTTP 206 for byte-range transfer")
        if run.returncode:
            raise DownloadError(f"Network transfer failed (curl exit {run.returncode}; proxy mode {proxy_mode})",
                                retryable=run.returncode in (5, 6, 7, 18, 28, 35, 52, 55, 56, 92))
        if status != 206:
            raise ValueError("Expected HTTP 206 for byte-range transfer")
        if info.get("content-range") != f"bytes {start}-{end}/{total}":
            raise ValueError("Unexpected Content-Range")
        if body.stat().st_size != size:
            raise ValueError("Unexpected byte-range payload size")
        body.replace(target)
    finally:
        for path in paths:
            path.unlink(missing_ok=True)


def file_digest(path, until):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while data := stream.read(1048576):
            if time.monotonic() >= until:
                raise TimeoutError("Download deadline exceeded during checksum verification")
            digest.update(data)
    return digest.hexdigest()


def download(url, total, sha256, target, workers=8, chunk_size=1048576,
             timeout=90, deadline=600, proxy_mode="env", retries=4, retry_delay=1):
    if not 1 <= workers <= 8 or total <= 0 or chunk_size <= 0:
        raise ValueError("Invalid worker count, asset size or chunk size")
    if timeout <= 0 or deadline <= 0 or retries < 0 or retry_delay < 0:
        raise ValueError("Invalid timeout, deadline or retry limit")
    if proxy_mode not in ("env", "direct"):
        raise ValueError("proxy_mode must be env or direct")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", sha256):
        raise ValueError("Expected a complete publisher SHA256")
    sha256 = sha256.lower()
    started = time.monotonic()
    until = started+deadline
    target = Path(target)
    if target.is_file() and target.stat().st_size == total and file_digest(target, until) == sha256:
        emit(event="verified_existing_archive", size=total, verified_sha256=sha256)
        return
    parts = target.with_suffix(target.suffix+".parts")
    parts.mkdir(parents=True, exist_ok=True)
    ranges = [(i, start, min(start+chunk_size-1, total-1)) for i, start in enumerate(range(0, total, chunk_size))]
    missing = []
    for item in ranges:
        i, start, end = item
        path = parts/f"{i:05d}.part"
        if not path.is_file() or path.stat().st_size != end-start+1:
            missing.append(item)
    emit(event="resume", cached_parts=len(ranges)-len(missing), missing_parts=len(missing),
         proxy_mode=proxy_mode, transport="curl_ipv4")
    stop = threading.Event()

    def transfer(item):
        index, start, end = item
        for attempt in range(retries+1):
            remaining = until-time.monotonic()
            if stop.is_set() or remaining <= 0:
                raise TimeoutError("Download deadline exceeded or another transfer failed")
            try:
                fetch_range(url, start, end, total, parts/f"{index:05d}.part",
                            min(timeout, remaining), proxy_mode=proxy_mode)
                return index
            except DownloadError as error:
                remaining = until-time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Download deadline exceeded") from None
                if not error.retryable or attempt >= retries:
                    raise
                emit(event="retry", part=index, attempt=attempt+1, http_status=error.http_status,
                     reason=type(error).__name__, proxy_mode=proxy_mode)
                pause = min(retry_delay*2**attempt, 10, remaining)
                if stop.wait(pause):
                    raise TimeoutError("Transfer cancelled after another failure") from None
        raise AssertionError("Unreachable retry state")

    if missing:
        pool = ThreadPoolExecutor(max_workers=workers)
        futures = [pool.submit(transfer, item) for item in missing]
        try:
            for future in as_completed(futures, timeout=max(0, until-time.monotonic())):
                index = future.result()
                emit(event="part", part=index, state="downloaded",
                     elapsed_seconds=round(time.monotonic()-started, 2))
        except FuturesTimeout:
            stop.set()
            raise TimeoutError("Download deadline exceeded") from None
        except BaseException:
            stop.set()
            raise
        finally:
            pool.shutdown(wait=True, cancel_futures=True)

    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=target.name+".", suffix=".assembled", delete=False) as output:
        temporary = Path(output.name)
        try:
            digest = hashlib.sha256()
            for index, _, _ in ranges:
                if time.monotonic() >= until:
                    raise TimeoutError("Download deadline exceeded during assembly")
                data = (parts/f"{index:05d}.part").read_bytes()
                digest.update(data)
                output.write(data)
            output.flush()
            if digest.hexdigest() != sha256:
                raise ValueError("Publisher SHA256 mismatch; completed parts preserved for diagnosis")
            if time.monotonic() >= until:
                raise TimeoutError("Download deadline exceeded before publication")
            output.close()
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    emit(event="verified_archive", size=total, verified_sha256=sha256)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--size", required=True, type=int)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--deadline", type=float, default=600)
    parser.add_argument("--request-timeout", type=float, default=30)
    parser.add_argument("--retries", type=int, default=4, help="Additional attempts per missing part")
    parser.add_argument("--proxy-mode", choices=("env", "direct"), default="env",
                        help="env respects proxy environment; direct explicitly bypasses it")
    args = parser.parse_args()
    try:
        download(args.url, args.size, args.sha256, args.output, workers=args.workers,
                 deadline=args.deadline, timeout=args.request_timeout,
                 proxy_mode=args.proxy_mode, retries=args.retries)
    except (DownloadError, TimeoutError, ValueError, OSError) as error:
        emit(event="failed", error=type(error).__name__, message=str(error))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
