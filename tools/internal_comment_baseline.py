"""Reject new internal work references in source comments.

The baseline preserves existing exemptions; removing a reference never permits
adding a different one. Runtime strings and archived fixtures are not scanned.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import subprocess

import korean_comment_baseline as syntax

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "server/tests/architecture/internal_comment_baseline.txt"
# A bare number is ambiguous; recognize it only in an explicit work reference.
INTERNAL_ID = re.compile(
    r"\bflowgate\.(?:v02\w*|default)\.\d{4}(?:\.\d{4}-[A-Z]+)?\b"
    r"|\b(?:NR|CH|TR|T|D|R)\s?\d{4}\b|\bT#\d+\b"
    r"|\bgroup\s+\d{4}\b"
)


def excluded(path):
    parts = Path(path).parts
    return "fixtures" in parts or "migrations" in parts or syntax.excluded(path)


def scan_sources(sources, js_parser=None):
    parsed = {}
    javascript = {p: t for p, t in sources.items() if Path(p).suffix in syntax.JS_EXTENSIONS}
    if javascript:
        if js_parser is None:
            result = subprocess.run(
                ["node", str(Path(__file__).with_name("korean_comment_syntax.cjs"))],
                input=json.dumps(javascript), text=True, encoding="utf-8", capture_output=True, check=True,
            )
            parsed.update(json.loads(result.stdout))
        else:
            parsed.update(js_parser(javascript))
    for path, text in sources.items():
        if Path(path).suffix == ".py":
            parsed[path] = syntax.python_parts(text)
    findings = []
    for path, (comments, _) in sorted(parsed.items()):
        if excluded(path):
            continue
        by_line = {}
        for line, comment in comments:
            by_line.setdefault(line, []).append(comment)
        for line, segments in sorted(by_line.items()):
            comment = " ".join(segments)
            if INTERNAL_ID.search(comment):
                findings.append((path, line, comment))
    return findings


def scan(root, prefix=""):
    sources = {}
    for path in syntax.source_paths(root):
        relative = path.relative_to(root).as_posix()
        if not relative.startswith(prefix) or excluded(relative) or path.suffix not in {".py", *syntax.JS_EXTENSIONS}:
            continue
        text = path.read_text(encoding="utf-8-sig")
        if INTERNAL_ID.search(text):
            sources[relative] = text
    return scan_sources(sources)


def additions(findings, baseline):
    remaining = syntax.counts(findings) - baseline
    messages = []
    for path, line, comment in findings:
        identity = syntax.key(path, comment)
        if remaining[identity]:
            messages.append(f"{path}:{line}: {comment}")
            remaining[identity] -= 1
    return messages



def differences(findings, baseline):
    messages = additions(findings, baseline)
    for (path, digest), count in sorted((baseline - syntax.counts(findings)).items()):
        messages.append(f"Stale baseline: {path} {digest} ({count}); regenerate all --dump-baseline pages")
    return messages


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--report-json", action="store_true")
    mode.add_argument("--dump-baseline", action="store_true")
    mode.add_argument("--locations", action="store_true")
    parser.add_argument("--prefix", default="")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    if args.prefix and not (args.report_json or args.locations):
        parser.error("--prefix is only supported for reports")
    findings = scan(ROOT, args.prefix)
    if args.locations:
        import base64
        import zlib
        rows = {}
        for path, line, comment in findings[args.offset:args.offset + args.limit]:
            source_line = (ROOT / path).read_text(encoding="utf-8-sig").splitlines()[line - 1]
            column = source_line.find(comment)
            if column >= 0:
                rows.setdefault(path, []).append([line, column, len(comment)])
        print(base64.b64encode(zlib.compress(json.dumps(rows).encode("utf-8"))).decode("ascii"))
        return 0
    if args.report_json:
        selected = [row for row in findings if row[0].startswith(args.prefix)]
        print(json.dumps({"total": len(selected), "items": selected[args.offset:args.offset + args.limit]}))
        return 0
    baseline_path = ROOT / BASELINE
    baseline = syntax.load_baseline(baseline_path.read_text(encoding="utf-8")) if baseline_path.exists() else Counter()
    messages = additions(findings, baseline)
    if args.dump_baseline:
        if baseline_path.exists() and messages:
            raise ValueError("Refusing new internal-reference baseline entries")
        rows = syntax.dump_baseline(syntax.counts(findings)).replace("Korean comment", "Internal reference").splitlines(keepends=True)
        print("".join(rows[args.offset:args.offset + args.limit]), end="")
        return 0
    if not baseline_path.exists():
        raise ValueError("Internal-reference baseline is missing")
    messages = differences(findings, baseline)
    if messages:
        print("\n".join(messages))
        return 1
    print(f"Internal-reference baseline satisfied ({len(findings)} existing lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
