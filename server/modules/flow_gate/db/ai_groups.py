"""Project-owned AI Group persistence. Provider roles are logical IDs without FKs."""
from __future__ import annotations

from typing import Optional

from .connection import get_store, now_iso

_COLUMNS = ("ai_group_id, project_id, name, name_key, name_key_hash, description, "
            "work_executor_provider_id, reviewer_provider_id, reworker_provider_id, "
            "created_at, updated_at")


def list_for_project(project_id: str) -> list[dict]:
    return get_store()._fetch_all(
        f"SELECT {_COLUMNS} FROM ai_groups WHERE project_id = ? "
        "ORDER BY updated_at DESC, ai_group_id", [project_id])


def get(project_id: str, ai_group_id: str) -> Optional[dict]:
    return get_store()._fetch_one(
        f"SELECT {_COLUMNS} FROM ai_groups WHERE project_id = ? AND ai_group_id = ?",
        [project_id, ai_group_id])


def get_by_name_hash(project_id: str, name_key_hash: str) -> Optional[dict]:
    return get_store()._fetch_one(
        f"SELECT {_COLUMNS} FROM ai_groups WHERE project_id = ? AND name_key_hash = ?",
        [project_id, name_key_hash])


def create(row: dict) -> None:
    now = now_iso()
    get_store()._execute(
        "INSERT INTO ai_groups (ai_group_id, project_id, name, name_key, name_key_hash, "
        "description, work_executor_provider_id, reviewer_provider_id, reworker_provider_id, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [row['ai_group_id'], row['project_id'], row['name'], row['name_key'],
         row['name_key_hash'], row['description'], row['work_executor_provider_id'],
         row['reviewer_provider_id'], row['reworker_provider_id'], now, now])


def update(project_id: str, ai_group_id: str, row: dict) -> None:
    get_store()._execute(
        "UPDATE ai_groups SET name = ?, name_key = ?, name_key_hash = ?, description = ?, "
        "work_executor_provider_id = ?, reviewer_provider_id = ?, reworker_provider_id = ?, "
        "updated_at = ? WHERE project_id = ? AND ai_group_id = ?",
        [row['name'], row['name_key'], row['name_key_hash'], row['description'],
         row['work_executor_provider_id'], row['reviewer_provider_id'],
         row['reworker_provider_id'], now_iso(), project_id, ai_group_id])


def delete(project_id: str, ai_group_id: str) -> None:
    get_store()._execute(
        "DELETE FROM ai_groups WHERE project_id = ? AND ai_group_id = ?",
        [project_id, ai_group_id])
