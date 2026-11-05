import importlib.util
from pathlib import Path
import socket
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "pilot.py"
spec = importlib.util.spec_from_file_location("pilot", SCRIPT) if SCRIPT.exists() else None
module = importlib.util.module_from_spec(spec) if spec else None
if spec:
    spec.loader.exec_module(module)

class IntegrityTest(unittest.TestCase):
    def test_modified_dependency_is_rejected_before_execution(self):
        self.assertIsNotNone(module, "Pinned pilot must verify dependency bytes before executing them")
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "archive"
            archive.write_bytes(b"unexpected replacement")
            with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                module.verify_file(archive, "0" * 64)

    def test_never_selects_a_port_owned_by_an_existing_server(self):
        self.assertTrue(callable(getattr(module, "fresh_local_port", None)), "Pilot must not reuse an unrelated running Apalache server")
        with socket.socket() as existing:
            existing.bind(("127.0.0.1", 0))
            existing.listen()
            self.assertNotEqual(module.fresh_local_port(), existing.getsockname()[1])

    def test_relocated_pilot_reads_only_its_code_dependencies(self):
        import hashlib
        import json
        import shutil
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            code=Path(tmp)/"independent-family/code"
            deps=code/"deps"
            deps.mkdir(parents=True)
            shutil.copyfile(SCRIPT,code/"pilot.py")
            for name in ("source.tar.gz","node.tar.xz"):
                (deps/name).write_bytes(b"pinned")
            digest=hashlib.sha256(b"pinned").hexdigest()
            (code/"dependencies.json").write_text(json.dumps({
                "aztec":{"archive":"deps/source.tar.gz","sha256":digest},
                "node":{"archive":"deps/node.tar.xz","sha256":digest},
                "apalache":{"archive":"deps/missing-backend.tgz","expected_sha256":digest}
            }))
            run=subprocess.run([sys.executable,"-B",str(code/"pilot.py"),
                                "--output",str(Path(tmp)/"native")],
                               cwd=Path(tmp),capture_output=True,text=True)
            self.assertNotEqual(run.returncode,0)
            self.assertIn(str(deps/"missing-backend.tgz"),run.stdout+run.stderr)
            self.assertNotIn("upstream-locks",run.stdout+run.stderr)

if __name__ == "__main__":
    unittest.main()
