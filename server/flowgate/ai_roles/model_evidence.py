"""L-3 §2.3–2.7. Pure, conservative model evidence; never retains commands."""
import json
import re

MODEL = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._:/@-]*\Z")
BARE = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.:/=@+,")
FLAGS = {"claude": {"-p", "--print", "--dangerously-skip-permissions"},
         "codex": {"--skip-git-repo-check", "--json"},
         "copilot": {"--no-ask-user", "--allow-all"}}


def valid(value):
    return isinstance(value, str) and 1 <= len(value) <= 200 and MODEL.fullmatch(value) is not None


def shell_words(command, shell_kind):
    if shell_kind not in ("windows_cmd", "posix_sh"):
        return None
    words, cur, in_word, quoted = [], "", False, False
    for i, char in enumerate(command):
        nxt = command[i + 1:i + 2]
        if quoted:
            if char == '"':
                if nxt == '"':
                    return None
                quoted = False
                continue
            if char in "$`%!^" or ord(char) < 32 or ord(char) == 127:
                return None
            if char == "\\" and nxt and nxt in '"\\$`':
                return None
            cur += char
            continue
        if char in " \t":
            if in_word:
                words.append(cur)
                cur, in_word = "", False
            continue
        if char == '"':
            quoted, in_word = True, True
            continue
        if char == "\\":
            if shell_kind != "windows_cmd" or nxt == '"':
                return None
        elif char not in BARE:
            return None
        cur, in_word = cur + char, True
    if quoted:
        return None
    if in_word:
        words.append(cur)
    return words


def _form(kind, word):
    if word == "--model" or (kind == "codex" and word == "-m"):
        return "separate", ""
    if word.startswith("--model="):
        return "joined", word[len("--model="):]
    if kind == "codex":
        if word.startswith("-m") and not word.startswith("--"):
            return "joined", word[2:]
        if word in ("-c", "--config"):
            return "config_separate", ""
        if word.startswith("--config="):
            return "config_joined", word[len("--config="):]
        if word.startswith("-c") and not word.startswith("--"):
            return "config_joined", word[2:]
    return None


def requested(provider, command=None, shell_kind=None):
    def result(state, value=None, reason=None):
        return {"state": state, "value": value, "reason": reason}
    if provider.get("exec_type") == "api":
        value = (provider.get("api_model") or "").strip()
        if not value:
            return result("none", reason="api_model_empty")
        return result("value", value) if valid(value) else result("unreadable", reason="invalid_value")
    kind = provider.get("kind")
    if kind not in FLAGS:
        return result("not_parsed", reason="custom_command")
    words = shell_words(command if command is not None else provider.get("cli_command") or "", shell_kind)
    if words is None:
        return result("not_parsed", reason="shell_syntax")
    program = re.split(r"[/\\]", words[0])[-1] if words else ""
    if shell_kind == "windows_cmd":
        program = program.lower()
    program = re.sub(r"\.(exe|cmd|bat|ps1)$", "", program)
    if program != kind:
        return result("not_parsed", reason="wrapped_command")
    specs, i = [], 1
    while i < len(words):
        word = words[i]
        if word == "--":
            break
        form = _form(kind, word)
        if form is None:
            i += 1
            continue
        typ, value = form
        prev = words[i - 1]
        pf = _form(kind, prev)
        position = (i == 1 or not prev.startswith("-") or prev == "-" or "=" in prev
                    or prev in FLAGS[kind] or (pf is not None and pf[0] in ("joined", "config_joined")))
        nxt = words[i + 1] if i + 1 < len(words) else None
        if not position:
            key = (value.split("=", 1)[0] if typ == "config_joined" else
                   nxt.split("=", 1)[0] if nxt and "=" in nxt else None)
            if not typ.startswith("config") or key == "model":
                specs.append(("uncertain_position", None))
            i += 1
            continue
        if typ == "separate":
            specs.append((None, nxt) if nxt and not nxt.startswith("-") else ("missing_value", None))
            i += 2 if nxt and not nxt.startswith("-") else 1
        elif typ == "joined":
            specs.append((None, value) if value else ("missing_value", None))
            i += 1
        else:
            text = value if typ == "config_joined" else nxt
            if text and not text.startswith("-"):
                key, _, val = text.partition("=")
                if key == "model":
                    specs.append((None, val) if val else ("missing_value", None))
            i += 2 if typ == "config_separate" and text and not text.startswith("-") else 1
    if not specs:
        return result("none", reason="cli_no_model_arg")
    for reason in ("uncertain_position", "missing_value"):
        if any(r == reason for r, _v in specs):
            return result("unreadable", reason=reason)
    values = list(dict.fromkeys(v for _r, v in specs))
    if len(values) > 1:
        return result("unreadable", reason="ambiguous_model_args")
    return result("value", values[0]) if valid(values[0]) else result("unreadable", reason="invalid_value")


def capability(provider, req):
    if provider.get("exec_type") == "api":
        return "api_reported"
    if provider.get("kind") == "copilot":
        return "cli_reported"
    if provider.get("kind") in ("claude", "codex") and req["state"] == "value":
        return "cli_request_only"
    return "cli_none"  # ALLOW: evidence never changes admission.


def evidence(body):
    if not isinstance(body, dict):
        return "unreadable", None
    value = body.get("model")
    if value is None:
        return "absent", None
    return ("reported", value.strip()) if isinstance(value, str) else ("unreadable", None)


def confirmed(exec_type, kind, outcome):
    calls = {k: 0 for k in ("total", "reported", "absent", "unreadable", "failed")}
    values, truncated, bad_lines, complete = [], False, None, True
    def empty(reason, counts=None):
        return {"state": "unverifiable", "values": [], "truncated": False, "coverage": None,
                "calls": counts, "bad_lines": bad_lines, "reason": reason}
    events = outcome.get("model_calls", [])
    if exec_type != "api":
        output = outcome.get("stdout_full") or ""
        if not output.strip():
            return empty("no_result")
        if kind in ("claude", "codex"):
            return empty("kind_reports_no_model")
        if kind != "copilot":
            return empty("custom_command")
        events, bad_lines, json_lines = [], 0, 0
        for line in output.splitlines():
            if not line.strip().startswith("{"):
                continue
            try:
                ev = json.loads(line)
                if not isinstance(ev, dict):
                    raise ValueError()
            except (ValueError, TypeError):
                bad_lines += 1
                continue
            json_lines += 1
            if ev.get("type") == "assistant.message":
                events.append(evidence(ev.get("data")))
        if not events:
            return empty("report_unreadable" if not json_lines or bad_lines else "report_missing")
        complete = bool(outcome.get("process_exited_itself")) and bad_lines == 0
    for state, value in events:
        calls["total"] += 1
        if state == "reported" and not valid(value):
            state = "unreadable"
        calls[state] += 1
        if state == "reported" and value not in values:
            if len(values) < 8:
                values.append(value)
            else:
                truncated = True
    if values:
        return {"state": "confirmed", "values": values, "truncated": truncated,
                "coverage": "full" if calls["reported"] == calls["total"] and not truncated and complete else "partial",
                "calls": calls, "bad_lines": bad_lines, "reason": None}
    if exec_type == "api" and calls["total"] == calls["failed"]:
        return empty("no_result", calls)
    return empty("report_unreadable" if calls["unreadable"] else "report_missing", calls)


def compare(req, conf, actual=True):
    if not actual:
        status, mismatch = "not_called", False
    elif conf.get("state") == "confirmed":
        mismatch = req["state"] == "value" and any(v != req["value"] for v in conf["values"])
        status = ("partial" if conf["coverage"] == "partial" else
                  ("different" if mismatch else "same") if req["state"] == "value" else "confirmed_only")
    else:
        status, mismatch = ("requested_only" if req["state"] == "value" else "unidentifiable"), False
    return {"status": status, "mismatch_found": mismatch,
            "reconciled": status in ("same", "different", "confirmed_only")}
