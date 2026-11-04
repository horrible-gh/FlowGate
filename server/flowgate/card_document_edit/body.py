"""One strict byte read under the row lock; the same bytes become the backup."""
from pathlib import Path
from modules.flow_gate.storage import body as storage_body, files
from .contracts import Body


def read_body_bytes(doc):
    path, state = storage_body.body_path_state(doc)
    if path is None:
        return Body(state)
    path = Path(path)
    try:
        identity = files.file_identity(path)
        data = path.read_bytes()
        if identity is None or identity != files.file_identity(path):
            return Body("read_error", path=path)
        try:
            text = data.decode("utf-8", errors="strict")
        except UnicodeError:
            return Body("read_error", data, None, path, identity)
        return Body("readable", data, text, path, identity)
    except (OSError, UnicodeError):
        return Body("read_error", path=path)


def keep_sibling_exists(path):
    return path is not None and any(p.name.startswith(path.name + ".keep-") for p in path.parent.iterdir())
