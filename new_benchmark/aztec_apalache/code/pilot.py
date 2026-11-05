#!/usr/bin/env python3
"""Reproduce one unmodified Aztec GovernanceProposer native verification session."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile


def verify_file(path, expected):
    with Path(path).open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != expected:
        raise ValueError(f"SHA256 mismatch: {path}")


def fresh_local_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Native export directory; defaults to a fresh /tmp directory")
    parser.add_argument("--timeout", type=float, default=240)
    args = parser.parse_args()
    code = Path(__file__).resolve().parent
    root = code
    locks = json.loads((code / "dependencies.json").read_text())
    source_archive = root / locks["aztec"]["archive"]
    node_archive = code / locks["node"]["archive"]
    backend_archive = code / locks["apalache"]["archive"]
    for path, digest in [(source_archive, locks["aztec"]["sha256"]), (node_archive, locks["node"]["sha256"]), (backend_archive, locks["apalache"]["expected_sha256"])]:
        verify_file(path, digest)
    if args.output is None:
        args.output = Path(tempfile.mkdtemp(prefix="ufnia-aztec-export-")) / "export"
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    work = Path(tempfile.mkdtemp(prefix="ufnia-aztec-pilot-"))
    (args.output / "temporary-runtime.txt").write_text(str(work) + "\n")
    for name, archive in [("source", source_archive), ("node", node_archive), ("quint-home/apalache-dist-0.62.4", backend_archive)]:
        destination = work / name
        destination.mkdir(parents=True)
        subprocess.run(["tar", "-xf", str(archive), "-C", str(destination)], check=True)
    source = next((work / "source").iterdir())
    for relative, digest in json.loads((code / "source-manifest.json").read_text()).items():
        verify_file(source / relative, digest)
    npm = work / "npm"
    npm.mkdir()
    for name in ["package.json", "package-lock.json"]:
        shutil.copy2(code / name, npm / name)
    env = dict(os.environ)
    env["PATH"] = str(work / "node/node-v22.23.4-linux-x64/bin") + os.pathsep + env["PATH"]
    env["QUINT_HOME"] = str(work / "quint-home")
    env["JVM_ARGS"] = "-Xmx8G"
    record = str(code / "record.py")
    setup = subprocess.run([sys.executable, record, "--output", str(args.output / "npm-ci"), "--timeout", "180", "--", "npm", "ci", "--prefix", str(npm), "--cache", str(work / "npm-cache"), "--ignore-scripts", "--no-audit", "--no-fund"], env=env)
    if setup.returncode:
        return setup.returncode
    config = {"debug": True, "write-intermediate": True, "checker": {"timeout-smt": 30, "no-deadlock": True, "smt-encoding": "oopsla19"}}
    config_path = args.output / "apalache-config.json"
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    run_dir = work / "native"
    run_dir.mkdir()
    native = subprocess.run([sys.executable, record, "--output", str(args.output / "verification"), "--timeout", str(args.timeout), "--", str(npm / "node_modules/.bin/quint"), "verify", "--max-steps=0", "--init=init_gp", "--invariant=governance_proposer_safety", "--apalache-version=0.62.4", "--server-endpoint=localhost:" + str(fresh_local_port()), "--verbosity=5", "--apalache-config=" + str(config_path), str(source / "spec/invariant_model.qnt")], cwd=run_dir, env=env)
    shutil.copytree(run_dir, args.output / "native-artifacts")
    for log in (args.output / "native-artifacts").rglob("log*.smt"):
        task = {"instance_id": "aztec_gp_safety_" + log.stem, "family": "aztec_apalache", "classification": "faithful_export", "raw_file": str(log.resolve()), "repository": locks["aztec"]["repository"], "source_commit": locks["aztec"]["commit"], "source_files": sorted(json.loads((code / "source-manifest.json").read_text())), "task_id": "governance_proposer_safety_depth0", "property": "governance_proposer_safety", "query_kind": "other", "scope_description": "Native Apalache SMT session for safety over arbitrary states satisfying init_gp. The session includes internal feasibility and property checks; each query role must be distinguished before publishing.", "bounds": {"max_steps": 0, "apalache_generate_gp_collection_bound": 4, "round_size_invariant_upper_bound": 10000, "constructor_step_round_size_bound_unused_at_depth_zero": 1000}, "input_file": locks["aztec"]["archive"], "input_hash": locks["aztec"]["sha256"], "license": "Apache-2.0", "uf_meaning": "Native oopsla19 function-cell encoding of the governance model maps; exact active UF signatures must be checked per extracted query.", "integer_nonlinearity_meaning": "Symbolic round-size div/mod in governance proposer constraints; presence must be confirmed per extracted query.", "source_ids": ["S03", "S05", "S06"], "split_group": "aztec-governance-proposer-safety", "limitations": ["Protocol-level Quint model, not a production-bytecode equivalence proof", "Depth zero with an arbitrary inductive state is not deployment reachability", "Whole native log contains multiple solver queries with different internal roles"], "transformation_relation": "identical_native_solver_session", "transformations": []}
        log.with_suffix(".task.json").write_text(json.dumps(task, indent=2) + "\n")
    print("Native export artifacts:", args.output)
    return native.returncode


if __name__ == "__main__":
    raise SystemExit(main())
