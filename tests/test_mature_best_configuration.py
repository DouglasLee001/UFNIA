"""Check retained affine views and the removal of the optional footprint cache."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BIN = Path(os.environ.get("MATURE_BINARY", str(ROOT / "build/ufnia_ls/ufnia_mature")))
AFFINE_ENV = ("UFNIA_AFFINE_VIEWS", "UFNIA_AFFINE_FOOTPRINT_CACHE",
              "UFNIA_AFFINE_PROGRESS", "UFNIA_AFFINE_POINT_UNITS")


class MatureBestConfigurationTests(unittest.TestCase):
    def run_configuration(self, flags=None, parse=True):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            source = work / "synthetic.native"
            source.write_text("2\n1 ( = ( + x ( * -1 y ) ) 0 )\n0\n1\n( 1 )\n0\nUFNIA_OBSERVERS 1 0\n")
            model = work / "model.json"
            environment = {key: value for key, value in os.environ.items() if key not in AFFINE_ENV}
            environment.update(flags or {})
            command = [str(BIN), str(source), "--uf-observers", "--interaction", "joint",
                       "--scoring", "paws", "--seed", "0", "--seconds", "0",
                       "--model-output", str(model)]
            if parse:
                command.append("--parse-only")
            process = subprocess.run(command, capture_output=True, text=True, env=environment)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertFalse(model.exists())
            result = json.loads(process.stdout if parse else process.stderr.splitlines()[-1])
            progress = list(work.glob("affine-*.report.json"))
            return result, [json.loads(path.read_text()) for path in progress]

    def assert_no_footprint_cache_fields(self, result):
        self.assertFalse(any(key.startswith("affine_footprint_cache_") for key in result))

    def test_default_mature_enables_affine_views(self):
        result, progress = self.run_configuration()
        self.assertTrue(result["affine_views_enabled"])
        self.assertGreater(result["affine_components"], 0)
        self.assert_no_footprint_cache_fields(result)
        self.assertEqual(progress, [])

    def test_explicit_environment_can_disable_affine_views(self):
        result, progress = self.run_configuration({"UFNIA_AFFINE_VIEWS": "0"})
        self.assertFalse(result["affine_views_enabled"])
        self.assertEqual(result["affine_components"], 0)
        self.assert_no_footprint_cache_fields(result)
        self.assertEqual(progress, [])

    def test_removed_footprint_cache_flag_has_no_effect(self):
        default, _ = self.run_configuration()
        self.assert_no_footprint_cache_fields(default)
        for value in ("0", "1"):
            with self.subTest(value=value):
                flagged, _ = self.run_configuration({"UFNIA_AFFINE_FOOTPRINT_CACHE": value})
                self.assertEqual(flagged, default)

    def test_removed_point_flag_has_no_effect(self):
        default, _ = self.run_configuration()
        flagged, _ = self.run_configuration({"UFNIA_AFFINE_POINT_UNITS": "1"})
        self.assertEqual(flagged, default)
        self.assertFalse(any("point_units" in key for key in flagged))

    def test_progress_reports_affine_configuration_without_search(self):
        result, progress = self.run_configuration({"UFNIA_AFFINE_PROGRESS": "1"}, parse=False)
        self.assertTrue(result["affine_progress_enabled"])
        self.assertTrue(result["affine_views_enabled"])
        self.assert_no_footprint_cache_fields(result)
        self.assertEqual(result["steps"], 0)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(len(progress), 1)
        self.assertTrue(progress[0]["affine_views_enabled"])
        self.assert_no_footprint_cache_fields(progress[0])


if __name__ == "__main__":
    unittest.main()
