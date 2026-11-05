#!/usr/bin/env python3
"""Run one native verification command and retain its execution evidence."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a command is required after --")
    args.output.mkdir(parents=True, exist_ok=False)
    started = datetime.now(timezone.utc).isoformat()
    start = time.monotonic()
    timed_out = False
    with (args.output / "stdout.log").open("wb") as stdout, (args.output / "stderr.log").open("wb") as stderr:
        try:
            process = subprocess.Popen(command, stdout=stdout, stderr=stderr, start_new_session=True)
        except OSError as error:
            stderr.write((str(error) + "\n").encode())
            returncode = 127
        else:
            try:
                returncode = process.wait(timeout=args.timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                returncode = 124
    report = {"argv": command, "cwd": str(Path.cwd()), "started_utc": started,
              "elapsed_seconds": time.monotonic() - start, "returncode": returncode,
              "timed_out": timed_out, "status": "unknown"}
    (args.output / "execution.json").write_text(json.dumps(report, indent=2) + "\n")
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
