"""verify_id_widths — validates numbering consistency, file presence, and DB-storage matching."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..db.connection import get_store
from ..db import projects as db_projects
from ..storage.paths import document_path, resolve_storage_path


@dataclass
class ValidationReport:
    """Validation result report."""

    project_id: str
    ok: bool = True

    # Width mismatch: (entity_type, id, expected_width, actual_width)
    width_mismatches: list[tuple[str, str, int, int]] = field(default_factory=list)

    # Missing file: file_path exists in the DB but the actual file is missing (doc_id, file_path)
    missing_files: list[tuple[str, str]] = field(default_factory=list)

    # Orphan file: exists in storage but not in the DB (file_path)
    orphan_files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "ok": self.ok,
            "width_mismatches": [
                {"entity_type": e, "id": i, "expected": ew, "actual": aw}
                for e, i, ew, aw in self.width_mismatches
            ],
            "missing_files": [
                {"doc_id": d, "file_path": fp}
                for d, fp in self.missing_files
            ],
            "orphan_files": self.orphan_files,
        }


def _get_widths(project_id: str) -> dict[str, int]:
    ps = db_projects.get_settings(project_id)
    if ps:
        return {
            "group": ps.get("digits_group", 4) or 4,
            "subgroup": ps.get("digits_sub_group", 3) or 3,
            "document": ps.get("digits_type", 4) or 4,
        }
    return {"group": 4, "subgroup": 3, "document": 4}


def verify_id_widths(project_id: str) -> ValidationReport:
    """Validate the project's numbering consistency, file presence, and DB-storage matching.

    Parameters
    ----------
    project_id : str
        Project ID to validate.

    Returns
    -------
    ValidationReport
        Validation result.
    """
    store = get_store()
    report = ValidationReport(project_id=project_id)
    widths = _get_widths(project_id)

    # 1. Numbering consistency — groups
    groups = store._fetch_all(
        "SELECT group_id FROM groups WHERE project_id = ?", [project_id]
    )
    for grp in groups:
        gid = grp["group_id"]
        # Validate width when group_id is a pure numeric code
        if gid.isdigit():
            if len(gid) != widths["group"]:
                report.width_mismatches.append(
                    ("group", gid, widths["group"], len(gid))
                )

    # 2. Numbering consistency — subgroups
    sub_groups = store._fetch_all(
        "SELECT sg.sub_group_id FROM sub_groups sg "
        "JOIN groups g ON sg.group_id = g.group_id "
        "WHERE g.project_id = ?",
        [project_id],
    )
    for sg in sub_groups:
        sgid = sg["sub_group_id"]
        # Check the length of the numeric suffix in sub_group_id
        numeric_part = sgid.split("-")[-1] if "-" in sgid else sgid
        if numeric_part.isdigit():
            if len(numeric_part) != widths["subgroup"]:
                report.width_mismatches.append(
                    ("subgroup", sgid, widths["subgroup"], len(numeric_part))
                )

    # 3. Numbering consistency + file presence — documents
    docs = store._fetch_all(
        "SELECT doc_id, file_path, seq FROM documents WHERE project_id = ?",
        [project_id],
    )
    db_paths: set[str] = set()
    for doc in docs:
        doc_id = doc["doc_id"]
        file_path: Optional[str] = doc.get("file_path")

        # Width validation: the seq portion of the final doc_code in doc_id
        import re
        m = re.match(r'^.*\.(\d+)-[A-Za-z]+$', doc_id)
        if m:
            numeric = m.group(1)
            if len(numeric) != widths["document"]:
                report.width_mismatches.append(
                    ("document", doc_id, widths["document"], len(numeric))
                )

        # File presence validation — resolve through the unified helper so a stored
        # relative (or legacy absolute) path no longer false-positives as missing
        # (L0054.0002 §4). db_paths collects the resolved absolute form so the
        # reverse orphan check below compares like for like.
        if file_path:
            resolved = resolve_storage_path(file_path, project_id)
            if resolved is None:
                report.missing_files.append((doc_id, file_path))
            else:
                db_paths.add(str(resolved))

    # 4. Orphan files (reverse check from storage -> DB)
    # Compare the list of .md/.docx files under project_root with the DB
    try:
        from ..storage.paths import project_root as get_proj_root
        proj_root = get_proj_root(project_id)
        if proj_root.exists():
            for p in proj_root.rglob("*"):
                if p.is_file():
                    abs_str = str(p.resolve())
                    # File not present in the DB
                    if abs_str not in db_paths:
                        # Exclude special directories
                        relative = p.relative_to(proj_root)
                        parts = relative.parts
                        if parts and parts[0] not in ("_backup", "_tmp", ".git"):
                            report.orphan_files.append(abs_str)
    except Exception:
        pass  # Ignore when there is no storage root

    if report.width_mismatches or report.missing_files or report.orphan_files:
        report.ok = False

    return report
