"""Optional first-name scheduling port, wired by the application composition root."""
_handler = None
_title_handler = None
_request_handler = None


def register_title(handler, request_handler):
    global _title_handler, _request_handler
    _title_handler, _request_handler = handler, request_handler


def title_request(flow_id, locale="ko", action="chat", doc_id=None):
    if _request_handler is None or not flow_id:
        return ""
    try:
        return _request_handler(flow_id, locale, action, doc_id)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("flow naming request exception_type=%s", type(exc).__name__)
        return ""


def submitted(token, title, doc_id=None, flow_title=None):
    """``title`` names only the chat ``doc_id`` it was posted in; ``flow_title`` only the flow."""
    if _title_handler is None:
        return
    try:
        _title_handler(token, title, doc_id, flow_title)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("flow naming submission exception_type=%s", type(exc).__name__)


def register(handler):
    global _handler
    _handler = handler


def schedule(flow_id, user_id, revision_no):
    if _handler is None:
        return False
    try:
        return bool(_handler(flow_id, user_id, revision_no))
    except Exception:
        return False


_completion_handler = None


def register_completion(handler):
    global _completion_handler
    _completion_handler = handler


def completed(run):
    if _completion_handler is None:
        return False
    try:
        return bool(_completion_handler(run))
    except Exception as exc:
        import logging
        logging.getLogger(__name__).debug("flow naming skipped reason=completion_exception exception_type=%s", type(exc).__name__)
        return False
