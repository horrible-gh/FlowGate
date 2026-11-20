"""Content-based ratchet for Korean comments and developer documentation.

Use --write after translating comments. Existing baselines can only shrink;
initial creation is allowed only when no baseline exists. Runtime strings are
not comments. Quoted UI text is exempt only if an exact runtime literal exists.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tokenize

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "server/tests/architecture/korean_comment_baseline.txt"
HANGUL = re.compile(r"[\u3131-\u318e\uac00-\ud7a3]")
QUOTED = re.compile(r'''"([^"\n]+)"|(?<![A-Za-z0-9])'([^'\n]+)'|「([^」\n]+)」|\[([^\]\n]+)\]''')
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "dist", "__pycache__", ".pytest_cache"}
EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".sql", ".md"}
JS_EXTENSIONS = EXTENSIONS - {".py", ".sql", ".md"}
# These files hold localized product copy, templates, or their generator.
EXCLUDED = {
    "client_gui/src/app/i18n/ko.ts",
    "client_gui/src/shared/i18n/ko.ts",
    "tools/gen_seed_045.py",
}


def excluded(path: str) -> bool:
    return (path in EXCLUDED or Path(path).name == "messages.ts"
            or Path(path).name == "045_seed_global_design_templates.sql")


def source_paths(root: Path):
    """Visit tracked source areas, avoiding dependency and generated trees."""
    import os
    for directory, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not (Path(directory) / d).is_symlink())
        for name in sorted(files):
            path = Path(directory) / name
            rel = path.relative_to(root)
            if path.is_symlink() or path.suffix.lower() not in EXTENSIONS:
                continue
            if path.suffix.lower() == ".md" or rel.parts[0] in {"server", "tools"} or rel.parts[:2] in {
                ("client_gui", "src"), ("client_gui", "tests")
            }:
                yield path


def python_parts(text: str):
    comments = []
    literals = set()
    tree = ast.parse(text)
    doc_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.body and isinstance(node.body[0], ast.Expr):
                value = node.body[0].value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    doc_nodes.add(value)
                    segment = ast.get_source_segment(text, value)
                    comments.extend((value.lineno + index, line) for index, line in enumerate(segment.splitlines()))
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type == tokenize.COMMENT:
            comments.append((token.start[0], token.string))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node not in doc_nodes:
            literals.add(node.value)
    return comments, literals


def sql_parts(text: str):
    """Separate SQL comments from quoted data/identifiers, including dollar strings."""
    comments, literals = [], set()
    i, line = 0, 1
    while i < len(text):
        start, start_line = i, line
        if text.startswith("--", i):
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
            comments.append((line, text[start:i]))
        elif text.startswith("/*", i):
            i += 2
            depth = 1
            while i < len(text) and depth:
                if text.startswith("/*", i):
                    depth += 1
                    i += 2
                elif text.startswith("*/", i):
                    depth -= 1
                    i += 2
                else:
                    i += 1
            if depth:
                raise ValueError("Unterminated SQL comment")
            comments.extend((start_line + n, value) for n, value in enumerate(text[start:i].splitlines()))
        elif text[i] in "'\"`[":
            quote = text[i]
            close = "]" if quote == "[" else quote
            i += 1
            value = []
            while i < len(text):
                if text[i] == close:
                    if i + 1 < len(text) and text[i + 1] == close:
                        value.append(close)
                        i += 2
                    else:
                        i += 1
                        break
                elif text[i] == "\\" and i + 1 < len(text):
                    value.append(text[i + 1])
                    i += 2
                else:
                    value.append(text[i])
                    i += 1
            if quote == "'":
                literals.add("".join(value))
        elif text[i] == "$" and (match := re.match(r"\$(?:[A-Za-z_][A-Za-z_0-9]*)?\$", text[i:])):
            marker = match.group()
            end = text.find(marker, i + len(marker))
            if end < 0:
                raise ValueError("Unterminated SQL dollar string")
            literals.add(text[i + len(marker):end])
            i = end + len(marker)
        else:
            i += 1
        line += text[start:i].count("\n")
    return comments, literals


def scan_sources(sources: dict[str, str], js_parser=None):
    """Return (path, line, comment) findings; never exempt quoted prose by itself."""
    parsed = {}
    literals = set()
    javascript = {path: text for path, text in sources.items() if Path(path).suffix in JS_EXTENSIONS}
    if javascript:
        if js_parser is None:
            result = subprocess.run(
                ["node", str(Path(__file__).with_name("korean_comment_syntax.cjs"))],
                input=json.dumps(javascript), text=True, encoding="utf-8", capture_output=True, check=False,
            )
            if result.returncode:
                raise ValueError(f"JavaScript comment parser failed: {result.stderr}")
            parsed.update(json.loads(result.stdout))
        else:
            parsed.update(js_parser(javascript))
    for path, text in sources.items():
        suffix = Path(path).suffix
        try:
            if suffix == ".py":
                parsed[path] = python_parts(text)
            elif suffix == ".sql":
                parsed[path] = sql_parts(text)
            elif suffix == ".md":
                parsed[path] = (list(enumerate(text.splitlines(), 1)), [])
        except (SyntaxError, tokenize.TokenError, ValueError) as error:
            raise ValueError(f"Cannot scan {path}: {error}") from error
    for comments, values in parsed.values():
        literals.update(value for value in values if HANGUL.search(value))
    findings = []
    for path, (comments, _) in sorted(parsed.items()):
        if excluded(path):
            continue
        # Multiple comment tokens on one source line form a single baseline row.
        by_line = {}
        for number, comment in comments:
            by_line.setdefault(number, []).append(comment)
        for number, segments in sorted(by_line.items()):
            comment = " ".join(segments)
            # Documentation is prose, not a source-code quote exception.
            remaining = comment if Path(path).suffix == ".md" else QUOTED.sub(
                lambda match: "" if next(value for value in match.groups() if value is not None) in literals else match.group(),
                comment,
            )
            if HANGUL.search(remaining):
                findings.append((path, number, comment))
    return findings


def scan(root: Path):
    sources = {}
    for path in source_paths(root):
        text = path.read_text(encoding="utf-8-sig")
        # A file without Korean cannot contribute an offender or a Korean literal.
        if HANGUL.search(text):
            sources[path.relative_to(root).as_posix()] = text
    return scan_sources(sources)


def key(path: str, comment: str):
    return path, hashlib.sha256(" ".join(comment.split()).encode("utf-8")).hexdigest()


def counts(findings):
    return Counter(key(path, comment) for path, _, comment in findings)


def load_baseline(text: str):
    result = Counter()
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        path, digest, count = line.split("\t")
        if not re.fullmatch(r"[a-f0-9]{64}", digest) or int(count) <= 0 or (path, digest) in result:
            raise ValueError(f"Invalid baseline row: {line}")
        result[path, digest] = int(count)
    return result


def dump_baseline(current):
    return "# Korean comment baseline v1: path<TAB>sha256<TAB>count\n" + "".join(
        f"{path}\t{digest}\t{count}\n" for (path, digest), count in sorted(current.items())
    )


def differences(findings, baseline):
    current = counts(findings)
    added, removed = current - baseline, baseline - current
    messages = []
    for path, line, comment in findings:
        identity = key(path, comment)
        if added[identity]:
            messages.append(f"{path}:{line}: {comment}")
            added[identity] -= 1
    for (path, digest), count in sorted(removed.items()):
        messages.append(f"Stale baseline: {path} {digest} ({count}); run --write after translation")
    return messages


def update_baseline(path: Path, current):
    if path.exists() and current - load_baseline(path.read_text(encoding="utf-8")):
        raise ValueError("Refusing new Korean baseline entries; translate new comments before --write")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_baseline(current), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--report-json", action="store_true", help="List remaining comments for translation")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--prefix", default="", help="Path prefix for JSON reports only")
    parser.add_argument("--offset", type=int, default=0, help="Report pagination offset")
    parser.add_argument("--limit", type=int, default=30, help="Report pagination size")
    args = parser.parse_args()
    findings = scan(args.root)
    if args.report_json:
        selected = [item for item in findings if item[0].startswith(args.prefix)]
        print(json.dumps({"total": len(selected), "items": selected[args.offset:args.offset + args.limit]}, ensure_ascii=True))
        return 0
    path = args.root / BASELINE
    if args.write:
        update_baseline(path, counts(findings))
        print(f"Recorded {len(findings)} Korean comment/document lines")
        return 0
    messages = differences(findings, load_baseline(path.read_text(encoding="utf-8")))
    if messages:
        print("\n".join(messages))
        return 1
    print(f"Korean comment baseline matches ({len(findings)} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
