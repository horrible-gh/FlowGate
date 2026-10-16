"""Front matter identity fields of a converted root (0064 L0005 2.6.1).

The judgement (``root_conversion.judge_root_conversion``) and the rewrite of the staged body share
``check``: both read the same ``type`` / ``doc_number`` values with the same rules. A rewrite changes
only the bytes of those two values; the key, the space after the colon, a comment, the line ending
(LF, CRLF, none), the BOM, every other line and the body after the front matter keep every byte.

Accepted values are one-line YAML scalars: plain (``type: R``, ``type: R  # memo``), single quoted
(``'R'``, ``''`` for a quote) and double quoted (``"0001-R"``, only ``\\"`` / ``\\\\`` escapes).
Anything else — an empty value, a block scalar, a flow collection, an anchor / alias / tag, an
unclosed quote, a value continued on the next line, a lone ``\\r`` line ending — is unsafe, as are
a duplicated key, a value naming another document and a front matter without its end delimiter.
A file without a start delimiter has no identity fields and is copied as it is.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field
from typing import Optional

from modules.flow_gate.utils.type_code import DOC_CODE_RE

BOM = b"\xef\xbb\xbf"
IDENTITY_KEYS = ("type", "doc_number")
_KEY_RE = re.compile(rb"^([A-Za-z_][A-Za-z0-9_-]*)[ \t]*:(?=[ \t\r\n]|$)")
_START, _ENDS = b"---", (b"---", b"...")


@dataclass(frozen=True)
class Scalar:
    value: str
    start: int          # value span inside its line, quotes included (bytes)
    end: int
    style: str          # "plain" | "single" | "double"


@dataclass
class IdentityCheck:
    safe: bool
    reason: Optional[str] = None
    field: Optional[str] = None
    bom: bytes = b""
    lines: list = dc_field(default_factory=list)
    fields: dict = dc_field(default_factory=dict)        # key -> (line index, Scalar)


class IdentityUnsafe(ValueError):
    def __init__(self, check: IdentityCheck):
        super().__init__(check.reason)
        self.check = check


def doc_code_alternates(code: str) -> set[str]:
    """Both spellings of one document number (``0001-R`` and ``R0001``), the rule of
    ``api.inbox_routes._doc_code_alternates``."""
    code = (code or "").strip()
    alts = {code}
    m = DOC_CODE_RE.fullmatch(code.upper())
    if m and m["type"].isalpha():
        alts.add(f"{m['type']}{m['seq']}")
    m = re.fullmatch(r"([A-Za-z]+)(\d+)", code)
    if m and m.group(1).upper().isalpha():
        alts.add(f"{m.group(2)}-{m.group(1).upper()}")
    return alts


def _compact(code: str) -> str:
    m = DOC_CODE_RE.fullmatch(code.upper())
    return f"{m['type']}{m['seq']}" if m else code


def _split_lines(body: bytes) -> list[bytes]:
    """Split after each LF; every line keeps its own line ending (LF, CRLF or none)."""
    out, start = [], 0
    while start < len(body):
        end = body.find(b"\n", start)
        if end < 0:
            out.append(body[start:])
            break
        out.append(body[start:end + 1])
        start = end + 1
    return out


def _content(line: bytes) -> bytes:
    if line.endswith(b"\r\n"):
        return line[:-2]
    return line[:-1] if line.endswith(b"\n") else line


def _delimiter(line: bytes) -> bytes:
    text = _content(line)
    return text[:-1] if text.endswith(b"\r") else text


def _rest_is_blank_or_comment(rest: bytes) -> bool:
    stripped = rest.lstrip(b" \t")
    return not stripped or (stripped.startswith(b"#") and len(stripped) < len(rest))


def _parse_scalar(line: bytes, colon_end: int) -> Optional[Scalar]:
    text = _content(line)
    if b"\r" in text:                                   # a lone CR line ending
        return None
    start = colon_end
    while start < len(text) and text[start:start + 1] in (b" ", b"\t"):
        start += 1
    if start >= len(text):
        return None
    head = text[start:start + 1]
    if head in (b"|", b">", b"[", b"{", b"&", b"*", b"!", b"#"):
        return None
    try:
        if head == b"'":
            i, chars = start + 1, []
            while i < len(text):
                if text[i:i + 1] == b"'":
                    if text[i + 1:i + 2] == b"'":
                        chars.append(b"'")
                        i += 2
                        continue
                    if not _rest_is_blank_or_comment(text[i + 1:]):
                        return None
                    return Scalar(b"".join(chars).decode("utf-8"), start, i + 1, "single")
                chars.append(text[i:i + 1])
                i += 1
            return None
        if head == b'"':
            i, chars = start + 1, []
            while i < len(text):
                ch = text[i:i + 1]
                if ch == b"\\":
                    nxt = text[i + 1:i + 2]
                    if nxt not in (b'"', b"\\"):
                        return None
                    chars.append(nxt)
                    i += 2
                    continue
                if ch == b'"':
                    if not _rest_is_blank_or_comment(text[i + 1:]):
                        return None
                    return Scalar(b"".join(chars).decode("utf-8"), start, i + 1, "double")
                chars.append(ch)
                i += 1
            return None
        cut = len(text)
        for marker in (b" #", b"\t#"):
            at = text.find(marker, start)
            if at >= 0:
                cut = min(cut, at)
        value = text[start:cut].rstrip(b" \t")
        if not value:
            return None
        return Scalar(value.decode("utf-8"), start, start + len(value), "plain")
    except UnicodeDecodeError:
        return None


def check(data: bytes, type_code: str, doc_code: str) -> IdentityCheck:
    """Read the identity fields of ``data`` (the body of the document ``type_code`` / ``doc_code``)."""
    bom, body = (BOM, data[len(BOM):]) if data.startswith(BOM) else (b"", data)
    lines = _split_lines(body)
    if not lines or _delimiter(lines[0]) != _START:
        return IdentityCheck(True, bom=bom, lines=lines)
    end = next((i for i in range(1, len(lines)) if _delimiter(lines[i]) in _ENDS), None)
    if end is None:
        return IdentityCheck(False, "missing_end_delimiter", bom=bom, lines=lines)
    fields: dict = {}
    for i in range(1, end):
        m = _KEY_RE.match(lines[i])
        key = m.group(1).decode("ascii") if m else None
        if key not in IDENTITY_KEYS:
            continue
        if key in fields:
            return IdentityCheck(False, "duplicate_key", key, bom=bom, lines=lines)
        scalar = _parse_scalar(lines[i], m.end())
        if scalar is None or (i + 1 < end and lines[i + 1][:1] in (b" ", b"\t")):
            return IdentityCheck(False, "unsupported_scalar", key, bom=bom, lines=lines)
        value = scalar.value.strip()
        if key == "type" and value.upper() != (type_code or "").strip().upper():
            return IdentityCheck(False, "identity_mismatch", key, bom=bom, lines=lines)
        if key == "doc_number" and value not in doc_code_alternates(doc_code):
            return IdentityCheck(False, "identity_mismatch", key, bom=bom, lines=lines)
        fields[key] = (i, scalar)
    return IdentityCheck(True, bom=bom, lines=lines, fields=fields)


def rewrite(data: bytes, type_code: str, doc_code: str, new_type: str, new_code: str) -> bytes:
    """``data`` with only the identity values replaced; raises ``IdentityUnsafe`` when that is not safe."""
    found = check(data, type_code, doc_code)
    if not found.safe:
        raise IdentityUnsafe(found)
    if not found.fields:
        return data
    lines = list(found.lines)
    for key, (i, scalar) in found.fields.items():
        if key == "type":
            value = new_type
        else:
            value = new_code if DOC_CODE_RE.fullmatch(scalar.value.strip().upper()) else _compact(new_code)
        quoted = {"single": f"'{value}'", "double": f'"{value}"'}.get(scalar.style, value)
        lines[i] = lines[i][:scalar.start] + quoted.encode("utf-8") + lines[i][scalar.end:]
    result = found.bom + b"".join(lines)
    again = check(result, new_type, new_code)
    if not again.safe or set(again.fields) != set(found.fields):
        raise IdentityUnsafe(again if not again.safe else IdentityCheck(False, "identity_mismatch"))
    return result
