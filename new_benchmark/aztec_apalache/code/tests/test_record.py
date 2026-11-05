import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "record.py"

class RecordTest(unittest.TestCase):
    def test_failed_command_preserves_complete_streams_and_status(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "record"
            command = [sys.executable, "-c", "import sys; print('full stdout'); print('full stderr', file=sys.stderr); sys.exit(7)"]
            result = subprocess.run([sys.executable, str(SCRIPT), "--output", str(output), "--timeout", "5", "--", *command], capture_output=True, text=True)
            self.assertEqual(result.returncode, 7, result.stderr)
            self.assertEqual((output / "stdout.log").read_text(), "full stdout\n")
            self.assertEqual((output / "stderr.log").read_text(), "full stderr\n")
            report = json.loads((output / "execution.json").read_text())
            self.assertEqual(report["argv"], command)
            self.assertEqual(report["returncode"], 7)
            self.assertFalse(report["timed_out"])
            self.assertGreaterEqual(report["elapsed_seconds"], 0)

    def test_missing_executable_keeps_failure_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "record"
            command = [str(Path(temp) / "not-installed")]
            result = subprocess.run([sys.executable, str(SCRIPT), "--output", str(output), "--timeout", "5", "--", *command], capture_output=True, text=True)
            self.assertEqual(result.returncode, 127, result.stderr)
            report = json.loads((output / "execution.json").read_text())
            self.assertEqual(report["returncode"], 127)
            self.assertIn("No such file", (output / "stderr.log").read_text())

if __name__ == "__main__":
    unittest.main()
