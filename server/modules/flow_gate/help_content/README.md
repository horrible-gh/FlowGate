# Help asset contract (F107, card 1)

JSON files are UTF-8. This directory contains data only. The loader does not execute files.

## Ownership

- manifest.json, routes.json, aliases.json, and language _index.json files belong to the framework card.
- conversation, documents, attachments belong to the documents migration card (owner 2).
- notices, cards, question, submit, source_tools, authoring_guide belong to owner 3.
- mention belongs to the later mention migration (owner 4); no mention call site is changed here.
- cards/state.json is the one example item, supplied in all three languages.

## Filling a pending item

Replace a pending file with an object containing locale, title, summary, purpose, request, response, errors for usage items. Guide items use body instead of request/response/errors. Remove status: pending. Each field may contain text, arrays or objects. Keep field keys language-neutral and translate all explanatory text together. Supply Korean first. Missing or pending en/ja falls back to the complete Korean item; missing Korean for a populated item is an error. Pending Korean is an explicit migration slot, never a public empty body: it continues to use the owning legacy supplier.

Use {{route:document.read}} for addresses and {{doc_id}} for declared context values. Substitution is one pass; braces inside original data are not evaluated. Missing context becomes {doc_id}. A placeholder occupying the complete field preserves its JSON type. Inside a larger string, text is inserted verbatim and other JSON values use JSON serialization (Unicode preserved; booleans/null use JSON spelling). Substituted values are never interpreted again. Original fields are recorded as JSON pointer paths. Actual Bearer values are never supplied; token expands to a placeholder only. Routes are resolved from the caller base URL, including the distinct v2/ai and v2/chat API families.

Dynamic item paths source_tools/<tool>, authoring_guide/<type>, design_template/<type> and mention/<block> use the wildcard manifest definition. They can be added without editing code or the shared manifest. Both the Korean file and the translated file must satisfy the same schema. The mention loader is provided for the later card and has no current mention callers.

## Validation and language metadata

loader.validate_catalog validates all fixed items and aliases. load_item validates dynamic files when read. Unknown references, root traversal, absent required sections, undeclared locale, literal API addresses, and clearly foreign-script prose fail validation. Inline code is neutral. This deterministic script check is a guard, not a general natural-language classifier; translation quality still needs review.

Responses carry language.requested_locale, resolved_locale, fallback_fragments and omitted_fragments. File-backed bulk responses are rebuilt together in Korean when a file requires fallback. Original user text is marked separately and is not translated. Legacy responses are marked content_source: legacy: their historical mixed prose is retained until their owner migrates the body. Do not interpret legacy metadata as a guarantee that old prose has been translated.

## Compatibility boundaries

legacy/compat owns console rendering, document access, retained type/group data, and external design template supply. legacy/docs_area and legacy/cards_area own the remaining old suppliers and their exports. Removing an area is allowed only when all its items are populated, legacy imports/exports are no longer used, and the console/retained-item/mention regressions pass. Locale and chat contract snapshots are deliberately independent so an area's deletion cannot invalidate compat. Duplicated unused prose in these snapshots remains to be pruned before declaring that migration complete.

Old aliases resolve through aliases.json. Retained documents/doc_type and documents/group_list keep their data suppliers and are omitted from the worker child index. Removed flat worker names stay unknown. Console flat responses use the frozen compatibility renderer.

## Bounded verification

python3 -m pytest -q tests/modules/flow_gate/test_help_assets_0107.py

Run only through FlowGate Self-check with cwd=server and timeout_seconds=60. The managed host resolves python3; pytest and python returned selfcheck_executable_not_found. Run scr_ec217ff39a384f7381d1d22b was accepted but remained running without a result; cancellation was accepted but returned selfcheck_cancel_unconfirmed (recovery_incomplete). Verification is on hold pending managed-run recovery; no test success is claimed. Remote writes independently perform Python/JSON syntax checks.
