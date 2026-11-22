"""Optional first-name scheduling port, wired by the application composition root."""
_handler = None


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
