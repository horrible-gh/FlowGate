"""FlowGate MVP — YAML header parsing + required field validation."""
from __future__ import annotations

import re
from datetime import date

VALID_TYPES = {
    "R", "B", "Q", "A", "AR", "DS",
    "D", "DB", "P", "L", "DC",
    "N", "NR", "T", "TR",
    "TV", "TVR",
    "V", "VR",
    "M", "AC", "RJ",
    "CH",  # L0044.0008 §2: conversation (chat) — auto-approve like M, non-gate
}
REQUIRED_FIELDS = {"type", "project", "title"}
CONFIRM_REQUIRED = {"type", "target", "action"}
# NR/TR types require a referenced document ID
NR_TR_TYPES = {"NR", "TR"}
VALID_PRIORITIES = {"low", "medium", "high"}
VALID_TV_TYPES = {"internal", "external", "integration"}
VALID_PASS_CRITERIA = {"all", "partial"}
VALID_TVR_SUMMARY_STATUS = {"Pass", "Fail", "Closed", "Reject"}
VALID_BOOL_LITERALS = {"true", "false"}
TV_REQUIRED_FIELDS = {"tv_type"}
TVR_REQUIRED_FIELDS = {"summary_status"}
TV_TARGET_PREFIX = "T"
TVR_TARGET_PREFIX = "TV"
LIST_BLOCK_KEYS = {"approved_files", "refs"}
DICT_BLOCK_KEYS = {"clear_scope"}

TITLE_MAX_LEN = 100
INBOX_TARGET_TYPES = {"Q", "AR", "DS", "D", "DB", "P", "L", "DC", "N", "NR", "T", "TR", "TV", "TVR", "V", "VR"}
NEXT_REQUIRED_TYPES = {"AR", "DS"}
NEXT_VALID_VALUES = {"D", "P", "L", "N", "T"}
GROUP_REQUIRED_EXEMPT = {"R"}
GROUP_SEQ_DIGITS = 4
TARGET_ID_DOC_ID_PATTERN = re.compile(r"^[A-Za-z0-9_]+-[A-Za-z0-9_]*-[A-Z]{1,2}[0-9]{3}$")
TARGET_ID_PREFIX_PATTERN = re.compile(
    r"^[A-Za-z0-9_]+-[A-Za-z0-9_]*-(?P<doc_type>[A-Z]{1,3})[0-9]{3}$"
)


def _normalize_allowed_projects(
    allowed_projects: set[str] | list[dict],
) -> tuple[set[str], set[tuple[str, str]]]:
    """Normalize the allowed project set and the allowed (project, module) pair set."""
    if isinstance(allowed_projects, set):
        return allowed_projects, set()

    project_names: set[str] = set()
    project_module_pairs: set[tuple[str, str]] = set()
    for row in allowed_projects:
        project = (row.get("project") or "").strip()
        module = (row.get("module") or "").strip()
        if not project:
            continue
        project_names.add(project)
        project_module_pairs.add((project, module))

    return project_names, project_module_pairs


def parse_yaml_header(content: str) -> tuple[dict | None, str]:
    """Parse the YAML header.

    Returns:
        (header_dict, error_message). On success, error_message is an empty string.
    """
    content = content.strip()
    if not content.startswith("---"):
        return None, "No YAML header found (must start with ---)"

    end_idx = content.find("---", 3)
    if end_idx == -1:
        return None, "YAML header is not closed (closing --- required)"

    yaml_block = content[3:end_idx].strip()
    header: dict = {}
    collecting_list_key: str | None = None
    collecting_dict_key: str | None = None
    for raw_line in yaml_block.split("\n"):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            if collecting_list_key is not None:
                collecting_list_key = None
            if collecting_dict_key is not None:
                collecting_dict_key = None
            continue

        if collecting_list_key is not None:
            list_item = re.match(r"^\s+-\s+(.+)$", raw_line)
            if list_item:
                header[collecting_list_key].append(list_item.group(1).strip())
                continue
            else:
                collecting_list_key = None
                # fall through to process this line normally

        if collecting_dict_key is not None:
            dict_item = re.match(r"^\s+([\w]+)\s*:\s*(.+)$", raw_line)
            if dict_item:
                header[collecting_dict_key][dict_item.group(1).strip()] = dict_item.group(2).strip()
                continue
            else:
                collecting_dict_key = None
                # fall through to process this line normally

        block_match = re.match(r"^([\w]+)\s*:\s*$", line)
        if block_match:
            block_key = block_match.group(1).strip()
            if block_key in LIST_BLOCK_KEYS:
                header[block_key] = []
                collecting_list_key = block_key
                continue
            if block_key in DICT_BLOCK_KEYS:
                header[block_key] = {}
                collecting_dict_key = block_key
                continue

        match = re.match(r"^([\w]+)\s*:\s*(.+)$", line)
        if match:
            header[match.group(1).strip()] = match.group(2).strip()

    if not header:
        return None, "YAML header is empty"

    return header, ""


def lint_header(
    header: dict,
    allowed_projects: set[str],
    allowed_project_modules: set[tuple[str, str]] | None = None,
) -> list[str]:
    """Validate a parsed header. Return the list of errors."""
    errors: list[str] = []
    doc_type = header.get("type", "")

    # CONFIRM memos use separate rules
    if doc_type == "CONFIRM":
        for field in CONFIRM_REQUIRED:
            if field not in header or not header[field]:
                errors.append(f"CONFIRM required field missing: {field}")
        return errors

    # Required fields for general memos
    for field in REQUIRED_FIELDS:
        if field not in header or not header[field]:
            errors.append(f"Required field missing: {field}")
    if errors:
        return errors

    # Valid type values
    if doc_type not in VALID_TYPES:
        errors.append(
            f"Invalid type: '{doc_type}' (allowed: {', '.join(sorted(VALID_TYPES))})"
        )

    # Validate title length (P-001 validation 5)
    title = header.get("title", "")
    if len(title) > TITLE_MAX_LEN:
        errors.append(f"title must be {TITLE_MAX_LEN} characters or fewer")

    # Validate group_id (P-001 validations 9 and 10)
    group_id = (header.get("group_id") or "").strip()
    if doc_type not in GROUP_REQUIRED_EXEMPT:
        if not group_id:
            errors.append(f"{doc_type} type requires a group_id field")
        else:
            parsed = parse_group_id(group_id)
            if parsed is None:
                errors.append(
                    "group_id format error: {project}.{module}.{seq} (seq must be a 4-digit number)"
                )
            else:
                header_prefix = header.get("project", "") + "." + (header.get("module") or "")
                gid_prefix = parsed["project"] + "." + parsed["module"]
                if gid_prefix != header_prefix:
                    errors.append(
                    "group_id project/module does not match header values"
                    )

    # Validate target_id (P-001 validation 7) — extended to INBOX_TARGET_TYPES
    if doc_type in INBOX_TARGET_TYPES:
        target_id = (header.get("target_id") or "").strip()
        if not target_id:
            errors.append(f"{doc_type} type requires a target_id field")
        elif not TARGET_ID_DOC_ID_PATTERN.match(target_id):
            errors.append(
                "target_id format error: {project}-{module}-{TYPE}{seq} (e.g. server-test-R001)"
            )

    if doc_type == "TV":
        for field in TV_REQUIRED_FIELDS:
            if field not in header or not str(header[field]).strip():
                errors.append(f"TV required field missing: {field}")

        tv_type = (header.get("tv_type") or "").strip()
        if tv_type and tv_type not in VALID_TV_TYPES:
            errors.append(
                f"Invalid tv_type: '{tv_type}' "
                f"(allowed: {', '.join(sorted(VALID_TV_TYPES))})"
            )

        pass_criteria = (header.get("pass_criteria") or "").strip()
        if pass_criteria and pass_criteria not in VALID_PASS_CRITERIA:
            errors.append(
                f"Invalid pass_criteria: '{pass_criteria}' "
                f"(allowed: {', '.join(sorted(VALID_PASS_CRITERIA))})"
            )

        errors.extend(validate_target_id_prefix(target_id=(header.get("target_id") or ""), expected_prefix=TV_TARGET_PREFIX, field_label="TV.target_id"))
        errors.extend(validate_clear_scope(header.get("clear_scope")))

    if doc_type == "TVR":
        for field in TVR_REQUIRED_FIELDS:
            if field not in header or not str(header[field]).strip():
                errors.append(f"TVR required field missing: {field}")

        summary_status = (header.get("summary_status") or "").strip()
        if summary_status and summary_status not in VALID_TVR_SUMMARY_STATUS:
            errors.append(
                f"Invalid summary_status: '{summary_status}' "
                f"(allowed: {', '.join(sorted(VALID_TVR_SUMMARY_STATUS))})"
            )

        errors.extend(validate_target_id_prefix(target_id=(header.get("target_id") or ""), expected_prefix=TVR_TARGET_PREFIX, field_label="TVR.target_id"))

    # Validate approved_files (T036) — DC type only
    if doc_type == "DC":
        approved = header.get("approved_files")
        if not approved:
            errors.append("DC type requires at least one entry in the approved_files field")

    # Validate next (P-001 validation 8)
    if doc_type in NEXT_REQUIRED_TYPES:
        next_val = (header.get("next") or "").strip()
        if not next_val:
            errors.append(f"{doc_type} type requires a next field")
        elif next_val not in NEXT_VALID_VALUES:
            errors.append(
                f"Invalid next: '{next_val}' (allowed: D, P, L, N, T)"
            )

    # Allowed project set — skip validation when empty
    project = header.get("project", "")
    if allowed_projects and project not in allowed_projects:
        errors.append(
            f"Disallowed project: '{project}' (allowed: {', '.join(sorted(allowed_projects))})"
        )

    # Module validation rules:
    # 1) If (project, "") is registered, all modules are allowed
    # 2) Otherwise, an exact (project, module) match is required
    #    (including when module is an empty string)
    module = (header.get("module") or "").strip()
    if allowed_project_modules:
        if (project, "") in allowed_project_modules:
            pass
        elif (project, module) not in allowed_project_modules:
            errors.append(f"Disallowed module combination: '{project}/{module}'")

    errors.extend(
        validate_metadata_values(
            header.get("owner"),
            header.get("priority"),
            header.get("due_date"),
        )
    )

    return errors


def validate_metadata_values(
    owner: str | None,
    priority: str | None,
    due_date: str | None,
) -> list[str]:
    """Validate the format of optional metadata (owner/priority/due_date)."""
    errors: list[str] = []

    if priority is not None and str(priority).strip():
        p = str(priority).strip().lower()
        if p not in VALID_PRIORITIES:
            errors.append(
                f"Invalid priority: '{priority}' (allowed: {', '.join(sorted(VALID_PRIORITIES))})"
            )

    if due_date is not None and str(due_date).strip():
        raw = str(due_date).strip()
        try:
            # Validate YYYY-MM-DD format
            date.fromisoformat(raw)
            if len(raw) != 10:
                raise ValueError("invalid format")
        except ValueError:
            errors.append("due_date must be in YYYY-MM-DD format")

    # owner is optional and may be empty
    return errors


def extract_target_doc_type(target_id: str) -> str | None:
    """Extract the document-type prefix from target_id."""
    match = TARGET_ID_PREFIX_PATTERN.match(target_id.strip())
    if not match:
        return None
    return match.group("doc_type")


def validate_target_id_prefix(target_id: str, expected_prefix: str, field_label: str) -> list[str]:
    """Validate the target_id prefix for TV/TVR documents."""
    if not target_id:
        return []

    target_doc_type = extract_target_doc_type(target_id)
    if target_doc_type is None:
        return []

    if target_doc_type != expected_prefix:
        return [
            f"{field_label} must reference a {expected_prefix}-prefixed document "
            f"(got: {target_doc_type})"
        ]
    return []


def validate_clear_scope(clear_scope: object) -> list[str]:
    """Validate the TV clear_scope block format."""
    if clear_scope is None:
        return []

    if not isinstance(clear_scope, dict):
        return [
            "clear_scope format error: a db/filesystem true|false block is required under clear_scope"
        ]

    errors: list[str] = []
    required_keys = {"db", "filesystem"}
    missing_keys = sorted(required_keys - set(clear_scope.keys()))
    if missing_keys:
        errors.append(
                "clear_scope format error: db/filesystem entries are required"
        )

    for key, value in clear_scope.items():
        normalized = str(value).strip().lower()
        if normalized not in VALID_BOOL_LITERALS:
            errors.append(
                f"clear_scope.{key} value must be true or false"
            )

    return errors


def _extract_body_from_content(content: str) -> str:
    """Extract the body after the YAML header."""
    stripped = content.strip()
    if not stripped.startswith("---"):
        return stripped
    end_idx = stripped.find("---", 3)
    if end_idx == -1:
        return stripped
    return stripped[end_idx + 3:].strip()


def _lint_q_body(body: str) -> list[str]:
    """Validate Q document body format (new T408 rules).

    Rules:
    1. At least one ### Q heading must exist
    2. No headings other than ### Q are allowed (### Q1, ## question body, # ..., etc.)
    3. Reject when the body after any ### Q heading is empty
    4. Do not place two questions on one line (### Q ... ### Q ... twice on one line)
    """
    lines = body.split("\n")

    # Rule 4: ### Q appears two or more times on one line
    for ln in lines:
        stripped = ln.strip()
        if len(re.findall(r"###\s+Q(?!\w)", stripped)) >= 2:
            return [
                f"Q body format violation: '### Q' appears more than once on the same line. "
                f"Each question must be on a separate line: '{stripped[:60]}'"
            ]

    # Rule 2: no headings other than ### Q
    for ln in lines:
        stripped = ln.strip()
        if re.match(r"^#{1,6}\s", stripped) and not re.match(r"^###\s+Q\s*$", stripped):
            return [
                f"Q body format violation: headings other than '### Q' are not allowed: '{stripped[:60]}'"
            ]

    # Rule 1: at least one ### Q heading
    q_positions = [i for i, ln in enumerate(lines) if re.match(r"^###\s+Q\s*$", ln.strip())]
    if not q_positions:
        return [
            "Q body format violation: no '### Q' heading found. Each question must start with a '### Q' heading."
        ]

    # Rule 3: reject when the body of any ### Q section is empty
    for idx, pos in enumerate(q_positions):
        next_pos = q_positions[idx + 1] if idx + 1 < len(q_positions) else len(lines)
        body_lines = [ln for ln in lines[pos + 1:next_pos] if ln.strip()]
        if not body_lines:
            return [
                f"Q body format violation: the body after '### Q' heading #{idx + 1} is empty. "
                "Each Q section must contain a question body."
            ]

    return []


def _lint_ds_body(body: str) -> list[str]:
    """Validate DLP/DB mentions in a DS document body (T033).

    A DS document must explicitly mention the required design artifacts
    (D/L/P/DB) in the body.
    """
    if not body:
        return [
            "A DS document must include design instruction content (D/L/P/DB entries) in the body. "
            "An empty body cannot enter InBox."
        ]
    # At least one of D, L, P, or DB must be mentioned (word-boundary based)
    if not re.search(r"\b(DB|D|L|P)\b", body):
        return [
            "The DS document body must specify the design target type (D/DB/L/P). "
            "Example: 'D design doc', 'L logic design', 'DB design', 'P protocol'"
        ]
    return []


def lint_file_content(content: str, allowed_projects: set[str] | list[dict]) -> tuple[dict | None, list[str]]:
    """Run parsing + validation in one step. Return (header, errors)."""
    header, parse_error = parse_yaml_header(content)
    if parse_error:
        return None, [parse_error]

    allowed_project_names, allowed_project_modules = _normalize_allowed_projects(allowed_projects)
    errors = lint_header(header, allowed_project_names, allowed_project_modules)

    # T029: validate Q-type body question formatting
    # (each question must be on a separate line)
    # T033: validate missing DLP/DB mentions in DS-type bodies
    if not errors and header:
        doc_type = header.get("type", "")
        body = _extract_body_from_content(content)
        if doc_type == "Q":
            errors.extend(_lint_q_body(body))
        elif doc_type == "DS":
            errors.extend(_lint_ds_body(body))

    return header, errors


def parse_group_id(group_id: str) -> dict | None:
    """Split group_id into project, module, and seq.

    group_id format: {project}.{module}.{seq:04d}
    (dot separator, seq is a 4-digit number).
    """
    if not group_id:
        return None

    parts = group_id.split(".")
    if len(parts) != 3:
        return None

    project, module, seq_str = parts

    if not seq_str.isdigit() or len(seq_str) != GROUP_SEQ_DIGITS:
        return None

    return {
        "project": project,
        "module": module,
        "seq": int(seq_str),
    }
