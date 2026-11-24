"""Optional first-name scheduling port, wired by the application composition root."""
import logging

log = logging.getLogger(__name__)

_handler = None


def register(handler):
    global _handler
    _handler = handler


def schedule(flow_id, user_id, revision_no):
    if _handler is None:
        log.info("flow naming skipped reason=run_handler_missing")
        return False
    try:
        return bool(_handler(flow_id, user_id, revision_no))
    except Exception as exc:
        log.warning("flow naming skipped reason=run_handler_exception exception_type=%s", type(exc).__name__)
        return False


_completion_handler = None


def register_completion(handler):
    global _completion_handler
    _completion_handler = handler


def completed(run):
    if _completion_handler is None:
        log.info("flow naming skipped reason=completion_handler_missing")
        return False
    try:
        return bool(_completion_handler(run))
    except Exception as exc:
        log.warning("flow naming skipped reason=completion_exception exception_type=%s", type(exc).__name__)
        return False
