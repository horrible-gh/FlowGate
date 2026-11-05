"""The pure TR2 builder shared by window and Agent revision transports."""
from modules.flow_gate.documents import tr2_service as tr2
from .contracts import Built


def build(raw, doc):
    tr2._assert_mutable(doc)
    body = tr2.parse(raw) if isinstance(raw, str) else raw
    if not isinstance(body, dict):
        tr2._invalid("body", "object required")
    dropped = [key for key in ("codebase_root", "source_honey") if key in body]
    validated = tr2.validate(body, doc=doc)
    tr2.verify_pair(doc["doc_id"], validated)
    canonical = tr2.canonicalize(validated)
    spec = canonical["edit_spec"]
    tr2.target_set(spec)
    root = tr2.resolve_source_root(doc["project_id"], doc["group_id"])
    canonical["baseline_fingerprint"] = tr2.target_fingerprint(spec, root)
    derived = {"files": tr2.derived_files(spec, root), "spec_fingerprint": tr2.spec_fingerprint(spec)}
    path = tr2.canonical_path_for_doc(doc)
    return Built(tr2.dumps(canonical).encode("utf-8"), target_path=path, row_updates={"filename": path.name},
                 snapshot_policy="CURRENT", body_saved_source="tr2_save",
                 result={"body": canonical, "derived": derived, "dropped_keys": dropped})
