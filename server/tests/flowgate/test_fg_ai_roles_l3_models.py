"""L-3 M-1~M-26: model evidence is independent of provider labels and execution policy."""
import json
import pytest
from flowgate.ai_roles import model_evidence as m


@pytest.mark.parametrize("kind,command,shell,state,value,reason", [
    ("claude", 'claude --model "opus-5" -p -', "posix_sh", "value", "opus-5", None),
    ("claude", 'claude --model="opus-5" -p -', "posix_sh", "value", "opus-5", None),
    ("claude", 'claude -p "explain --model fake-model syntax"', "posix_sh", "none", None, "cli_no_model_arg"),
    ("claude", 'claude -p -', "posix_sh", "none", None, "cli_no_model_arg"),
    ("claude", 'claude --model', "posix_sh", "unreadable", None, "missing_value"),
    ("claude", 'claude --model -p -', "posix_sh", "unreadable", None, "missing_value"),
    ("claude", 'claude --model= -p -', "posix_sh", "unreadable", None, "missing_value"),
    ("claude", 'claude -p - -- --model x', "posix_sh", "none", None, "cli_no_model_arg"),
    ("claude", "claude -p '--model x'", "posix_sh", "not_parsed", None, "shell_syntax"),
    ("claude", 'claude --model $M', "posix_sh", "not_parsed", None, "shell_syntax"),
    ("claude", 'claude --model %M%', "windows_cmd", "not_parsed", None, "shell_syntax"),
    ("claude", 'claude --model x > out.txt', "posix_sh", "not_parsed", None, "shell_syntax"),
    ("claude", 'claude --model "a""b"', "windows_cmd", "not_parsed", None, "shell_syntax"),
    ("claude", 'claude --model "x', "posix_sh", "not_parsed", None, "shell_syntax"),
    ("claude", 'npx claude --model x', "posix_sh", "not_parsed", None, "wrapped_command"),
    ("claude", 'A=1 claude --model x', "posix_sh", "not_parsed", None, "wrapped_command"),
    ("claude", 'cmd /c claude --model x', "windows_cmd", "not_parsed", None, "wrapped_command"),
    ("claude", r'"C:\Program Files\Claude\claude.exe" --model x', "windows_cmd", "value", "x", None),
    ("claude", r'C:\tools\claude.exe --model x', "windows_cmd", "value", "x", None),
    ("claude", r'C:\tools\claude.exe --model x', "posix_sh", "not_parsed", None, "shell_syntax"),
    ("claude", 'claude --append-system-prompt --model x', "posix_sh", "unreadable", None, "uncertain_position"),
    ("claude", 'claude --dangerously-skip-permissions --model x', "posix_sh", "value", "x", None),
    ("codex", 'codex --model gpt-a exec -m gpt-b -', "posix_sh", "unreadable", None, "ambiguous_model_args"),
    ("codex", 'codex -c model=gpt-a', "posix_sh", "value", "gpt-a", None),
    ("codex", 'codex -cmodel=gpt-a', "posix_sh", "value", "gpt-a", None),
    ("codex", 'codex --config=model=gpt-a', "posix_sh", "value", "gpt-a", None),
    ("codex", 'codex -mgpt-a', "posix_sh", "value", "gpt-a", None),
    ("codex", 'codex -c model_reasoning_effort=high', "posix_sh", "none", None, "cli_no_model_arg"),
    ("codex", 'codex --unknown -c model', "posix_sh", "none", None, "cli_no_model_arg"),
    ("codex", 'codex --unknown --config model', "posix_sh", "none", None, "cli_no_model_arg"),
    ("codex", 'codex --unknown -c model=gpt-a', "posix_sh", "unreadable", None, "uncertain_position"),
    ("codex", 'codex --unknown -c model_reasoning_effort=high', "posix_sh", "none", None, "cli_no_model_arg"),
    ("codex", 'codex --json --model gpt-a --model gpt-a', "posix_sh", "value", "gpt-a", None),
    ("custom", 'secret command --model fake', "posix_sh", "not_parsed", None, "custom_command"),
])
def test_m_cli(kind, command, shell, state, value, reason):
    assert m.requested({"exec_type": "cli", "kind": kind}, command, shell) == dict(state=state, value=value, reason=reason)


@pytest.mark.parametrize("body,state,value", [({}, "absent", None), ({"model": None}, "absent", None),
    ([], "unreadable", None), ({"model": 7}, "unreadable", None), ({"model": " x "}, "reported", "x")])
def test_m_response_evidence(body, state, value):
    assert m.evidence(body) == (state, value)


@pytest.mark.parametrize("events,status,mismatch,coverage", [
    ([("reported", "x")], "same", False, "full"),
    ([("reported", "y")], "different", True, "full"),
    ([("reported", "x"), ("absent", None)], "partial", False, "partial"),
    ([("reported", "y"), ("absent", None)], "partial", True, "partial"),
    ([("reported", "x"), ("failed", None)], "partial", False, "partial"),
    ([("reported", "x"), ("reported", "y")], "different", True, "full"),
    ([("failed", None), ("reported", "x")], "partial", False, "partial"),
    ([("reported", "x"), ("reported", "bad value")], "partial", False, "partial"),
])
def test_m_api_full_and_partial(events, status, mismatch, coverage):
    conf = m.confirmed("api", "openai", {"model_calls": events})
    comp = m.compare({"state": "value", "value": "x"}, conf)
    assert conf["coverage"] == coverage
    assert comp == {"status": status, "mismatch_found": mismatch, "reconciled": coverage == "full"}
    assert sum(conf["calls"][k] for k in ("reported", "absent", "unreadable", "failed")) == len(events)


def test_m_cap_and_unverifiable():
    conf = m.confirmed("api", "custom", {"model_calls": [("reported", f"m{i}") for i in range(9)]})
    assert len(conf["values"]) == 8 and conf["truncated"] and conf["coverage"] == "partial"
    for events, reason in [([], "no_result"), ([("failed", None)], "no_result"),
                           ([("absent", None)], "report_missing"), ([("unreadable", None)], "report_unreadable")]:
        assert m.confirmed("api", "openai", {"model_calls": events})["reason"] == reason


def test_m_copilot_complete_partial_and_no_report():
    output = json.dumps({"type": "assistant.message", "data": {"model": "sonnet-5"}})
    for extra, exited, coverage in [("", True, "full"), ("", False, "partial"), ("\n{broken", True, "partial"),
        ('\n' + json.dumps({"type": "assistant.message", "data": {}}), True, "partial")]:
        conf = m.confirmed("cli", "copilot", {"stdout_full": output + extra, "process_exited_itself": exited})
        assert conf["values"] == ["sonnet-5"] and conf["coverage"] == coverage
    for text, reason in [("plain", "report_unreadable"), ('{"type":"other"}', "report_missing"), ("", "no_result")]:
        assert m.confirmed("cli", "copilot", {"stdout_full": text})["reason"] == reason
    for kind, reason in [("claude", "kind_reports_no_model"), ("codex", "kind_reports_no_model"), ("custom", "custom_command")]:
        assert m.confirmed("cli", kind, {"stdout_full": output})["reason"] == reason


def test_m_unknown_models_are_allowed_and_never_filled_from_name():
    provider = {"exec_type": "cli", "kind": "codex", "name": "Expensive Model", "cli_command": "codex --json"}
    req = m.requested(provider, shell_kind="posix_sh")
    assert m.capability(provider, req) == "cli_none"
    conf = m.confirmed("cli", "codex", {"stdout_full": "output"})
    assert m.compare(req, conf)["status"] == "unidentifiable"
