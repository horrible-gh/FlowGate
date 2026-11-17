"""Conversation image delivery at the concrete provider boundary."""
from pathlib import Path
import os
import shutil

from modules.flow_gate.db.connection import get_store
from modules.flow_gate.services import message_image_service as images


class ConversationMention(str):
    """Text plus the already-resolved context window; never recalculate the range."""
    def __new__(cls, text, context):
        value = super().__new__(cls, text)
        value.image_context = context
        return value


def image_count(conversation_id, after_seq):
    row = get_store()._fetch_one(
        "SELECT COUNT(*) AS n FROM message_image_links l "
        "JOIN message_images i ON i.image_id = l.image_id "
        "WHERE l.conversation_id = ? AND l.seq > ?", [conversation_id, after_seq])
    return int(row["n"]) if row else 0


def copy_notice(context):
    count = image_count(context["conversation_id"], context["after_seq"])
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


def provider_prompt(prompt, provider, run):
    context = run.get("message_image_context")
    if not context:
        return prompt
    total = image_count(context["conversation_id"], context["after_seq"])
    if not total:
        return prompt
    ko = context["locale"] == "ko"
    lines = ["## 메시지 이미지" if ko else "## Message images"]
    delivered = 0
    omitted = 0
    failures = 0
    if images.provider_accepts_images(provider):
        rows = images.images_for_window(context["conversation_id"], context["after_seq"], limit=10)
        omitted = max(0, total - len(rows))
        for row in rows:
            try:
                path = _copy(row, run["token_scratch_dir"])
            except (OSError, ValueError, KeyError):
                failures += 1
                continue
            delivered += 1
            lines.append(f"- 메시지 #{row['seq']}: {path}" if ko else f"- Message #{row['seq']}: {path}")
        if delivered:
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
