-- flowgate.default.0229 T0004: bind resolve_conflict worker tokens to one merge session.
ALTER TABLE tokens ADD COLUMN merge_id INTEGER;
