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
    "WP",  # 0395 D0007 §7: work plan — advisory general-series document
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
    # A UTF-8 BOM survives str.strip() (U+FEFF is not whitespace), so without
    # this a BOM-prefixed body reports "no YAML header" and every frontmatter
    # guard downstream reads it as "nothing declared" — the fail-open T0004 2.3
    # closes. strip_bom is the one place that knowledge lives.
    content = strip_bom(content.strip())
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


# ── Submission header normalization (group 0460 T0004) ──────────────────────
# Pure function: repairs a *complete, high-confidence* collapsed
# "next_type -> next_type_detail -> project -> module -> group/group_id ->
# title -> target_id" sequence back into 7 separate lines — in the block's own
# line ending, so a CRLF document stays CRLF — at the very start of a submitted
# document body, on the first line of a leading ``---`` frontmatter, or in the
# section *immediately* below an "Instruction to include next document header"
# heading. This is a targeted
# display/storage repair for a known-good pattern, not a generic YAML/text
# parser — ambiguous, partial, reordered, or duplicated-key input is left
# byte-for-byte untouched (NR0003 finding: global `breaks`/`key:` splitting
# is explicitly out of scope; it would touch unrelated prose).

_NEXT_HEADER_SECTION_MARKER = "Instruction to include next document header"
_HEADER_CANDIDATE_WINDOW = 800
_SHORT_TARGET_ID_RE = re.compile(r"^[A-Za-z]{1,3}[0-9]{3,4}$")
# A project/module token is an ASCII slug (see doc-id grammar above); a title,
# a URL or a Korean phrase is not.
_HEADER_SLUG_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_HEADER_DOC_NUMBER_RE = re.compile(r"^[A-Za-z]{1,3}[0-9]{3,4}$")

# A duplicated/misplaced key label must abort the match rather than being
# silently absorbed into a neighboring lazy value (backtracking regexes will
# happily do that otherwise — e.g. "next_type_detail: X project: A project: B
# module: ..." would let next_type_detail's value stretch across the first
# "project:" to reach the second one, treating a real duplicate as one
# valid field). Each value capture is barred from crossing any key label —
# including `next_type:` itself, so a second next_type cannot hide inside
# next_type_detail's value either.
_ALL_HEADER_KEY_LABELS = (
    "next_type_detail:", "next_type:", "project:", "module:", "group_id:",
    "group:", "title:", "target_id:",
)
_NO_KEY_LOOKAHEAD = "".join(f"(?!{re.escape(_lbl)})" for _lbl in _ALL_HEADER_KEY_LABELS)

_HEADER_PREFIX_RE = re.compile(
    r"next_type:[ \t]*(?P<next_type>\S+)\s+"
    rf"next_type_detail:[ \t]*(?P<next_type_detail>(?:{_NO_KEY_LOOKAHEAD}.)+?)\s+"
    r"project:[ \t]*(?P<project>\S+)\s+"
    r"module:[ \t]*(?P<module>\S+)\s+"
    r"(?P<group_label>group_id|group):[ \t]*(?P<group>\S+)\s+"
    r"title:[ \t]*",
    re.DOTALL,
)
_HEADER_TARGET_ID_FIELD_RE = re.compile(r"target_id:[ \t]*(?P<target_id>\S+)")
_HEADER_TARGET_ID_LABEL_RE = re.compile(r"target_id:")
_TRAILING_TARGET_ID_LINE_RE = re.compile(
    r"\r?\n[ \t]*target_id:[ \t]*\S+[ \t]*(?=\r?\n|\Z)"
)


def _header_group_is_valid(group: str) -> bool:
    if group.isdigit() and len(group) == GROUP_SEQ_DIGITS:
        return True
    return parse_group_id(group) is not None


def _header_target_id_is_valid(target_id: str) -> bool:
    if _SHORT_TARGET_ID_RE.match(target_id):
        return True
    return bool(TARGET_ID_DOC_ID_PATTERN.match(target_id))


def _header_value_is_plausible(key: str, token: str) -> bool:
    """Could `token` really be the value of header field `key`?

    This is what separates "a title that happens to name a field" from "a
    second, real field collapsed into the line". T0004 2.1/2.3 forbid judging a
    colon on the key name alone: a title reading "project: <a Korean phrase>"
    names `project`, but that phrase is not a project token, so it stays
    authored prose — while "module: default group: 0460" carries a real group
    value and is a genuine collapse.
    """
    if not token:
        return False
    if key in {"next_type", "type"}:
        return token in VALID_TYPES
    if key in {"project", "module"}:
        return bool(_HEADER_SLUG_RE.match(token))
    if key in {"group", "group_id"}:
        return _header_group_is_valid(token)
    if key == "target_id":
        return _header_target_id_is_valid(token)
    if key == "doc_number":
        return bool(_HEADER_DOC_NUMBER_RE.match(token))
    # next_type_detail / type_detail / title take free text: any token fits.
    return True


# Longest label of each family first so `group_id` is not shadowed by `group`
# and `next_type_detail` is not shadowed by `next_type`.
_TITLE_FOREIGN_KEYS = (
    "next_type_detail", "next_type", "project", "module", "group_id", "group",
    "title",
)
_TITLE_FOREIGN_FIELD_RE = re.compile(
    r"(?<![\w-])(?P<key>" + "|".join(_TITLE_FOREIGN_KEYS) + r")[ \t]*:[ \t]*(?P<value>\S+)"
)


def _title_repeats_a_header_key(title: str) -> bool:
    """True when the title slice carries a *second* real occurrence of a header
    key (T0004 2.1: repair only when every key appears exactly once).

    `target_id` is deliberately absent from the scan: a repeated target id is
    resolved by the end-boundary rule (last valid id wins), not by aborting.
    """
    for m in _TITLE_FOREIGN_FIELD_RE.finditer(title):
        if _header_value_is_plausible(m.group("key"), m.group("value")):
            return True
    return False


# Both Markdown fence forms are fenced code (CommonMark 4.5): a ``` example and
# a ~~~ example must be equally untouchable.
# Every "end of line" here is `\r?$`, not `$`: in MULTILINE mode `$` matches
# *before* the "\n" of a CRLF pair, leaving the "\r" unmatched — so a plain `$`
# quietly stops recognizing fences, headings and delimiters the moment the
# document uses CRLF.
_FENCE_LINE_RE = re.compile(
    r"(?m)^[ \t]{0,3}(?P<fence>`{3,}|~{3,})[ \t]*(?P<info>[^\r\n]*?)[ \t]*\r?$"
)


def _header_is_inside_fence(text: str, index: int) -> bool:
    """True when `index` falls inside a fenced code block opened earlier.

    A fence is closed only by a fence of the *same* character, at least as
    long, and carrying no info string — so "```text" opens and "```" closes,
    while a "~~~" line inside a ``` block is content, not a delimiter.
    """
    open_fence: str | None = None
    for m in _FENCE_LINE_RE.finditer(text[:index]):
        fence = m.group("fence")
        if open_fence is None:
            open_fence = fence
            continue
        if (
            fence[0] == open_fence[0]
            and len(fence) >= len(open_fence)
            and not m.group("info")
        ):
            open_fence = None
    return open_fence is not None


_LEADING_FRONTMATTER_OPEN_RE = re.compile(r"\A\ufeff?[ \t]*---[ \t]*\r?\n")
# The marker only designates a candidate when it is the *section heading*
# itself. The same phrase quoted in a sentence ("see the 'Instruction to
# include next document header' section above") is prose and must not turn the
# next next_type line — possibly paragraphs away — into a repair target.
_NEXT_HEADER_SECTION_HEADING_RE = re.compile(
    r"(?m)^[ \t]{0,3}#{1,6}[ \t]+"
    + re.escape(_NEXT_HEADER_SECTION_MARKER)
    + r"[ \t]*:?[ \t]*\r?$"
)
# Between that heading and the block: blank lines and at most one "---" rule
# line (the shape mention_service emits). Nothing else — no prose, no second
# heading, no fence.
_NEXT_HEADER_SECTION_GAP_RE = re.compile(
    r"\r?\n(?:[ \t]*\r?\n)*(?:[ \t]*---[ \t]*\r?\n(?:[ \t]*\r?\n)*)?"
)


def _header_candidate_positions(text: str) -> list[int]:
    """Line-start offsets worth trying: document start, the first line inside a
    leading ``---`` frontmatter, and the line immediately below an 'Instruction
    to include next document header' *heading*."""
    positions: list[int] = []
    bom = 1 if text.startswith("\ufeff") else 0
    if text.startswith("next_type:", bom):
        positions.append(bom)
    # T0004 2.3: the very same collapsed sequence routinely arrives *inside* a
    # real ``---`` frontmatter. It has to be repaired here, before
    # frontmatter_parse_is_ambiguous() ever sees it — otherwise a recoverable,
    # high-confidence body is blanket-rejected as malformed (422) and the
    # identity comparison it is owed (match -> continue, conflict -> 409) never
    # runs at all.
    _fm_open = _LEADING_FRONTMATTER_OPEN_RE.match(text)
    if _fm_open is not None and text.startswith("next_type:", _fm_open.end()):
        positions.append(_fm_open.end())
    for heading in _NEXT_HEADER_SECTION_HEADING_RE.finditer(text):
        gap = _NEXT_HEADER_SECTION_GAP_RE.match(text, heading.end())
        if gap is None:
            continue
        if text.startswith("next_type:", gap.end()):
            positions.append(gap.end())
    return positions


def _match_header_sequence(window: str) -> dict | None:
    """Match one collapsed/partial header sequence at the start of `window`.

    The end boundary must be *confirmed*, not merely "the first thing that
    looks like a target id" (T0004 2.1: repair only when every key appears once
    and the end boundary is settled). The block ends with the line carrying the
    first `target_id:` label, so that line is the entire search region:

    * a title may legitimately contain the literal text "target_id: <id>", so
      the boundary is the *last* validating candidate on that line (T0004 2.1
      states this rule explicitly) and everything before it stays title text;
    * nothing but whitespace may follow the chosen value on that line, so a
      trailing stray key or sentence aborts the repair;
    * the title slice may not carry a second occurrence of any other header
      key, and the six leading keys are each barred from repeating by the
      prefix pattern itself.
    """
    prefix_match = _HEADER_PREFIX_RE.match(window)
    if not prefix_match:
        return None
    next_type = prefix_match.group("next_type").strip()
    # The *submitted* token has to be a registered document type. Accepting
    # next_type.upper() while writing the original spelling back out would
    # bless an unregistered "next_type: tr" by rewriting it into a
    # seven-line header, which is the opposite of strict validation.
    if next_type not in VALID_TYPES:
        return None
    group = prefix_match.group("group").strip()
    if not _header_group_is_valid(group):
        return None

    title_start = prefix_match.end()
    first_label = _HEADER_TARGET_ID_LABEL_RE.search(window, title_start)
    if first_label is None:
        return None
    line_break = window.find("\n", first_label.end())
    if line_break == -1:
        region_end = len(window)
    else:
        # Idempotency: once "title: X target_id: R0001 target_id: R0002" has been
        # repaired, the *title* line carries the first target_id label and the
        # real boundary has moved onto the next line. Whole target_id-only lines
        # following it therefore belong to the same region, or a second pass
        # would re-split the block at a different boundary.
        region_end = line_break
        while True:
            trailing = _TRAILING_TARGET_ID_LINE_RE.match(window, region_end)
            if trailing is None:
                break
            region_end = trailing.end()
    region = window[:region_end]
    candidates = [
        tm
        for tm in _HEADER_TARGET_ID_FIELD_RE.finditer(region, title_start)
        if _header_target_id_is_valid(tm.group("target_id").strip())
    ]
    if not candidates:
        # No validating target id on that line: the end boundary is not settled.
        return None
    tm = candidates[-1]
    if region[tm.end():].strip():
        # Something else still sits on the header's last line: the boundary is
        # not settled, so this is not the high-confidence pattern.
        return None
    title = window[title_start:tm.start()].strip()
    if not title or "\n" in title:
        # A title spanning a line break is not one field: the block is not the
        # high-confidence single-title pattern, so leave it alone rather than
        # fold several lines into one.
        return None
    if _title_repeats_a_header_key(title):
        # A second title/project/module/group/next_type field is hiding in what
        # would become the title. Folding a duplicate label into a value is
        # exactly what T0004 2.1 forbids: leave the text alone.
        return None
    return {
        "next_type": next_type,
        "next_type_detail": prefix_match.group("next_type_detail").strip(),
        "project": prefix_match.group("project").strip(),
        "module": prefix_match.group("module").strip(),
        "group_label": prefix_match.group("group_label"),
        "group": group,
        "title": title,
        "target_id": tm.group("target_id").strip(),
        "end": tm.end(),
    }


def _header_span_eol(span: str, text: str) -> str:
    """The line ending a header block must be rebuilt with.

    An already-correct submission has to come back byte-for-byte, and that
    includes its EOL: rebuilding a valid CRLF block with LF would rewrite bytes
    the caller never asked to change and report a phantom normalization, which
    T0004 2.1 ("leave already-correct multi-line input byte-for-byte alone")
    forbids. A genuinely collapsed block inherits the document's dominant EOL
    so the repair does not introduce a second line-ending style either.
    """
    if "\r\n" in span:
        return "\r\n"
    if "\n" in span:
        return "\n"
    crlf = text.count("\r\n")
    return "\r\n" if crlf > text.count("\n") - crlf else "\n"


def normalize_submission_header(text: str) -> tuple[str, list[dict]]:
    """Repair collapsed next-document-header sequences in a submission body.

    Pure function. Returns (normalized_text, normalizations) where each
    normalization record is
    {"kind": "collapsed_next_header", "line_start": <1-based>, "inserted_breaks": <int>}.
    Applying this function twice to its own output is a no-op (idempotent):
    an already-correct 7-line block reconstructs to the same bytes — including
    its own line ending, LF or CRLF — so no normalization is recorded and the
    text is returned unchanged.
    """
    if not text:
        return text, []

    normalizations: list[dict] = []
    out = text
    # Right-to-left so an earlier splice never invalidates a later offset.
    for pos in sorted(set(_header_candidate_positions(out)), reverse=True):
        if _header_is_inside_fence(out, pos):
            continue
        window = out[pos:pos + _HEADER_CANDIDATE_WINDOW]
        fields = _match_header_sequence(window)
        if fields is None:
            continue

        original_span = window[:fields["end"]]
        eol = _header_span_eol(original_span, out)
        replacement = eol.join([
            f"next_type: {fields['next_type']}",
            f"next_type_detail: {fields['next_type_detail']}",
            f"project: {fields['project']}",
            f"module: {fields['module']}",
            f"{fields['group_label']}: {fields['group']}",
            f"title: {fields['title']}",
            f"target_id: {fields['target_id']}",
        ])
        if replacement == original_span:
            continue

        span_end = pos + fields["end"]
        line_start = out.count("\n", 0, pos) + 1
        inserted_breaks = 6 - original_span.count("\n")
        out = out[:pos] + replacement + out[span_end:]
        normalizations.append({
            "kind": "collapsed_next_header",
            "line_start": line_start,
            "inserted_breaks": inserted_breaks,
        })

    normalizations.sort(key=lambda n: n["line_start"])
    return out, normalizations


_FRONTMATTER_KNOWN_KEYS = (
    "next_type", "next_type_detail", "project", "module", "group",
    "group_id", "title", "target_id", "type", "type_detail", "doc_number",
)
# Longest label first so "group_id" is never shadowed by "group"; the
# lookbehind keeps "type" from matching inside "next_type".
_FRONTMATTER_EMBEDDED_KEY_RE = re.compile(
    r"(?<![\w-])(?P<key>"
    + "|".join(re.escape(k) for k in sorted(_FRONTMATTER_KNOWN_KEYS, key=len, reverse=True))
    + r")[ \t]*:[ \t]*(?P<value>\S+)"
)
# A line is a partial collapse when at least two *known* key traces share it:
# the key the parser actually kept, plus one or more further key/value pairs
# that got swallowed into its value. Two traces is the smallest chain that can
# exist ("module: default group: 0460"), and any longer chain trips the same
# test. A lone key-shaped fragment inside a value is ordinary authored text — a
# title naming the field, a note, a URL, a doc excerpt — and T0004 2.3
# explicitly forbids judging it on the key name alone, so the embedded pair
# must also carry a *plausible value* for that key before it counts.
_FRONTMATTER_MIN_KEY_TRACES = 2


def _frontmatter_embedded_fields(value: object) -> set[str]:
    """Distinct known keys that appear inside a *scalar* value as a plausible
    key/value pair.

    Lists and nested dicts are deliberately not inspected: normal list/nested
    handling is a preserved parse contract (T0004 2.3), and a list item such as
    "- target_id: <an example>" is authored content, not evidence of a collapse.
    """
    if not isinstance(value, str):
        return set()
    return {
        m.group("key")
        for m in _FRONTMATTER_EMBEDDED_KEY_RE.finditer(value)
        if _header_value_is_plausible(m.group("key"), m.group("value"))
    }


def _frontmatter_entry_is_collapsed(key: str, value: object) -> bool:
    embedded = _frontmatter_embedded_fields(value)
    if not embedded:
        return False
    traces = set(embedded)
    if key in _FRONTMATTER_KNOWN_KEYS:
        traces.add(key)
    return len(traces) >= _FRONTMATTER_MIN_KEY_TRACES


def strip_bom(text: str) -> str:
    """Drop a leading UTF-8 BOM. ``str.strip()`` does not: U+FEFF is not
    whitespace, so every "does this start with ---?" test has to go through
    here or a BOM-prefixed frontmatter silently stops being frontmatter."""
    return text[1:] if text.startswith("\ufeff") else text


def looks_like_frontmatter(text: object) -> bool:
    """True when `text` opens a ``---`` frontmatter block, BOM or not.

    Callers gate their frontmatter checks on this so the ambiguity verdict, the
    identity comparison and parse_yaml_header all agree on *what counts as
    frontmatter* — a BOM-prefixed body must not slip past a guard that a
    BOM-less one is held to.
    """
    if not isinstance(text, str):
        return False
    return strip_bom(text).lstrip().startswith("---")


# `\r?$` for the same reason as _FENCE_LINE_RE: a CRLF document's closing
# delimiter line is "---\r" as far as MULTILINE `$` is concerned.
_FRONTMATTER_CLOSE_LINE_RE = re.compile(r"(?m)^[ \t]*---[ \t]*\r?$")


def frontmatter_parse_is_ambiguous(text: str) -> bool:
    """True when `text` opens a real ``---``-delimited frontmatter block whose
    parse is unclosed, truncated, or plausibly *partial* — one or more collapsed
    "key: value" pairs swallowed into a single line's greedy value (the
    NR0003 finding: parse_yaml_header's first-key + greedy-rest-of-line regex
    accepts this silently, so a later identity check on a field that never
    made it into the header dict sees "absent" rather than "conflicting" and
    fail-opens). Callers use this to turn that fail-open into an explicit
    reject before the identity-mismatch guard runs. Returns False for text
    that is not `---`-delimited at all — that is normal document body, not
    frontmatter, and is out of scope here.
    """
    if not isinstance(text, str):
        return False
    body = strip_bom(text).strip()
    if not body.startswith("---"):
        return False
    close = _FRONTMATTER_CLOSE_LINE_RE.search(body, 3)
    if close is None:
        # Opened and never really closed. Three hyphens sitting inside an
        # ordinary scalar ("title: a --- b") are not a delimiter — only a
        # standalone "---" line is — so accepting the first literal "---" here
        # would fail a malformed body open.
        return True
    if body.find("---", 3) != close.start():
        # parse_yaml_header cuts the block at the first literal "---", so when
        # that is not the real delimiter line, the header dict it returns is a
        # truncated parse: fields past the cut never got compared.
        return True
    header, parse_error = parse_yaml_header(body)
    if parse_error or not isinstance(header, dict):
        return True
    return any(_frontmatter_entry_is_collapsed(k, v) for k, v in header.items())


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
