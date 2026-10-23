"""0064 T#1: atomic card files, rollback lifecycle, path identity, and default titles."""
from __future__ import annotations

import ast
import inspect
import json
import os
import re
import sqlite3
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_fg_card_documents import (FA, FB, _doc, _h, _sql, db_path, env, node, put_draft)
from modules.flow_gate.db import connection, documents
from modules.flow_gate.storage import files, paths
from flowgate.card_documents import legacy_adapter as legacy, repository, slots


@pytest.fixture(autouse=True)
def line_route(env, monkeypatch):
    from flowgate.card_documents import routes
    env["client"].app.include_router(routes.router)
    monkeypatch.setattr(legacy, "has_permission", lambda *args: True)


def snapshot(env):
    with sqlite3.connect(env["db"]) as db:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                  if r[0] not in {"group_events", "sqlite_sequence", "graph_flow_guards"}]
        return {t: sorted(map(repr, db.execute('SELECT * FROM "' + t + '"').fetchall())) for t in tables}


def disk(env):
    return {str(p): p.read_bytes() for p in env["storage"].rglob("*") if p.is_file()}


def draft(env, nodes, version=0):
    return env["client"].put(f"/api/v1/flows/{FB}/graph/draft", headers=_h(),
        json={"draft_version": version, "nodes": nodes, "edges": []})


def test_second_card_failure_rolls_back_draft_files_and_records_one_event(env, monkeypatch):
    """V-B1a-1 / V-P-5: real temporary-file replace fails after the first card succeeded."""
    put_draft(env, FB, 0, [node("tmp_gate", node_type="gate")])
    before = snapshot(env)
    original = Path.replace
    count = 0
    def fail_second(path, target):
        nonlocal count
        if ".tmp-" in path.name:
            count += 1
            if count == 2:
                raise PermissionError("injected replace failure")
        return original(path, target)
    monkeypatch.setattr(Path, "replace", fail_second)
    response = draft(env, [node("tmp_r", "R"), node("tmp_n", "N")], version=1)
    assert response.status_code == 503, response.text
    assert response.json()["error"] == {
        "code": "document_storage_unwritable",
        "message": "문서 저장소에 쓸 수 없어 저장하지 못했습니다. 잠시 뒤 다시 시도하거나 운영자에게 알리세요.",
        "params": {"node_key": "tmp_n", "slot": "instruction", "cause": "write_failed"}}
    assert snapshot(env) == before
    assert disk(env) == {}
    events = _sql(env["db"], "SELECT * FROM group_events WHERE event_type='doc_storage_write_failed'")
    assert len(events) == 1
    assert events[0]["group_id"] == FB and events[0]["reason"] == "PermissionError"
    assert json.loads(events[0]["note"]) == {"flow_id": FB, "code": "0002-N", "slot": "instruction", "exception": "PermissionError"}
    assert not env["events"][1:]  # no changed-canvas event from the failed save


def test_line_write_failure_preserves_rows_tokens_and_existing_files(env, monkeypatch):
    """V-B1a-2: no row, line or token is left by the failed request."""
    saved = put_draft(env, FB, 0, [node("tmp_r", "R")])
    key = saved["key_map"]["tmp_r"]
    old_id = saved["card_documents"][key][0]["doc_id"]
    documents.update(old_id, {"status": "cancelled"})
    before, before_files = snapshot(env), disk(env)
    with monkeypatch.context() as patcher:
        patcher.setattr(legacy, "write_new_file", lambda *args: (_ for _ in ()).throw(OSError("disk full")))
        response = env["client"].post(f"/api/v1/flows/{FB}/nodes/{key}/slots/requirement/document",
                                     headers=_h(), json={"content": "본문\nsecond line\n"})
    assert response.status_code == 503, response.text
    assert response.json()["error"]["params"] == {"node_key": key, "slot": "requirement", "cause": "write_failed"}
    assert snapshot(env) == before and disk(env) == before_files
    response = env["client"].post(f"/api/v1/flows/{FB}/nodes/{key}/slots/requirement/document",
                                 headers=_h(), json={"content": "본문\nsecond line\n"})
    assert response.status_code == 201, response.text
    row = documents.get_by_id(response.json()["doc_id"])
    assert json.loads(row["meta"])["graph_card"]["created_empty"] is False
    expected = legacy._front_matter("flowgate", "v02", FB, "R", row["doc_id"].rsplit(".", 1)[1], row["title"]) + "본문\nsecond line\n"
    assert paths.resolve_storage_path(row["file_path"], "flowgate").read_bytes() == expected.encode("utf-8")


def test_failure_after_row_and_line_insert_removes_new_file(env, monkeypatch):
    """V-B1a-4: the write succeeded; a later transaction stage failed."""
    original = repository.insert
    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise legacy.GraphApiError(500, "internal_error")
    before = snapshot(env)
    monkeypatch.setattr(repository, "insert", fail)
    response = draft(env, [node("tmp_r", "R")])
    assert response.status_code == 500
    assert snapshot(env) == before and disk(env) == {}


def test_orphan_is_preserved_and_new_draft_gets_its_file(env):
    """V-B1a-5,9: orphan bytes survive and new bytes are exact UTF-8."""
    target = paths.document_path("flowgate", FB, "0001-R", "requirement.md", module="v02")
    target.parent.mkdir(parents=True)
    target.write_bytes(b"old orphan\r\n")
    saved = put_draft(env, FB, 0, [node("tmp_r", "R", "Requirement"), node("tmp_wp", "WP")])
    assert [p.read_bytes() for p in target.parent.glob(target.name + ".orphan-*")] == [b"old orphan\r\n"]
    for key in saved["key_map"].values():
        for line in saved["card_documents"][key]:
            row = documents.get_by_id(line["doc_id"])
            assert json.loads(row["meta"])["graph_card"]["created_empty"] is True
            content = paths.resolve_storage_path(row["file_path"], "flowgate").read_bytes()
            if row["type_code"] == "WP":
                body, title = legacy._work_plan_body("flowgate")
                assert row["title"] == title and content == body.encode("utf-8")
            else:
                code = row["doc_id"].rsplit(".", 1)[1]
                assert content == legacy._front_matter("flowgate", "v02", FB, row["type_code"], code, row["title"]).encode("utf-8")
    row = documents.get_by_id(saved["card_documents"][saved["key_map"]["tmp_r"]][0]["doc_id"])
    assert row["title"] == "요구사항" and b"\r" not in target.read_bytes()


@pytest.mark.parametrize("kind", ["absolute", "storage_prefix", "missing_branch", "duplicate_branch", "case", "outside_jail"])
def test_referenced_file_aliases_are_never_moved(env, tmp_path, kind):
    """V-B1a-8: all projects/statuses and actual file identity, irrespective of the read jail."""
    target = paths.document_path("flowgate", FB, "0001-R", "requirement.md", module="v02")
    if kind == "outside_jail":
        target = tmp_path / "outside" / target.name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"protected")
    relative = paths.to_storage_relative(target, "flowgate")
    stored = {"absolute": str(target), "storage_prefix": "/storage/" + relative,
              "missing_branch": relative.replace("/main/", "/"),
              "duplicate_branch": relative.replace("/main/", "/main/main/"),
              "case": str(target).swapcase(), "outside_jail": str(target)}[kind]
    if kind == "case" and not Path(stored).exists():
        pytest.skip("filesystem is case-sensitive; case alias only applies to case-insensitive filesystems")
    _doc(FA, 2, "M", status="cancelled", file_path=stored)
    assert files.referenced_by_any_document(target)
    with connection.get_store().transaction():
        with pytest.raises(files.StorageUnwritable) as error:
            files.write_new_file(target, b"replacement")
    assert error.value.params["cause"] == "target_in_use"
    assert target.read_bytes() == b"protected"
    assert not list(target.parent.glob(target.name + ".orphan-*"))
    assert not list(target.parent.glob(target.name + ".tmp-*"))


@pytest.mark.parametrize("kind", ["hardlink", "symlink"])
def test_links_are_conservatively_in_use(env, tmp_path, kind):
    original, target = tmp_path / "original", tmp_path / "target"
    original.write_bytes(b"protected")
    try:
        if kind == "hardlink":
            os.link(original, target)
        else:
            target.symlink_to(original)
    except OSError:
        pytest.skip(kind + " creation unavailable")
    assert files.same_file(original, target)
    with pytest.raises(files.StorageUnwritable):
        files.write_new_file(target, b"new")
    assert target.read_bytes() == original.read_bytes() == b"protected"


def test_after_rollback_nested_reverse_order_and_callback_failure(env, caplog):
    seen = []
    def callback(value):
        assert not connection.in_transaction()
        seen.append(value)
    assert not connection.after_rollback(lambda: callback("outside"))
    with pytest.raises(ValueError, match="original"):
        with connection.get_store().transaction():
            connection.after_commit(lambda: callback("commit"))
            connection.after_rollback(lambda: callback("first"))
            with connection.get_store().transaction():
                connection.after_rollback(lambda: (_ for _ in ()).throw(RuntimeError("cleanup")))
                connection.after_rollback(lambda: callback("last"))
            raise ValueError("original")
    assert seen == ["last", "first"] and "after-rollback callback failed" in caplog.text
    with connection.get_store().transaction():
        connection.after_rollback(lambda: callback("discarded"))
        connection.after_commit(lambda: callback("committed"))
    assert seen == ["last", "first", "committed"]


def test_commit_failure_runs_cleanup_after_frame_closes(env, tmp_path):
    store = connection.FlowGateStore()
    @contextmanager
    def begin():
        yield SimpleNamespace(execute=lambda *args: None)
        raise OSError("commit failed")
    store._db = SimpleNamespace(begin_transaction=begin)
    path = tmp_path / "new.md"
    seen = []
    with pytest.raises(OSError, match="commit failed"):
        with store.transaction():
            files.write_new_file(path, b"new\n")
            connection.after_commit(lambda: seen.append("commit"))
            connection.after_rollback(lambda: seen.append(connection.in_transaction()))
    assert seen == [False] and not path.exists()


def test_outside_transaction_row_failure_removes_file(env, monkeypatch):
    from modules.flow_gate.db import groups
    from modules.flow_gate.documents import document_service
    monkeypatch.setattr(document_service, "create_document", lambda *a, **k: (_ for _ in ()).throw(ValueError("row")))
    with pytest.raises(ValueError, match="row"):
        legacy.create_document(groups.get_by_id(FB), {"node_key": "gn_test", "label": "R"}, slots.card_slots("document", "R")[0], "u_kim")
    assert disk(env) == {}


def test_dispatch_savepoint_only_wraps_token_issue(env):
    """V-B1a-6: assert the actual savepoint body calls only token issuance."""
    from modules.flow_gate.graph_workflow.services import node_dispatch
    tree = ast.parse(inspect.getsource(node_dispatch))
    points = [n for n in ast.walk(tree) if isinstance(n, ast.With) and any(
        isinstance(i.context_expr, ast.Call) and isinstance(i.context_expr.func, ast.Name)
        and i.context_expr.func.id == "_savepoint" for i in n.items)]
    assert len(points) == 1
    calls = [ast.unparse(n.func) for statement in points[0].body for n in ast.walk(statement) if isinstance(n, ast.Call)]
    assert calls[0] == "token_service.issue"
    assert set(calls) <= {"token_service.issue", "(root or {}).get"}


@pytest.mark.parametrize("label", ["요구사항", "Requirement", "要件", "バグレポート", " 요구사항 ", "REQUIREMENT", "Requi\u200brement", "\ufeffRequirement", unicodedata.normalize("NFD", "요구사항")])
def test_default_title_exact_normalized_match(label, env):
    """V-B4a-1 / V-P-14."""
    assert legacy._title(label, slots.card_slots("document", "R")[0]) == "요구사항"


def test_custom_titles_empty_and_limit(env):
    slot = slots.card_slots("document", "R")[0]
    assert legacy._title("로그인 개선", slot) == "로그인 개선 · 요구사항"
    assert legacy._title("요구사항 2", slot) == "요구사항 2 · 요구사항"
    assert legacy._title("  ", slot) == "요구사항"
    assert len(legacy._title("긴" * 200, slot)) == legacy.TITLE_MAX_LEN
    assert legacy._title("AI\t  delegation", slots.CardSlot("delegation", "M", "run")) == "AI 위임"


def test_resave_keeps_existing_document_title(env):
    saved = put_draft(env, FB, 0, [node("tmp_r", "R", "요구사항")])
    key = saved["key_map"]["tmp_r"]
    doc_id = saved["card_documents"][key][0]["doc_id"]
    documents.update(doc_id, {"title": "기존 제목 · 요구사항"})
    again = put_draft(env, FB, 1, [node(key, "R", "Requirement")])
    assert again["card_documents"][key][0]["title"] == "기존 제목 · 요구사항"


def test_japanese_slot_names_match_client_messages(env):
    """V-B4a-4: compare the maintained dictionary to each client slot source."""
    root = Path(__file__).resolve().parents[2] / "client_gui/src/features/designer/inspector/features"
    actual = {}
    for card in ["requirementCard", "workPlan", "designCard", "researchCard", "taskCard", "testSpec"]:
        text = (root / card / "messages.ts").read_text(encoding="utf-8")
        block = re.search(r"ja:\s*\{\s*slot:\s*\{([^}]+)", text).group(1)
        for key, value in re.findall(r"(\w+):\s*'([^']+)'", block):
            if card == "requirementCard" and key == "bug":
                key = "requirement"
            if key in slots.SLOT_KEYS:
                actual.setdefault(key, set()).add(value)
    expected = {key: {value} if isinstance(value, str) else set(value) for key, value in slots.SLOT_TITLES["ja"].items()}
    assert expected == actual



# ── flowgate.v02.0088 T#1: a new document's title in the request's language (D0010 §3.2 / §6) ──────────────

R_NAMES = {"ko": "요구사항", "ja": "要件", "en": "Requirement"}
B_NAMES = {"ko": "요구사항", "ja": "バグレポート", "en": "Requirement"}      # ko / en: one requirement name for R and B
DEFAULT_LABELS = ["", "요구사항", "要件", "Requirement", "버그", "バグ", "Bug", "バグレポート"]


def _requirement(doc_type):
    return slots.card_slots("document", doc_type)[0]


@pytest.mark.parametrize("locale", ["ko", "ja", "en"])
@pytest.mark.parametrize("label", DEFAULT_LABELS)
def test_a_default_label_takes_the_name_of_the_new_document_s_type_in_the_request_locale(label, locale):
    """Blank, the R names, the B card names (버그 / バグ / Bug) and the ja B document name are all default names: the
    title is the name of the type the document is (R or B), never the label plus a suffix (``バグ · バグレポート``)."""
    r_title = legacy._title(label, _requirement("R"), locale)
    b_title = legacy._title(label, _requirement("B"), locale)
    assert (r_title, b_title) == (R_NAMES[locale], B_NAMES[locale])
    assert " · " not in r_title and " · " not in b_title


def test_the_type_of_a_new_document_is_its_line_s_never_its_label():
    """``バグレポート`` on an R document stays R (要件); ``Bug`` on an R document is no longer ``Bug · Requirement``
    (before 0088 the B card names were not default names) — an intended change, fixed here (review 2183)."""
    assert legacy._title("バグレポート", _requirement("R"), "ja") == "要件"
    assert legacy._title("Bug", _requirement("R"), "en") == "Requirement"
    assert legacy._title("Bug", _requirement("R")) == "요구사항"
    assert legacy._title("요구사항", _requirement("B"), "ja") == "バグレポート"


@pytest.mark.parametrize("locale, expected", [("ko", "로그인 오류 · 요구사항"), ("ja", "로그인 오류 · バグレポート"),
                                              ("en", "로그인 오류 · Requirement")])
def test_a_person_s_label_keeps_its_text_and_takes_the_type_s_name_in_the_request_locale(locale, expected):
    assert legacy._title("로그인 오류", _requirement("B"), locale) == expected
    assert legacy._title("로그인 오류", _requirement("R"), locale) == "로그인 오류 · " + R_NAMES[locale]
    long = legacy._title("긴" * 200, _requirement("B"), locale)
    assert len(long) == legacy.TITLE_MAX_LEN and long.endswith(" · " + B_NAMES[locale])


@pytest.mark.parametrize("locale", [None, "", "zh", "JA", "ja-JP", "en-US"])
def test_no_blank_or_unsupported_locale_is_korean(locale):
    """The request normalization (``envelope.locale_of``) is not widened: anything but ko / ja / en is ko."""
    assert legacy._title("", _requirement("B"), locale) == "요구사항"
    assert legacy._title("로그인 오류", _requirement("B"), locale) == "로그인 오류 · 요구사항"
    assert legacy._title("R", slots.CardSlot("delegation", "M", "run"), locale) == "R · AI 위임"


@pytest.mark.parametrize("locale, name", [("ko", "AI 위임"), ("ja", "AI委任"), ("en", "AI delegation")])
def test_other_lines_take_their_own_name_and_keep_their_own_default_names(locale, name):
    delegation = slots.CardSlot("delegation", "M", "run")
    assert legacy._title("", delegation, locale) == name
    assert legacy._title("AI\t  delegation", delegation, locale) == name
    assert legacy._title("Bug", delegation, locale) == "Bug · " + name          # the R / B card names: requirement only
    instruction = slots.card_slots("document", "T")[0]
    assert legacy._title("指示書", instruction, locale) == slots.slot_title("instruction", locale)


@pytest.mark.parametrize("locale, prefix", [("ko", "작업계획 — "), ("ja", "作業計画 — "), ("en", "Work plan — "),
                                            (None, "작업계획 — "), ("ja-JP", "작업계획 — ")])
def test_a_new_work_plan_derives_its_title_in_the_request_locale_with_the_same_plan(locale, prefix):
    from modules.flow_gate.services import work_plan_service as wp

    body, title = legacy._work_plan_body("flowgate", locale)
    plan = json.loads(body)
    assert title.startswith(prefix) and title == wp.derived_title(plan, slots.title_locale(locale))
    assert body == legacy._work_plan_body("flowgate")[0]                     # the plan JSON does not depend on it


def test_a_resolved_title_is_used_as_is_for_the_row_and_front_matter(env, monkeypatch):
    """The deploy decides a new instruction's title once and hands it over (``resolved_title``); no second decision."""
    from modules.flow_gate.db import groups

    monkeypatch.setattr(legacy, "_title", lambda *a, **k: pytest.fail("the title was decided again"))
    slot = slots.card_slots("document", "T")[0]
    doc_id = legacy.create_document(groups.get_by_id(FB), {"node_key": "gn_test", "label": "구현"}, slot, "u_kim",
                                    "# 구현 · 指示書\n\n본문\n", locale="ja", resolved_title="구현 · 指示書")
    row = documents.get_by_id(doc_id)
    text = (env["storage"] / row["file_path"]).read_text(encoding="utf-8")
    assert row["title"] == "구현 · 指示書" and "\ntitle: 구현 · 指示書\n" in text and text.endswith("# 구현 · 指示書\n\n본문\n")


def test_without_a_resolved_title_the_generator_decides_it_once_in_the_locale(env):
    from modules.flow_gate.db import groups

    doc_id = legacy.create_document(groups.get_by_id(FB), {"node_key": "gn_test", "label": "バグ"},
                                    _requirement("B"), "u_kim", locale="ja")
    row = documents.get_by_id(doc_id)
    text = (env["storage"] / row["file_path"]).read_text(encoding="utf-8")
    assert row["title"] == "バグレポート" and "\ntitle: バグレポート\n" in text
