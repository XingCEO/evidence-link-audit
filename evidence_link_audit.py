"""Capture and verify file-and-line evidence against a local source tree."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
from typing import Any


class CaptureError(ValueError):
    """A capture failed; ``report`` contains the individual findings."""

    def __init__(self, report: dict[str, Any]):
        super().__init__("evidence could not be captured")
        self.report = report


def _records(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        raise ValueError("input must be a JSON object")
    if "schema_version" in data and (
        type(data["schema_version"]) is not int or data["schema_version"] != 1
    ):
        raise ValueError("unsupported schema_version; expected 1")
    records = data.get("evidence")
    if not isinstance(records, list) or not records:
        raise ValueError("evidence must be a nonempty array")
    normalized = []
    ids = set()
    allowed = {"id", "path", "start", "end", "contains", "sha256"}
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("each evidence record must be an object")
        if set(record) - allowed:
            raise ValueError("unknown evidence field: " + ", ".join(sorted(set(record) - allowed)))
        key, path = record.get("id"), record.get("path")
        if not isinstance(key, str) or not key.strip():
            raise ValueError("evidence id must be a nonempty string")
        if key in ids:
            raise ValueError("duplicate evidence id: " + key)
        ids.add(key)
        if not isinstance(path, str) or not path or "\x00" in path or "\\" in path:
            raise ValueError("evidence paths must be nonempty relative POSIX paths")
        parsed = PurePosixPath(path)
        if (
            parsed.is_absolute()
            or ".." in parsed.parts
            or str(parsed) != path
            or not parsed.parts
            or re.match(r"^[A-Za-z]:", path)
        ):
            raise ValueError("evidence paths must be canonical repository-relative POSIX paths")
        start, end = record.get("start"), record.get("end", record.get("start"))
        if type(start) is not int or type(end) is not int:
            raise ValueError("start and end must be integers")
        if "contains" in record and not isinstance(record["contains"], str):
            raise ValueError("contains must be a string")
        if "sha256" in record and (
            not isinstance(record["sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", record["sha256"]) is None
        ):
            raise ValueError("sha256 must be 64 lowercase hexadecimal characters")
        normalized.append({**record, "end": end})
    return normalized


def _lines(text: str) -> list[str]:
    """Use editor-style LF lines, normalizing CRLF but retaining a bare CR."""
    if not text:
        return []
    lines = text.replace("\r\n", "\n").split("\n")
    if lines[-1] == "":
        lines.pop()
    return lines


def _report(records, read_file, require_hashes=False):
    results = []
    for record in records:
        path, start, end = record["path"], record["start"], record["end"]
        result = {
            "id": record["id"], "path": path, "start": start, "end": end, "issues": []
        }
        if require_hashes and "sha256" not in record:
            result["issues"].append("missing_digest")
        if "sha256" in record:
            result["expected_sha256"] = record["sha256"]
        text, issue = read_file(path)
        if issue:
            result["issues"].append(issue)
        else:
            lines = _lines(text)
            if start < 1 or end < start or end > len(lines):
                result["issues"].append("invalid_line_range")
            else:
                excerpt = "\n".join(lines[start - 1:end])
                digest = hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
                result["sha256"] = digest
                if "sha256" in record and record["sha256"] != digest:
                    result["issues"].append("content_changed")
                if "contains" in record and record["contains"].replace("\r\n", "\n") not in excerpt:
                    result["issues"].append("snippet_missing")
        result["valid"] = not result["issues"]
        results.append(result)
    return {"valid": all(result["valid"] for result in results), "checked": len(results), "evidence": results}


def analyze(data: dict[str, Any]) -> dict[str, Any]:
    """Analyze explicit text snapshots (the original v0.1 library interface)."""
    records = _records(data)
    files = data.get("files")
    if not isinstance(files, dict) or any(not isinstance(value, str) for value in files.values()):
        raise ValueError("files must map paths to text strings")
    return _report(records, lambda path: (files[path], None) if path in files else (None, "missing_file"))


def audit_repository(repo: str | Path, data: dict[str, Any], *, require_hashes: bool = True) -> dict[str, Any]:
    """Check evidence against UTF-8 files within ``repo``, requiring captured hashes by default."""
    records = _records(data)
    try:
        root = Path(repo).resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ValueError("repository root does not exist or cannot be resolved") from error
    if not root.is_dir():
        raise ValueError("repository root must be a directory")
    cache = {}

    def read_file(path):
        if path in cache:
            return cache[path]
        try:
            resolved = (root / path).resolve(strict=True)
            if not resolved.is_relative_to(root):
                value = (None, "outside_repository")
            elif not resolved.is_file():
                value = (None, "not_regular_file")
            else:
                value = (resolved.read_bytes().decode("utf-8"), None)
        except FileNotFoundError:
            value = (None, "missing_file")
        except UnicodeDecodeError:
            value = (None, "invalid_utf8")
        except (OSError, RuntimeError):
            value = (None, "unreadable_file")
        cache[path] = value
        return value

    return _report(records, read_file, require_hashes)


def capture_repository(repo: str | Path, data: dict[str, Any]) -> dict[str, Any]:
    """Create a hash manifest, refreshing old hashes only on explicit capture."""
    records = _records(data)
    unhashed = [{key: value for key, value in record.items() if key != "sha256"} for record in records]
    report = audit_repository(repo, {"evidence": unhashed}, require_hashes=False)
    if not report["valid"]:
        raise CaptureError(report)
    captured = [dict(record, sha256=result["sha256"]) for record, result in zip(unhashed, report["evidence"])]
    return {"schema_version": 1, "evidence": captured}


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key: " + key)
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("nonstandard JSON value: " + value)


def main(argv=None):
    """Write deterministic JSON; check findings return 1, invalid requests return 2."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", help="legacy snapshot JSON path, or - for stdin")
    parser.add_argument("--repo", help="source-tree root; paths are relative to this directory")
    parser.add_argument("--evidence", help="evidence JSON path, or - for stdin")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--capture", action="store_true", help="emit current hashes as a reusable manifest")
    mode.add_argument("--check", action="store_true", help="verify a manifest against current files")
    args = parser.parse_args(argv)
    native = args.repo is not None or args.evidence is not None or args.capture or args.check
    if native:
        if args.input is not None or args.repo is None or args.evidence is None or not (args.capture or args.check):
            parser.error("use --repo ROOT --evidence JSON with exactly one of --capture or --check")
        input_path = args.evidence
    else:
        if args.input is None:
            parser.error("provide snapshot JSON, or use --repo ROOT --evidence JSON --capture/--check")
        input_path = args.input
    try:
        raw = sys.stdin.read() if input_path == "-" else Path(input_path).read_text(encoding="utf-8")
        data = json.loads(raw, object_pairs_hook=_json_object, parse_constant=_invalid_constant)
        if args.capture:
            result = capture_repository(args.repo, data)
        elif args.check:
            result = audit_repository(args.repo, data)
        else:
            result = analyze(data)
        status = 1 if args.check and not result["valid"] else 0
    except CaptureError as error:
        result, status = error.report, 2
    except (OSError, ValueError, TypeError, OverflowError, RecursionError) as error:
        print(f"Invalid input: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
