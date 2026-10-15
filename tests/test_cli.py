import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class CommandLineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "source.py").write_text("answer = 42\n", encoding="utf-8")
        self.records = {"evidence": [{"id": "answer", "path": "source.py", "start": 1}]}

    def run_cli(self, *args, data=None):
        return subprocess.run([sys.executable, "-m", "evidence_link_audit", *map(str, args)], input=data, capture_output=True, text=True)

    def test_capture_check_and_stale_exit_codes(self):
        capture = self.run_cli("--repo", self.root, "--evidence", "-", "--capture", data=json.dumps(self.records))
        self.assertEqual(capture.returncode, 0, capture.stderr)
        manifest = json.loads(capture.stdout)
        self.assertEqual(manifest["schema_version"], 1)
        checked = self.run_cli("--repo", self.root, "--evidence", "-", "--check", data=capture.stdout)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        (self.root / "source.py").write_text("answer = 43\n", encoding="utf-8")
        stale = self.run_cli("--repo", self.root, "--evidence", "-", "--check", data=capture.stdout)
        self.assertEqual(stale.returncode, 1, stale.stderr)
        self.assertEqual(json.loads(stale.stdout)["evidence"][0]["issues"], ["content_changed"])

    def test_manifest_path_is_independent_of_repo_root(self):
        manifest_path = self.root / "records.json"
        manifest_path.write_text(json.dumps(self.records), encoding="utf-8")
        capture = self.run_cli("--repo", self.root, "--evidence", manifest_path, "--capture")
        self.assertEqual(capture.returncode, 0, capture.stderr)

    def test_capture_failure_is_report_with_input_error_exit(self):
        self.records["evidence"][0]["contains"] = "not here"
        capture = self.run_cli("--repo", self.root, "--evidence", "-", "--capture", data=json.dumps(self.records))
        self.assertEqual(capture.returncode, 2)
        self.assertFalse(json.loads(capture.stdout)["valid"])
        self.assertNotIn("schema_version", json.loads(capture.stdout))

    def test_check_does_not_silently_capture_hash(self):
        run = self.run_cli("--repo", self.root, "--evidence", "-", "--check", data=json.dumps(self.records))
        self.assertEqual(run.returncode, 1)
        self.assertEqual(json.loads(run.stdout)["evidence"][0]["issues"], ["missing_digest"])

    def test_snapshot_example_preserves_analysis_completed_exit(self):
        run = self.run_cli("examples/input.json")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertFalse(json.loads(run.stdout)["valid"])

    def test_malformed_json_is_input_error(self):
        for data in ["{broken", "[]", '{"evidence": [], "evidence": []}', '{"evidence": NaN}', "[" * 2000 + "]" * 2000]:
            with self.subTest(data=data):
                run = self.run_cli("-", data=data)
                self.assertEqual(run.returncode, 2)
                self.assertEqual(run.stdout, "")
                self.assertNotIn("Traceback", run.stderr)

    def test_invalid_flag_combinations(self):
        for args in [[], ["--repo", str(self.root)], ["--capture"], ["--repo", str(self.root), "--evidence", "-"], ["examples/input.json", "--check"]]:
            with self.subTest(args=args):
                run = self.run_cli(*args)
                self.assertEqual(run.returncode, 2)
                self.assertNotIn("Traceback", run.stderr)

    def test_missing_input_is_clean_error(self):
        run = self.run_cli(self.root / "absent.json")
        self.assertEqual(run.returncode, 2)
        self.assertNotIn("Traceback", run.stderr)
