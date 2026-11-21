"""Verify that modified Python and client files only change documentation."""
from __future__ import annotations

import ast
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT).decode("utf-8-sig")


def python_shape(text):
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.body and isinstance(node.body[0], ast.Expr):
                value = node.body[0].value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    value.value = ""
    return ast.dump(tree, include_attributes=False)


def compare_client_tests(selected):
    """Compare Vitest failures with HEAD without replacing working-tree files."""
    import hashlib
    import os
    import shutil
    import tarfile
    import tempfile

    npm = shutil.which("npm")
    if not npm:
        raise RuntimeError("npm is required")
    dependencies = ROOT / "client_gui/node_modules"
    if not dependencies.is_dir():
        raise RuntimeError("Prepare client dependencies before comparing tests")

    def run(directory, report):
        process = subprocess.run(
            [npm, "test", "--", *selected, "--reporter=json", "--outputFile", str(report)],
            cwd=directory, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1500,
        )
        if not report.exists():
            raise RuntimeError(f"Vitest did not produce a report: {process.stderr[-4000:]}")
        data = json.loads(report.read_text(encoding="utf-8-sig"))
        if not data.get("numTotalTests"):
            raise RuntimeError("Vitest collected no tests; refusing a vacuous comparison")
        failures = set()
        root = str(directory.resolve()).replace("\\", "/").rstrip("/") + "/"
        for suite in data.get("testResults", []):
            name = suite["name"].replace("\\", "/")
            if name.lower().startswith(root.lower()):
                name = name[len(root):]
            assertions = suite.get("assertionResults", [])
            for assertion in assertions:
                if assertion.get("status") == "failed":
                    failures.add(name + "::" + assertion["fullName"])
            if suite.get("status") == "failed" and not assertions:
                failures.add(name + "::COLLECTION")
        summary = {key: data.get(key) for key in (
            "numTotalTestSuites", "numFailedTestSuites", "numTotalTests", "numFailedTests",
            "numPassedTests", "numPendingTests", "success")}
        summary["exit_code"] = process.returncode
        summary["failure_hash"] = hashlib.sha256("\n".join(sorted(failures)).encode()).hexdigest()
        return summary, failures

    with tempfile.TemporaryDirectory(prefix="flowgate-comment-check-") as temporary:
        temporary = Path(temporary)
        archive = temporary / "head.tar"
        with archive.open("wb") as output:
            subprocess.run(["git", "archive", "HEAD"], cwd=ROOT, stdout=output, check=True)
        baseline_root = temporary / "head"
        baseline_root.mkdir()
        with tarfile.open(archive) as source:
            source.extractall(baseline_root, filter="data")
        os.symlink(dependencies, baseline_root / "client_gui/node_modules", target_is_directory=True)
        baseline, before = run(baseline_root / "client_gui", temporary / "baseline.json")
        current, after = run(ROOT / "client_gui", temporary / "current.json")
        added, removed = sorted(after - before), sorted(before - after)
        print(json.dumps({"baseline": baseline, "current": current,
                          "added_failures": added, "removed_failures": removed}, ensure_ascii=True))
        return 1 if added or current["numTotalTests"] != baseline["numTotalTests"] else 0


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compare-client-tests", action="store_true")
    parser.add_argument("--client-test", action="append", default=[])
    args = parser.parse_args()
    if args.compare_client_tests:
        return compare_client_tests(args.client_test)
    paths = git("diff", "--name-only", "--diff-filter=M", "HEAD").splitlines()
    failures = []
    checked = 0
    for name in paths:
        path = Path(name)
        if "migrations" in path.parts or "fixtures" in path.parts:
            failures.append(name)
        if path.suffix != ".py":
            continue
        before = git("show", f"HEAD:{name}")
        after = (ROOT / name).read_text(encoding="utf-8-sig")
        checked += 1
        if python_shape(before) != python_shape(after):
            failures.append(name)
    print(json.dumps({"python_checked": checked, "changed_runtime_or_protected_files": failures}))
    client = subprocess.run(["node", "tools/korean_comment_syntax.cjs", "--verify-changes"], cwd=ROOT)
    return 1 if failures or client.returncode else 0


if __name__ == "__main__":
    raise SystemExit(main())
