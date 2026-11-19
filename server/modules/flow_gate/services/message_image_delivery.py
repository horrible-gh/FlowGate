"""Conversation image delivery at the concrete provider boundary."""
from pathlib import Path
import os
import shutil
import shlex

from modules.flow_gate.db.connection import get_store
from modules.flow_gate.services import message_image_service as images


class ConversationMention(str):
    """Text plus the already-resolved context window; never recalculate the range."""
    def __new__(cls, text, context):
        value = super().__new__(cls, text)
        value.image_context = context
        return value


def image_count(conversation_id, after_seq, through_seq=None):
    row = get_store()._fetch_one(
        "SELECT COUNT(*) AS n FROM message_image_links l "
        "JOIN message_images i ON i.image_id = l.image_id "
        "WHERE l.conversation_id = ? AND l.seq > ?"
        + (" AND l.seq <= ?" if through_seq is not None else ""),
        [conversation_id, after_seq, *([through_seq] if through_seq is not None else [])])
    return int(row["n"]) if row else 0


def copy_notice(context):
    count = image_count(context["conversation_id"], context["after_seq"], **({"through_seq": context["through_seq"]} if context.get("through_seq") is not None else {}))
    if not count:
        return ""
    if context["locale"] == "ko":
        return f"이미지 {count}장은 복사 경로로 전달되지 않음."
    return f"{count} image(s) are not delivered through the copy path."


def _copy(row, scratch):
    root = Path(scratch).resolve(strict=True)
    folder = root / "images"
    if folder.is_symlink() or not folder.resolve().is_relative_to(root):
        raise ValueError("image_scratch_outside_root")
    folder.mkdir(mode=0o700, exist_ok=True)
    ext = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}[row["media_type"]]
    target = folder / f"{int(row['seq'])}-{int(row['position'])}.{ext}"
    if target.is_symlink() or not target.resolve().is_relative_to(root):
        raise ValueError("image_scratch_outside_root")
    # Exclusive creation prevents following links or truncating existing hardlinks.
    # Retry files are replaced by unlinking the directory entry, never by truncation.
    if target.exists():
        target.unlink()
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as dest, open(row["abs_path"], "rb") as source:
            shutil.copyfileobj(source, dest)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return str(target)


def codex_image_argument(path):
    """Quote a generated filename for the local shell, never user message text.

    Codex splits comma-separated image arguments even inside quotes. Reject that
    case and cmd.exe expansion characters rather than silently attaching another
    file. Normal scratch paths do not contain these characters.
    """
    if any(c in path for c in (",", "\r", "\n", "\x00")):
        raise ValueError("image_path_not_representable")
    if os.name == "nt":
        if any(c in path for c in ('"', "%", "!")):
            raise ValueError("image_path_not_representable")
        return '"' + path + '"'
    return shlex.quote(path)


def _remote_prompt(prompt, provider, run, context, total):
    """Offer authenticated downloads; never send a server-local scratch path."""
    from urllib.parse import quote
    ko = context["locale"] == "ko"
    base = (run.get("api_base_url") or "").rstrip("/")
    rows = images.images_for_window(context["conversation_id"], context["after_seq"], through_seq=context.get("through_seq"))
    lines = ["## 메시지 이미지" if ko else "## Message images"]
    if base:
        for row in rows:
            url = (f"{base}/conversation/{quote(context['conversation_id'], safe='')}/images/"
                   f"{quote(row['image_id'], safe='')}/content")
            lines.append(f"- Message #{row['seq']}, image {row['position'] + 1} ({row['media_type']}): GET {url}")
        lines.append(
            "위 URL을 이 대화의 Authorization: Bearer 작업 토큰으로 GET하여 현재 작업 디렉터리에 이미지 파일로 저장한 뒤 "
            "Read 또는 view_image 이미지 도구로 반드시 열어 확인하십시오. 토큰은 위 대화 안내에 있습니다. "
            "URL 문자열만 읽고 이미지를 봤다고 하지 마십시오. 다운로드는 토큰을 소모하지 않습니다. 답변 제출 전에 확인하십시오."
            if ko else
            "GET these URLs with the conversation's Authorization: Bearer worker token shown above. "
            "Save the binary responses as image files in your current working directory, then open them with "
            "the Read or view_image image tool before answering. Reading a URL is not viewing its image. "
            "Downloads do not consume the token; download before submitting your reply.")
    else:
        lines.append("Image download unavailable: API base URL is missing.")
    offered = len(rows) if base else 0
    omitted = max(0, total - len(rows))
    if omitted:
        lines.append(f"오래된 이미지 {omitted}장은 최근 10장 한도로 제외했습니다." if ko else
                     f"{omitted} older image(s) omitted by the latest-10 limit.")
    # URLs were offered, not a confirmed model download or visual inspection.
    diagnostic = dict(images_total=total, images_delivered=0, images_undelivered=total,
                      images_download_available=offered, images_omitted=omitted,
                      images_copy_failed=0, provider_id=provider.get("id"))
    run.update(diagnostic)
    run.setdefault("message_image_diagnostics", []).append(diagnostic)
    return prompt + "\n\n" + "\n".join(lines)


def provider_prompt(prompt, provider, run):
    # Attempts share a run: clear attachments even on no-context/no-image returns.
    run.pop("message_image_cli_args", None)
    context = run.get("message_image_context")
    if not context:
        return prompt
    total = image_count(context["conversation_id"], context["after_seq"], **({"through_seq": context["through_seq"]} if context.get("through_seq") is not None else {}))
    if not total:
        return prompt
    if provider.get("exec_location") == "remote" and images.provider_accepts_images(provider):
        return _remote_prompt(prompt, provider, run, context, total)
    ko = context["locale"] == "ko"
    lines = ["## 메시지 이미지" if ko else "## Message images"]
    delivered = 0
    omitted = 0
    failures = 0
    cli_args = []
    codex = provider.get("kind") == "codex"
    if images.provider_accepts_images(provider):
        rows = images.images_for_window(context["conversation_id"], context["after_seq"], limit=10, through_seq=context.get("through_seq"))
        omitted = max(0, total - len(rows))
        for row in rows:
            try:
                path = _copy(row, run["token_scratch_dir"])
                if codex:
                    cli_args.extend(["--image", codex_image_argument(path)])
            except (OSError, ValueError, KeyError):
                failures += 1
                continue
            delivered += 1
            lines.append(f"- 메시지 #{row['seq']}: {path}" if ko else f"- Message #{row['seq']}: {path}")
        if delivered:
            if codex:
                run["message_image_cli_args"] = {"provider_id": provider.get("id"), "args": cli_args}
                lines.append("위 순서의 이미지를 Codex --image 입력으로 첨부합니다." if ko else
                             "These images are attached as Codex --image inputs in the order above.")
            else:
                lines.append("이미지 파일을 Read 도구로 열어 확인하십시오." if ko else "Open these image files with the Read tool.")
        if omitted:
            lines.append(f"오래된 이미지 {omitted}장은 최근 10장 한도로 제외했습니다." if ko else f"{omitted} older image(s) omitted by the latest-10 limit.")
        if failures:
            lines.append(f"이미지 {failures}장은 파일 복사 실패로 전달되지 않음." if ko else f"{failures} image(s) not delivered because file copying failed.")
    else:
        lines.append(f"이미지 {total}장이 있으나 이 연결로는 전달되지 않음." if ko else f"{total} image(s) are present but not delivered through this connection.")
    diagnostic = dict(images_total=total, images_delivered=delivered,
                      images_undelivered=total-delivered, images_omitted=omitted,
                      images_copy_failed=failures, provider_id=provider.get("id"))
    run.update(diagnostic)
    run.setdefault("message_image_diagnostics", []).append(diagnostic)
    return prompt + "\n\n" + "\n".join(lines)


def diagnostic_history(run):
    """Use the durable run history JSON without changing attempt counters in memory."""
    return [{"reason": "message_images", **item}
            for item in run.get("message_image_diagnostics", [])]
