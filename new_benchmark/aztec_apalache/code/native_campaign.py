#!/usr/bin/env python3
"""Reproduce pinned native Aztec slashing-proposer reachability campaigns.

Runtime preparation may install the pinned npm lock. Verification runs retain
raw sessions and evidence under /tmp; their native outcome does not label each
internal SMT query in those sessions.
"""
import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tarfile

PINNED_ARCHIVES = {
    "aztec": "e7b53de9a93320d7dd6417fb65b21ea48e49dd5b111f0621f2df1fb9a7da0b50",
    "node": "1805bcd35e6815559bd2eb97db438d3fdb0eb3a5e6a977b7c1badca76337e9ce",
    "apalache": "8ac8488422c30d552735bae11b92a2d014d8e02e8608702acc08f9a2d8a9abb2",
}
UPSTREAM_COMMIT = "6fee2e4f6dbbe632cf62b411e344e1a008642cd7"
SOURCE_PATH = "spec/slashing_proposer_machine.qnt"
QMIN_ANCHOR = "                _sp1_quorum_size > 1,\n"


def default_profiles():
    return [
        {"id": f"sp_quorum_q{q}_d{q}", "source": SOURCE_PATH,
         "invariant": "signal_quorum_ex", "qmin": q, "max_steps": q, "init": "init"}
        for q in (2, 3, 4, 6)
    ] + [
        {"id": f"sp_executed_q{q}_d{q+1}", "source": SOURCE_PATH,
         "invariant": "executed_ex", "qmin": q, "max_steps": q+1, "init": "init"}
        for q in (2, 3)
    ]


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _verify(path, expected):
    if sha256(path) != expected:
        raise ValueError(f"SHA256 mismatch: {path}")


def _write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _temporary_path(path):
    path = Path(path).resolve()
    if not path.is_relative_to(Path("/tmp")):
        raise ValueError(f"Runtime and native artifacts must be under /tmp: {path}")
    return path


def _load_locks(code):
    locks = json.loads((code / "dependencies.json").read_text())
    if locks["aztec"]["commit"] != UPSTREAM_COMMIT:
        raise ValueError("Unexpected Aztec source commit")
    for name, version in (("node", "22.23.4"), ("quint", "0.33.1"), ("apalache", "0.62.4")):
        if locks[name]["version"] != version:
            raise ValueError(f"Unexpected pinned {name} version")
    archives = {}
    for name in ("aztec", "node", "apalache"):
        metadata = locks[name]
        expected = metadata["expected_sha256" if name == "apalache" else "sha256"]
        if expected != PINNED_ARCHIVES[name]:
            raise ValueError(f"Unexpected pinned {name} archive hash")
        archive = (code / metadata["archive"]).resolve()
        if not archive.is_relative_to(code):
            raise ValueError(f"Dependency archive escapes code directory: {archive}")
        _verify(archive, expected)
        archives[name] = archive
    return locks, archives


def _archive_members(archive, destination):
    with tarfile.open(archive) as stream:
        for member in stream:
            target = (destination / member.name).resolve()
            if not target.is_relative_to(destination):
                raise ValueError(f"Unsafe archive path: {member.name}")
            if member.issym() or member.islnk():
                link = ((target.parent if member.issym() else destination) / member.linkname).resolve()
                if not link.is_relative_to(destination):
                    raise ValueError(f"Unsafe archive link: {member.name}")
            elif not member.isdir() and not member.isfile():
                raise ValueError(f"Unsupported archive entry: {member.name}")


def _extract(archive, destination):
    destination.mkdir(parents=True)
    _archive_members(archive, destination)
    with tarfile.open(archive) as stream:
        stream.extractall(destination, filter="data")


def _verify_extracted(archive, destination):
    _archive_members(archive, destination)
    with tarfile.open(archive) as stream:
        for member in stream:
            if member.isfile():
                target = destination / member.name
                source = stream.extractfile(member)
                expected = hashlib.file_digest(source, "sha256").hexdigest()
                _verify(target, expected)
            elif member.issym():
                target = destination / member.name
                if not target.is_symlink() or os.readlink(target) != member.linkname:
                    raise ValueError(f"Changed extracted dependency symlink: {target}")


def _source_root(runtime):
    roots = list((runtime / "source").iterdir())
    if len(roots) != 1 or not roots[0].is_dir():
        raise ValueError("Expected one pinned Aztec archive root")
    return roots[0]


def _environment(runtime):
    env = dict(os.environ)
    env["PATH"] = str(runtime / "node/node-v22.23.4-linux-x64/bin") + os.pathsep + env.get("PATH", "")
    env["QUINT_HOME"] = str(runtime / "quint-home")
    env["JVM_ARGS"] = "-Xmx4G"
    return env


def _validate_runtime(code, runtime, archives):
    source = _source_root(runtime)
    source_manifest = json.loads((code / "source-manifest.json").read_text())
    for relative, expected in source_manifest.items():
        path = (source / relative).resolve()
        if not path.is_relative_to(source):
            raise ValueError(f"Source manifest path escapes pinned root: {relative}")
        _verify(path, expected)
    for name in ("package.json", "package-lock.json"):
        if (runtime / "npm" / name).read_bytes() != (code / name).read_bytes():
            raise ValueError(f"Pinned npm lock/input mismatch: {name}")
    for name, destination in (("node", runtime / "node"), ("apalache", runtime / "quint-home/apalache-dist-0.62.4")):
        _verify_extracted(archives[name], destination)
    package = runtime / "npm/node_modules/@informalsystems/quint/package.json"
    if json.loads(package.read_text())["version"] != "0.33.1":
        raise ValueError("Installed Quint package version mismatch")
    command = [str(runtime / "npm/node_modules/.bin/quint"), "--version"]
    version = subprocess.run(command, env=_environment(runtime), capture_output=True, text=True, timeout=20)
    if version.returncode or not re.search(r"(?<![0-9])0[.]33[.]1(?![0-9])", version.stdout + version.stderr):
        raise ValueError(f"Quint --version did not confirm pinned 0.33.1: {version.stdout}{version.stderr}")
    return {
        "code": str(code), "source_root": str(source),
        "dependencies_sha256": sha256(code / "dependencies.json"),
        "source_manifest_sha256": sha256(code / "source-manifest.json"),
        "package_lock_sha256": sha256(code / "package-lock.json"),
        "record_sha256": sha256(code / "record.py"),
        "archive_sha256": dict(PINNED_ARCHIVES), "quint_version": version.stdout.strip(),
        "node_version": "22.23.4", "apalache_version": "0.62.4",
    }


def prepare_runtime(code, work):
    """Validate pins, extract dependencies and install npm once; return runtime root.

    Passing an existing pilot/scout runtime explicitly adopts it only after
    verifying the complete pinned source manifest, npm inputs, extracted backend
    and Node files, and the actual Quint CLI version.
    """
    code = Path(code).resolve()
    work = _temporary_path(work)
    _, archives = _load_locks(code)
    work.mkdir(parents=True, exist_ok=True)
    destinations = {"aztec": work / "source", "node": work / "node",
                    "apalache": work / "quint-home/apalache-dist-0.62.4"}
    for name, destination in destinations.items():
        if not destination.exists():
            _extract(archives[name], destination)
    npm = work / "npm"
    if not npm.exists():
        npm.mkdir()
        for name in ("package.json", "package-lock.json"):
            shutil.copy2(code / name, npm / name)
    else:
        for name in ("package.json", "package-lock.json"):
            if (npm / name).read_bytes() != (code / name).read_bytes():
                raise ValueError(f"Pinned npm lock/input mismatch: {name}")
    if not (npm / "node_modules/.bin/quint").exists():
        record = [sys.executable, str(code / "record.py"), "--output", str(work / "npm-ci"),
                  "--timeout", "180", "--", "npm", "ci", "--prefix", str(npm),
                  "--cache", str(work / "npm-cache"), "--ignore-scripts", "--no-audit", "--no-fund"]
        result = subprocess.run(record, cwd=work, env=_environment(work))
        execution = json.loads((work / "npm-ci/execution.json").read_text())
        if result.returncode or execution["returncode"] or execution["timed_out"]:
            raise RuntimeError(f"Pinned npm ci failed; evidence: {work / 'npm-ci'}")
    metadata = _validate_runtime(code, work, archives)
    _write_json(work / "native-runtime.json", metadata)
    return work


def _validate_profile(profile):
    profile = dict(profile)
    profile.setdefault("source", SOURCE_PATH)
    profile.setdefault("init", "init")
    profile.setdefault("encoding", "oopsla19")
    profile.setdefault("qmin", 2)
    if profile["qmin"] is None:
        profile["qmin"] = 2
    if profile["source"] != SOURCE_PATH or profile["init"] != "init":
        raise ValueError("Only the pinned empty-state slashing proposer model is supported")
    if profile["invariant"] not in ("signal_quorum_ex", "executed_ex"):
        raise ValueError("Unsupported native witness property")
    if profile["encoding"] not in ("oopsla19", "funArrays", "arrays"):
        raise ValueError("Unsupported native SMT encoding")
    for key in ("qmin", "max_steps"):
        if type(profile[key]) is not int or profile[key] < (2 if key == "qmin" else 0):
            raise ValueError(f"Invalid profile {key}")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", profile["id"]):
        raise ValueError("Invalid profile id")
    return profile


def _port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _native_outcome(stdout, stderr, execution):
    lines = (stdout + "\n" + stderr).splitlines()
    if execution["timed_out"]:
        return {"status": "timeout", "meaning": "native_run_timed_out", "evidence": []}
    errors = [line for line in lines if re.search(
        r"RuntimeError|SyntaxError|TypeError|OutOfMemoryError|Exception in thread|INTERNAL ERROR|^Error:", line)]
    if errors:
        return {"status": "error", "meaning": "native_failure", "evidence": errors}
    unknown = [line for line in lines if re.search(r"solver returned unknown|SMT.*timeout|SMT.*unknown", line, re.I)]
    if unknown:
        return {"status": "unknown", "meaning": "native_result_unresolved", "evidence": unknown}
    witnesses = [line for line in lines if re.search(
        r"Invariant .+ is violated|Found (?:a counterexample|an example execution)|\[violation\]", line, re.I)]
    if witnesses:
        return {"status": "sat", "meaning": "bounded_reachability_witness", "evidence": witnesses}
    if execution["returncode"]:
        return {"status": "error", "meaning": "native_nonzero_exit", "evidence": []}
    complete = [line for line in lines if re.search(
        r"No (?:violation|counterexample) found|All invariants hold|No error has been found", line, re.I)]
    if complete:
        return {"status": "unsat", "meaning": "no_witness_within_bound", "evidence": complete}
    return {"status": "unknown", "meaning": "no_explicit_native_result", "evidence": []}


def run_case(runtime, profile, output, timeout_seconds=120, *, python=sys.executable, smt_timeout_seconds=30):
    """Record one native run, returning provenance, task, outcome and artifact data."""
    runtime = _temporary_path(runtime)
    output = _temporary_path(output)
    profile = _validate_profile(profile)
    if timeout_seconds <= 0 or smt_timeout_seconds <= 0:
        raise ValueError("Timeouts must be positive")
    recorded_runtime = json.loads((runtime / "native-runtime.json").read_text())
    code = Path(recorded_runtime["code"]).resolve()
    locks, archives = _load_locks(code)
    verified_runtime = _validate_runtime(code, runtime, archives)
    if recorded_runtime != verified_runtime:
        raise ValueError("Runtime pins or source manifest changed after preparation")
    output.mkdir(parents=True, exist_ok=False)
    source_root = _source_root(runtime)
    private_source = output / "source"
    shutil.copytree(source_root, private_source, symlinks=True)
    source_file = private_source / profile["source"]
    original = source_file.read_bytes()
    text = original.decode()
    if text.count(QMIN_ANCHOR) != 1:
        raise ValueError("Pinned constructor quorum anchor must occur exactly once")
    if profile["qmin"] > 2:
        text = text.replace(QMIN_ANCHOR, QMIN_ANCHOR + f"                _sp1_quorum_size >= {profile['qmin']},\n")
        source_file.write_text(text)
    effective = source_file.read_bytes()
    classification = "faithful_export" if profile["qmin"] == 2 else "application_derived"
    diff = "".join(difflib.unified_diff(original.decode().splitlines(True), effective.decode().splitlines(True),
                                      fromfile="upstream/" + profile["source"], tofile="campaign/" + profile["source"]))
    diff_path = output / "source.diff"
    diff_path.write_text(diff)
    config = {"debug": True, "write-intermediate": True, "checker": {
        "timeout-smt": smt_timeout_seconds, "no-deadlock": True, "smt-encoding": profile["encoding"]}}
    config_path = output / "apalache-config.json"
    _write_json(config_path, config)
    native_dir = output / "native"
    native_dir.mkdir()
    command = [str(runtime / "npm/node_modules/.bin/quint"), "verify",
               f"--max-steps={profile['max_steps']}", "--init=" + profile["init"],
               "--invariant=" + profile["invariant"], "--apalache-version=0.62.4",
               f"--server-endpoint=localhost:{_port()}", "--verbosity=5",
               "--apalache-config=" + str(config_path), str(source_file)]
    verification = output / "verification"
    env = _environment(runtime)
    recorded_command = [str(python), str(code / "record.py"), "--output", str(verification),
                        "--timeout", str(timeout_seconds), "--", *command]
    process = subprocess.run(recorded_command, cwd=native_dir, env=env)
    execution = json.loads((verification / "execution.json").read_text())
    execution["recorder_returncode"] = process.returncode
    stdout_path, stderr_path = verification / "stdout.log", verification / "stderr.log"
    outcome = _native_outcome(stdout_path.read_text(errors="replace"), stderr_path.read_text(errors="replace"), execution)
    source_info = {"repository": locks["aztec"]["repository"], "commit": UPSTREAM_COMMIT,
                   "path": profile["source"], "original_sha256": hashlib.sha256(original).hexdigest(),
                   "effective_sha256": hashlib.sha256(effective).hexdigest(),
                   "manifest_sha256": verified_runtime["source_manifest_sha256"],
                   "effective_file": str(source_file), "diff": str(diff_path)}
    bounds = {"max_steps": profile["max_steps"], "minimum_quorum_size": profile["qmin"],
              "round_size_invariant_upper_bound": 10000,
              "rollup_count": 1, "slashing_proposer_count": 1, "payload_count": 3,
              "slot_upper_bound": str(2**256 - 1)}
    limitations = [
        "Protocol-level Quint model, not a production-bytecode equivalence proof",
        "Reachability is relative to the source environment, which may change slots non-monotonically between transactions",
        "Native sessions include internal feasibility and property checks; identify each query role before publishing",
    ]
    task = {
        "instance_id": profile["id"], "family": "aztec_apalache", "classification": classification,
        "repository": locks["aztec"]["repository"], "source_commit": UPSTREAM_COMMIT,
        "source_files": sorted(json.loads((code / "source-manifest.json").read_text())),
        "task_id": profile["id"], "property": profile["invariant"], "query_kind": "reachability",
        "scope_description": "Native bounded reachability witness from the pinned slashing proposer model's empty round accounting",
        "bounds": bounds, "input_file": locks["aztec"]["archive"], "input_hash": locks["aztec"]["sha256"],
        "license": "Apache-2.0", "split_group": "aztec-slashing-proposer-progress",
        "native_encoding": profile["encoding"],
        "uf_meaning": "Rollup, round accounting and payload signal-count maps are encoded natively; actual positive-arity UF signatures and QF_UFNIA eligibility must be established from each active query AST",
        "integer_nonlinearity_meaning": "Source has symbolic ROUND_SIZE division and shared round/slot arithmetic; actual surviving nonlinearity must be checked per active query",
        "source_ids": ["S03", "S05", "S06"], "limitations": limitations,
        "transformation_relation": "identical_native_solver_session" if classification == "faithful_export" else "constructor_input_under_approximation",
        "transformations": [] if classification == "faithful_export" else [
            {"kind": "restricted_constructor_inputs", "predicate": f"_sp1_quorum_size >= {profile['qmin']}",
             "retained_original_predicate": "_sp1_quorum_size > 1", "diff_file": str(diff_path)}],
    }
    sessions = sorted(native_dir.rglob("log*.smt"))
    for session in sessions:
        session_task = dict(task, raw_file=str(session))
        _write_json(session.with_suffix(".task.json"), session_task)
    artifacts = {"output": str(output), "source_file": str(source_file), "source_diff": str(diff_path),
                 "config": str(config_path), "native_output_dir": str(native_dir),
                 "stdout": str(stdout_path), "stderr": str(stderr_path),
                 "execution": str(verification / "execution.json"),
                 "raw_sessions": list(map(str, sessions)), "report": str(output / "run.json")}
    hashes = {str(path): sha256(path) for path in sorted(output.rglob("*")) if path.is_file()}
    report = {
        "schema_version": 1, "profile": profile, "task": task, "source": source_info, "bounds": bounds,
        "runtime": verified_runtime, "native_outcome": outcome, "execution": execution,
        "invocation": {"command": command, "recorded_command": recorded_command, "cwd": str(native_dir),
                       "environment": {key: env[key] for key in ("PATH", "QUINT_HOME", "JVM_ARGS")},
                       "timeout_seconds": timeout_seconds, "config_sha256": sha256(config_path)},
        "artifacts": artifacts, "artifact_hashes": hashes,
    }
    report["run_sha256"] = hashlib.sha256(json.dumps(report, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    _write_json(output / "run.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--profile", choices=[p["id"] for p in default_profiles()], required=True)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--smt-timeout", type=int, default=30)
    args = parser.parse_args()
    runtime = prepare_runtime(Path(__file__).resolve().parent, args.runtime)
    profile = next(p for p in default_profiles() if p["id"] == args.profile)
    report = run_case(runtime, profile, args.output, args.timeout, smt_timeout_seconds=args.smt_timeout)
    print(json.dumps({"report": report["artifacts"]["report"], "native_outcome": report["native_outcome"]}, indent=2))
    return 0 if report["native_outcome"]["status"] == "sat" else 1


if __name__ == "__main__":
    raise SystemExit(main())
