"""Local image delivery with real SQLite links and PNG bytes; no external AI calls."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_message_images_0029 import env, upload, append, PNG, restore_lazy_agent_environment
from _fg_conversation_support import fg_store, CHAT_DOC
from modules.flow_gate.services import message_image_delivery as delivery
from modules.flow_gate.services import invoke_mention_service as mentions
from modules.flow_gate.services.ai_invoke import worker, finalize
from modules.flow_gate.db import ai_invoke_runs

CLAUDE = dict(id="claude", exec_type="cli", kind="claude", exec_location="local")


def run_for(tmp_path, after=0, locale="en"):
    scratch = tmp_path / "token"
    scratch.mkdir(exist_ok=True)
    return dict(message_image_context=dict(conversation_id=CHAT_DOC, after_seq=after, locale=locale),
                token_scratch_dir=str(scratch), action_scope="chat", api_base_url="http://test/api/v1")


def add_images(n):
    seqs = []
    for i in range(n):
        append([upload()["image_id"]], key=f"image-message-{i:04}")
        seqs.append(i+1)
    return seqs


@pytest.mark.parametrize("kind", ["claude", "codex"])
def test_local_recent_window_and_independent_copies(env, tmp_path, kind):
    add_images(13)
    run = run_for(tmp_path, after=1)
    prompt = delivery.provider_prompt("BASE", dict(CLAUDE, kind=kind), run)
    files = list((Path(run["token_scratch_dir"])/"images").glob("*"))
    assert len(files) == 10
    assert {p.name for p in files} == {f"{i}-0.png" for i in range(4,14)}
    assert all(p.read_bytes() == PNG and not p.is_symlink() for p in files)
    assert all(str(p) in prompt for p in files)
    assert "2 older image(s)" in prompt
    assert ("Read tool" if kind == "claude" else "Codex --image") in prompt
    assert run["images_total"] == 12 and run["images_delivered"] == 10
    for row in delivery.images.images_for_window(CHAT_DOC, 1):
        copied = Path(run["token_scratch_dir"])/"images"/f"{row['seq']}-0.png"
        assert not copied.samefile(row["abs_path"])


@pytest.mark.parametrize("provider", [
    dict(CLAUDE, kind="copilot"), dict(CLAUDE, kind="custom"),
    dict(CLAUDE, exec_type="api"),
])
def test_unsupported_is_visible_and_durable(env, tmp_path, monkeypatch, provider):
    add_images(1)
    run = run_for(tmp_path)
    prompt = delivery.provider_prompt("BASE", provider, run)
    assert "1 image(s) are present but not delivered" in prompt
    assert not (Path(run["token_scratch_dir"])/"images").exists()
    assert run["images_undelivered"] == 1
    run.update(run_id="r", group_id="g", project_id="p", doc_ref=CHAT_DOC, mode="single")
    saved=[]
    monkeypatch.setattr(ai_invoke_runs, "upsert", lambda row: saved.append(row))
    monkeypatch.setattr(ai_invoke_runs, "maybe_purge", lambda: None)
    assert finalize._persist_run_record(run)
    assert saved[0]["fallback_history"][-1]["images_undelivered"] == 1
    assert saved[0]["fallback_history"][-1]["reason"] == "message_images"


def test_copy_and_shared_context(env, tmp_path, monkeypatch):
    add_images(2)
    calls=[]
    def window(*args, **kwargs):
        calls.append(1)
        return dict(after_seq=1, folded=0, checkpoint_seq=None)
    monkeypatch.setattr(mentions.chat_doc_settings_service, "context_window", window)
    args=dict(doc_id=CHAT_DOC, project="p", module="m", group_name="g", raw_token="test",
              token_id="tok", api_base_url="http://test", locale="ko")
    copied=mentions.build_conversation_mention(**args)
    assert "이미지 1장은 복사 경로로 전달되지 않음" in copied
    invoked=mentions.build_conversation_mention(**args, command_execution=True)
    assert len(calls) == 2
    run=run_for(tmp_path)
    run["message_image_context"]=invoked.image_context
    text=delivery.provider_prompt(str(invoked), CLAUDE, run)
    assert "메시지 #2:" in text and "메시지 #1:" not in text
    assert "Read 도구" in text


def test_no_images_unchanged_and_window_exclusion(env, tmp_path):
    run=run_for(tmp_path)
    assert delivery.provider_prompt("EXACT\n", CLAUDE, run) == "EXACT\n"
    assert delivery.copy_notice(run["message_image_context"]) == ""
    add_images(1)
    run["message_image_context"]["after_seq"]=1
    assert delivery.provider_prompt("EXACT\n", CLAUDE, run) == "EXACT\n"


@pytest.mark.parametrize("link_kind", ["directory", "file"])
@pytest.mark.parametrize("kind", ["claude", "codex"])
def test_scratch_symlink_rejected(env, tmp_path, link_kind, kind):
    add_images(1)
    run=run_for(tmp_path)
    folder=Path(run["token_scratch_dir"])/"images"
    outside=tmp_path/"outside"
    outside.mkdir()
    if link_kind == "directory":
        folder.symlink_to(outside, target_is_directory=True)
    else:
        folder.mkdir()
        target=outside/"untouched"
        target.write_bytes(b"original")
        (folder/"1-0.png").symlink_to(target)
    text=delivery.provider_prompt("BASE", dict(CLAUDE, kind=kind), run)
    assert "message_image_cli_args" not in run
    assert "copying failed" in text and run["images_copy_failed"] == 1
    assert not (outside/"1-0.png").exists()
    if link_kind == "file":
        assert target.read_bytes() == b"original"


def test_provider_boundary_fallback_has_no_path_leak(env, tmp_path, monkeypatch):
    add_images(1)
    run=run_for(tmp_path)
    monkeypatch.setattr(worker.node_title_hooks, "provider_prompt", lambda prompt,*a: prompt)
    local=worker._provider_prompt(run, CLAUDE, "BASE")
    remote=worker._provider_prompt(run, dict(CLAUDE, exec_location="remote"), "BASE")
    assert str(Path(run["token_scratch_dir"])/"images") in local
    assert str(Path(run["token_scratch_dir"])/"images") not in remote
    assert "Authorization: Bearer" in remote and run["images_download_available"] == 1


@pytest.mark.parametrize("cap", ["items", "bytes"])
def test_image_failure_history_keeps_last_connection_under_caps(env, tmp_path, monkeypatch, cap):
    import json
    add_images(1)
    run = run_for(tmp_path)
    delivery.provider_prompt("BASE", CLAUDE, run)
    failure = dict(reason="spawn_failed", provider_id="claude", detail="CLI failed to start")
    run.update(run_id="r", group_id="g", project_id="p", doc_ref=CHAT_DOC, mode="single",
               end_reason="all_providers_failed", fallback_history=[failure], attempt_no=1)
    saved = []
    monkeypatch.setattr(ai_invoke_runs, "upsert", lambda row: saved.append(row))
    monkeypatch.setattr(ai_invoke_runs, "maybe_purge", lambda: None)
    assert finalize._persist_run_record(run)
    history = saved[0]["fallback_history"]
    assert history[0]["reason"] == "message_images"
    assert history[-1] == failure
    if cap == "items":
        monkeypatch.setattr(ai_invoke_runs, "_HISTORY_MAX_ITEMS", 1)
    else:
        monkeypatch.setattr(ai_invoke_runs, "_HISTORY_MAX_SERIALIZED_BYTES",
                            len(json.dumps([failure], ensure_ascii=False).encode("utf-8")))
    stored = json.loads(ai_invoke_runs._dump_array("fallback_history", history))
    assert stored[-1] == failure
    assert stored[0] == {"reason": "truncated", "dropped": 1}
    assert run["fallback_history"] == [failure] and run["attempt_no"] == 1


def test_codex_images_reach_launch_and_are_cleared(env, tmp_path, monkeypatch):
    import shlex
    from modules.flow_gate.services.ai_invoke import provider_cli
    add_images(2)
    run = run_for(tmp_path)
    run.update(scratch_dir=str(tmp_path), raw_token="test", run_id="r")
    provider = dict(CLAUDE, kind="codex", id="codex", cli_command="codex exec -")
    prompt = delivery.provider_prompt("BASE", provider, run)
    assert "Read tool" not in prompt
    seen = []
    def resolve(provider, run, command):
        seen.append(command)
        return None, "test_boundary"
    monkeypatch.setattr(provider_cli, "_resolve_cli_launch", resolve)
    monkeypatch.setattr(provider_cli, "_audit_cli_launch", lambda *a: None)
    assert provider_cli._cli_execute(provider, prompt, run)[0] == "spawn_failed"
    args = shlex.split(seen[-1])
    paths = [args[i+1] for i, arg in enumerate(args) if arg == "--image"]
    assert [Path(p).name for p in paths] == ["1-0.png", "2-0.png"]
    assert all(Path(p).read_bytes() == PNG for p in paths)
    assert args.count("--image") == 2 and "--skip-git-repo-check" in args
    assert "--output-last-message" in args and "-" in args
    # A different connection cannot inherit attachments from the earlier one.
    provider_cli._cli_execute(dict(provider, id="other"), "BASE", run)
    assert "--image" not in seen[-1]
    delivery.provider_prompt("BASE", dict(CLAUDE, exec_location="remote"), run)
    assert "message_image_cli_args" not in run
    provider_cli._cli_execute(provider, "BASE", run)
    assert "--image" not in seen[-1]
    delivery.provider_prompt("BASE", provider, run)
    run["message_image_context"]["after_seq"] = 2
    assert delivery.provider_prompt("EXACT", provider, run) == "EXACT"
    assert "message_image_cli_args" not in run


def test_codex_path_quoting_and_visible_failure(env, tmp_path, monkeypatch):
    import shlex
    monkeypatch.setattr(delivery, "os", SimpleNamespace(name="posix"))
    path = "/tmp/space ' dollar$(no)/image.png"
    assert shlex.split(delivery.codex_image_argument(path)) == [path]
    monkeypatch.setattr(delivery, "os", SimpleNamespace(name="nt"))
    assert delivery.codex_image_argument("C:/space dir/image.png") == '"C:/space dir/image.png"'
    for path in ("C:/%TEMP%/image.png", "C:/wow!/image.png", "C:/a,b/image.png"):
        with pytest.raises(ValueError):
            delivery.codex_image_argument(path)
    add_images(1)
    run = run_for(tmp_path)
    monkeypatch.setattr(delivery, "_copy", lambda *a: "C:/a,b/image.png")
    prompt = delivery.provider_prompt("BASE", dict(CLAUDE, kind="codex"), run)
    assert "not delivered" in prompt and run["images_delivered"] == 0
    assert "message_image_cli_args" not in run


@pytest.mark.parametrize("kind", ["claude", "codex"])
def test_remote_download_window_and_no_local_paths(env, tmp_path, kind):
    add_images(13)
    run = run_for(tmp_path, after=1)
    delivery.provider_prompt("BASE", dict(CLAUDE, kind="codex"), run)
    prompt = delivery.provider_prompt("BASE", dict(CLAUDE, kind=kind, exec_location="remote"), run)
    assert str(tmp_path) not in prompt
    assert "message_image_cli_args" not in run
    assert "2 older image(s)" in prompt
    assert "view_image" in prompt and "Authorization: Bearer" in prompt
    assert "Message #3," not in prompt and "Message #4," in prompt
    assert "Message #13," in prompt
    assert run["images_download_available"] == 10
    assert prompt.count("/content") == 10


def test_remote_missing_api_base_is_visible(env, tmp_path):
    add_images(1)
    run = run_for(tmp_path)
    run.pop("api_base_url")
    prompt = delivery.provider_prompt("BASE", dict(CLAUDE, exec_location="remote"), run)
    assert "API base URL is missing" in prompt
    assert run["images_download_available"] == 0
