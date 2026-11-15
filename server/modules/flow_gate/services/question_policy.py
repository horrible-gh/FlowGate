"""Resolve the policy owner without substituting a card assignee or AI provider."""
from modules.flow_gate.db import user_question_policies as repo
from modules.flow_gate.db.connection import get_store


def owner(issued_to=None, group_id=None, *, card=False):
    if card and group_id and get_store().table_exists("graph_runs"):
        from modules.flow_gate.graph_workflow.store import run_store
        run = run_store.active_run(group_id)
        if run:
            return run.get("started_by")
    return issued_to


def text(issued_to=None, group_id=None, *, card=False):
    return repo.get(owner(issued_to, group_id, card=card))["body"]


def block(body):
    if not body or not body.strip():
        return ""
    return ("[사용자 질의 방침]\n" + body + "\n[사용자 질의 방침 끝]\n"
            "질의 여부는 위 방침을 우선 적용하십시오. 방침상 AI가 정할 일이면 Q를 남기지 말고 정해서 진행하십시오. "
            "현재 사용자의 명시적 지시와 접근 권한은 그대로 지키십시오.")
