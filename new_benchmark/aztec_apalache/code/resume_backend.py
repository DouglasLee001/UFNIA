#!/usr/bin/env python3
"""Resume the pinned backend after resolving its official asset redirect once."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.parse

import download_ranges


class ResolutionError(RuntimeError):
    """A safe error containing no redirect URL or proxy configuration."""


def resolve_asset(url, timeout=30, proxy_mode="direct"):
    if proxy_mode not in ("env", "direct") or timeout <= 0:
        raise ResolutionError("Invalid redirect request options")
    source = urllib.parse.urlsplit(url)
    if source.scheme != "https" or source.netloc != "api.github.com":
        raise ResolutionError("Expected the official HTTPS GitHub asset API")
    with tempfile.TemporaryDirectory(prefix="ufnia-apalache-redirect-") as temporary:
        headers = Path(temporary) / "headers"
        command = [
            "curl", "--disable", "--silent", "--head", "--ipv4",
            "--proto", "=https", "--connect-timeout", str(min(10, timeout)),
            "--max-time", str(timeout),
            "--header", "Accept: application/octet-stream",
            "--header", "Cache-Control: no-cache",
            "--header", "User-Agent: qf-ufnia-pilot",
            "--dump-header", str(headers), "--output", os.devnull,
            "--write-out", "%{http_code}",
        ]
        if proxy_mode == "direct":
            command += ["--noproxy", "*"]
        command += ["--url", url]
        try:
            run = subprocess.run(command, capture_output=True, text=True, timeout=timeout + 0.5)
        except subprocess.TimeoutExpired:
            raise ResolutionError("Official asset redirect request timed out") from None
        except OSError:
            raise ResolutionError("Could not start the official asset redirect request") from None
        if run.returncode:
            raise ResolutionError(f"Official asset redirect request failed (curl exit {run.returncode})")
        status = int(run.stdout.strip()) if run.stdout.strip().isdigit() else 0
        if status != 302:
            raise ResolutionError(f"Official asset API expected HTTP 302; received HTTP {status}")
        location = download_ranges.final_headers(headers.read_bytes()).get("location", "")
        try:
            destination = urllib.parse.urlsplit(location)
        except ValueError:
            raise ResolutionError("Official asset API returned an invalid redirect") from None
        if destination.scheme != "https" or destination.netloc != "release-assets.githubusercontent.com":
            raise ResolutionError("Official asset API returned an unexpected redirect host or scheme")
        return location


def resume_backend(code, *, workers=4, request_timeout=180, retries=5,
                   deadline=3600, proxy_mode="direct"):
    if not 1 <= workers <= 8 or request_timeout <= 0 or deadline <= 0 or retries < 0:
        raise ValueError("Invalid worker count, timeout, deadline, or retry limit")
    if proxy_mode not in ("env", "direct"):
        raise ValueError("Invalid proxy mode")
    until = time.monotonic() + deadline
    code = Path(code).resolve()
    backend = json.loads((code / "dependencies.json").read_text())["apalache"]
    target = code / backend["archive"]
    size = backend["expected_size"]
    sha256 = backend["expected_sha256"].lower()
    if target.is_file() and target.stat().st_size == size:
        if download_ranges.file_digest(target, until) == sha256:
            download_ranges.emit(event="verified_existing_archive", size=size, verified_sha256=sha256)
            return
    remaining = until - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Backend resume deadline exceeded before redirect resolution")
    location = resolve_asset(backend["official_api_asset"],
                             timeout=min(30, request_timeout, remaining), proxy_mode=proxy_mode)
    remaining = until - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Backend resume deadline exceeded before transfer")
    download_ranges.download(location, size, sha256, target, workers=workers,
                             timeout=request_timeout, retries=retries,
                             deadline=remaining, proxy_mode=proxy_mode)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--request-timeout", type=float, default=180)
    parser.add_argument("--retries", type=int, default=5)
    parser.add_argument("--deadline", type=float, default=3600)
    parser.add_argument("--proxy-mode", choices=("env", "direct"), default="direct")
    args = parser.parse_args(argv)
    try:
        resume_backend(Path(__file__).resolve().parent, workers=args.workers,
                       request_timeout=args.request_timeout, retries=args.retries,
                       deadline=args.deadline, proxy_mode=args.proxy_mode)
    except (ResolutionError, download_ranges.DownloadError, TimeoutError) as error:
        download_ranges.emit(event="failed", error=type(error).__name__, message=str(error))
        return 1
    except Exception as error:
        download_ranges.emit(event="failed", error=type(error).__name__,
                             message="Backend resume failed; check pinned metadata, local files, and transfer validation")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
