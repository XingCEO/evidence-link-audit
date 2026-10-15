"""Regression tests use only synthetic in-memory and temporary source trees."""

import hashlib
from pathlib import Path
import tempfile
import unittest

from evidence_link_audit import CaptureError, analyze, audit_repository, capture_repository


def evidence(**overrides):
    return {"evidence": [{"id": "cache-lookup", "path": "src/cache.py", "start": 1, "end": 2, **overrides}]}


class SnapshotTests(unittest.TestCase):
    def test_snapshot_api_remains_available(self):
        report = analyze({"files": {"src/cache.py": "def read(key):\n    return cache.get(key)\n"}, **evidence()})
        self.assertTrue(report["valid"])
        self.assertEqual(report["checked"], 1)

    def test_stale_snippet_is_reported(self):
        report = analyze({"files": {"src/cache.py": "new\nvalue"}, **evidence(contains="old")})
        self.assertEqual(report["evidence"][0]["issues"], ["snippet_missing"])

    def test_snapshot_values_must_be_text(self):
        with self.assertRaises(ValueError):
            analyze({"files": {"src/cache.py": []}, **evidence()})


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "repository"
        (self.root / "src").mkdir(parents=True)
        self.source = self.root / "src/cache.py"
        self.source.write_bytes(b"def read(key):\n    return cache.get(key)\n")

    def capture(self, data=None):
        return capture_repository(self.root, evidence() if data is None else data)

    def test_capture_then_check(self):
        manifest = self.capture(evidence(contains="cache.get"))
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["evidence"][0]["sha256"], hashlib.sha256(self.source.read_bytes().rstrip(b"\n")).hexdigest())
        self.assertTrue(audit_repository(self.root, manifest)["valid"])
        self.assertEqual(manifest, self.capture(evidence(contains="cache.get")))

    def test_changed_cited_line_is_stale(self):
        manifest = self.capture()
        self.source.write_text("def read(key):\n    return cache[key]\n", encoding="utf-8")
        result = audit_repository(self.root, manifest)["evidence"][0]
        self.assertEqual(result["issues"], ["content_changed"])
        self.assertNotEqual(result["expected_sha256"], result["sha256"])

    def test_unselected_lines_do_not_invalidate_evidence(self):
        manifest = self.capture(evidence(start=2))
        self.source.write_text("# Changed first line\n    return cache.get(key)\n# Added third line\n", encoding="utf-8")
        self.assertTrue(audit_repository(self.root, manifest)["valid"])

    def test_insertion_before_range_invalidates_evidence(self):
        manifest = self.capture()
        self.source.write_text("# added\n" + self.source.read_text(encoding="utf-8"), encoding="utf-8")
        self.assertEqual(audit_repository(self.root, manifest)["evidence"][0]["issues"], ["content_changed"])

    def test_crlf_and_final_newline_do_not_change_hash(self):
        manifest = self.capture()
        self.source.write_bytes(b"def read(key):\r\n    return cache.get(key)")
        self.assertTrue(audit_repository(self.root, manifest)["valid"])

    def test_lone_cr_and_unicode_separator_are_content(self):
        self.source.write_bytes("one\rtwo\u2028three\nfour\n".encode())
        record = self.capture(evidence(start=1, end=1))["evidence"][0]
        self.assertEqual(record["sha256"], hashlib.sha256("one\rtwo\u2028three".encode()).hexdigest())

    def test_end_is_one_based_inclusive(self):
        self.source.write_text("first\nsecond\nthird\n", encoding="utf-8")
        manifest = self.capture(evidence(start=2, end=3, contains="second\nthird"))
        self.assertEqual(manifest["evidence"][0]["sha256"], hashlib.sha256(b"second\nthird").hexdigest())

    def test_check_requires_digest(self):
        report = audit_repository(self.root, evidence(contains="cache.get"))
        self.assertEqual(report["evidence"][0]["issues"], ["missing_digest"])

    def test_capture_refreshes_existing_hash_explicitly(self):
        manifest = self.capture()
        self.source.write_text("replacement\nline two\n", encoding="utf-8")
        refreshed = self.capture(manifest)
        self.assertNotEqual(manifest["evidence"][0]["sha256"], refreshed["evidence"][0]["sha256"])
        self.assertTrue(audit_repository(self.root, refreshed)["valid"])

    def test_failed_capture_does_not_emit_manifest(self):
        with self.assertRaises(CaptureError) as failure:
            self.capture(evidence(contains="absent"))
        self.assertEqual(failure.exception.report["evidence"][0]["issues"], ["snippet_missing"])

    def test_removed_file(self):
        manifest = self.capture()
        self.source.unlink()
        self.assertEqual(audit_repository(self.root, manifest)["evidence"][0]["issues"], ["missing_file"])

    def test_directory_is_not_a_source_file(self):
        report = audit_repository(self.root, evidence(path="src"), require_hashes=False)
        self.assertEqual(report["evidence"][0]["issues"], ["not_regular_file"])

    def test_invalid_utf8_is_a_finding(self):
        self.source.write_bytes(b"\xff\xfe")
        report = audit_repository(self.root, evidence(), require_hashes=False)
        self.assertEqual(report["evidence"][0]["issues"], ["invalid_utf8"])

    def test_ranges_cannot_refer_to_nonexistent_lines(self):
        for start, end in [(0, 1), (2, 1), (1, 3), (-1, 1)]:
            with self.subTest(start=start, end=end):
                report = audit_repository(self.root, evidence(start=start, end=end), require_hashes=False)
                self.assertEqual(report["evidence"][0]["issues"], ["invalid_line_range"])
        self.source.write_text("", encoding="utf-8")
        report = audit_repository(self.root, evidence(start=1, end=1), require_hashes=False)
        self.assertEqual(report["evidence"][0]["issues"], ["invalid_line_range"])

    def test_final_blank_line_can_be_cited(self):
        self.source.write_bytes(b"content\n\n")
        manifest = self.capture(evidence(start=2, end=2))
        self.assertEqual(manifest["evidence"][0]["sha256"], hashlib.sha256(b"").hexdigest())

    def test_invalid_path_forms_are_rejected(self):
        for path in ["../outside.py", "/etc/passwd", "src/../cache.py", "./src/cache.py", "src//cache.py", "C:/cache.py", "src\\cache.py", ".", "", "\x00"]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.capture(evidence(path=path))

    def test_symlink_cannot_escape_root(self):
        external = self.root.parent / "external.py"
        external.write_text("secret\nvalue\n", encoding="utf-8")
        self.source.unlink()
        self.source.symlink_to(external)
        report = audit_repository(self.root, evidence(), require_hashes=False)
        self.assertEqual(report["evidence"][0]["issues"], ["outside_repository"])
        self.assertNotIn("sha256", report["evidence"][0])

    def test_parent_directory_symlink_cannot_escape_root(self):
        external = self.root.parent / "elsewhere"
        external.mkdir()
        (external / "other.py").write_text("one\ntwo", encoding="utf-8")
        (self.root / "link").symlink_to(external, target_is_directory=True)
        report = audit_repository(self.root, evidence(path="link/other.py"), require_hashes=False)
        self.assertEqual(report["evidence"][0]["issues"], ["outside_repository"])

    def test_symlink_within_root_is_supported(self):
        (self.root / "alias.py").symlink_to(self.source)
        manifest = self.capture(evidence(path="alias.py"))
        self.assertTrue(audit_repository(self.root, manifest)["valid"])

    def test_symlink_loop_is_reported(self):
        self.source.unlink()
        self.source.symlink_to(self.source.name)
        report = audit_repository(self.root, evidence(), require_hashes=False)
        self.assertEqual(report["evidence"][0]["issues"], ["unreadable_file"])

    def test_invalid_manifest_contracts(self):
        cases = [[], {}, {"evidence": []}, evidence(id=""), evidence(start=True), evidence(end="2"), evidence(sha256="bad"), evidence(contains=[]), evidence(extra="typo"), {"schema_version": True, **evidence()}]
        duplicate = evidence()
        duplicate["evidence"].append(dict(duplicate["evidence"][0]))
        cases.append(duplicate)
        for data in cases:
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.capture(data)

    def test_missing_repository_is_input_error(self):
        with self.assertRaises(ValueError):
            capture_repository(self.root / "absent", evidence())
