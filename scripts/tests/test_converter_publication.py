"""Deterministic concurrent-writer checks for CLI artifact publication."""

import contextlib
import io
from multiprocessing.process import BaseProcess
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import convert_ufnia


class PublicationTests(unittest.TestCase):
    def test_file_created_while_worker_runs_is_preserved_and_publication_rolls_back(self):
        for raced_artifact in ('native', 'metadata'):
            with self.subTest(raced_artifact=raced_artifact), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                source, output = root / 'input.smt2', root / 'output.nia'
                metadata = Path(str(output) + '.json')
                source.write_text('(assert true)\n(check-sat)\n')
                raced = output if raced_artifact == 'native' else metadata
                original_join = BaseProcess.join

                def completed_worker(process, timeout=None):
                    original_join(process, timeout)
                    if not process.is_alive():
                        raced.write_text('concurrent writer\n')

                with patch.object(BaseProcess, 'join', completed_worker), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    code = convert_ufnia.main([str(source), '-o', str(output)])
                self.assertEqual(code, 2, 'must reject a late-created artifact')
                self.assertEqual(raced.read_text(), 'concurrent writer\n')
                self.assertFalse((metadata if raced == output else output).exists())
                self.assertEqual(list(root.glob('.*.tmp')), [])
