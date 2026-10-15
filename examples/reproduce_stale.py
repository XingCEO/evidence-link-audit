"""Exercise the installed CLI on a disposable synthetic source tree."""

import json
from pathlib import Path
import shutil
import subprocess
import tempfile


examples = Path(__file__).resolve().parent
with tempfile.TemporaryDirectory(prefix="evidence-demo-") as directory:
    root = Path(directory) / "repository"
    shutil.copytree(examples / "repository", root)
    capture = subprocess.run(
        ["evidence-link-audit", "--repo", str(root), "--evidence", str(examples / "evidence.json"), "--capture"],
        capture_output=True, text=True, check=True,
    )
    manifest = Path(directory) / "evidence.lock.json"
    manifest.write_text(capture.stdout, encoding="utf-8")
    command = ["evidence-link-audit", "--repo", str(root), "--evidence", str(manifest), "--check"]
    clean = subprocess.run(command, capture_output=True, text=True)
    if clean.returncode != 0 or not json.loads(clean.stdout)["valid"]:
        raise RuntimeError("freshly captured evidence did not verify: " + clean.stderr)
    source = root / "src/cache.py"
    source.write_text(source.read_text(encoding="utf-8").replace("return cache.get(key)", "return cache[key]"), encoding="utf-8")
    stale = subprocess.run(command, capture_output=True, text=True)
    report = json.loads(stale.stdout)
    if stale.returncode != 1 or report["evidence"][0]["issues"] != ["content_changed", "snippet_missing"]:
        raise RuntimeError("modified evidence was not rejected: " + stale.stderr)
    print(json.dumps({"fresh_check_exit": clean.returncode, "stale_check_exit": stale.returncode, "issues": report["evidence"][0]["issues"]}, indent=2))
