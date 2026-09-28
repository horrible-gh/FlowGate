import type { Translate } from './apiErrors'

/**
 * A single Work Plan validation error as work_plan_service.render_errors() sends it
 * (T0007 §1.1): `params` is locale-independent -- a `what_key` (see `EMPTY_SELECTION_WHAT`
 * below) is carried as its stable identifier, not a word already picked in one locale -- so
 * a client can re-render `msg` in any locale from `code`+`params` alone.
 */
export interface WpFieldError {
  readonly loc: string
  readonly key: string | null
  readonly code: string
  readonly params: Readonly<Record<string, unknown>>
  readonly msg: string
}

// Every code work_plan_service._ERROR_COPY can put in a save/create response. Kept as its
// own registry (not apiErrors.ts's normalizeApiError/resolveApiError) because the Work Plan
// error envelope is `{code, errors: [...]}` at the response's top level, not the
// `response.data.error`/`response.data.detail` shapes that module already commits to.
const KNOWN_CODES: ReadonlySet<string> = new Set([
  'json_parse_failed',
  'wp_version_invalid',
  'wp_version_unsupported',
  'missing_field',
  'type_invalid',
  'unknown_field',
  'enum_not_allowed',
  'binding_not_allowed',
  'empty_selection',
  'unknown_type_code',
  'duplicate_type',
  'quantities_key_mismatch',
  'unit_mismatch',
  'count_not_integer',
  'count_out_of_range',
  'key_format_invalid',
  'duplicate_key',
  'steps_quantity_mismatch',
  'steps_too_many',
  'step_shape_mismatch',
  'locked_flag_mismatch',
  'provider_not_allowed',
  'note_not_allowed',
  'origin_not_allowed',
  'provider_not_candidate',
  'provider_id_format_invalid',
  'duplicate_provider_candidate',
  'provider_candidates_too_many',
  'note_too_long',
  'note_has_control_char',
  'provider_display_name_without_provider_id',
  'review_count_invalid',
  'reviewer_not_allowed',
  'reviewer_provider_unavailable',
  'reviewer_display_name_without_provider_id',
  'pre_instruction_too_long',
  'pre_instruction_control_char',
  'pre_instruction_not_allowed',
  'pre_instruction_attachment_digest_invalid',
  'pre_instruction_attachment_ai_forbidden',
  'pre_instruction_attachment_doc_mismatch',
  'pre_instruction_attachment_reserved_name_required',
  'pre_instruction_attachment_registry_missing',
  'pre_instruction_attachment_file_missing',
  'pre_instruction_attachment_original_name_mismatch',
  'pre_instruction_attachment_digest_mismatch',
  'pre_instruction_attachment_outside_storage',
])

// Keys `empty_selection`'s `what_key` param can carry (mirrors work_plan_service's
// _EMPTY_SELECTION_WHAT) -- each has a `main.work_plan.empty_selection_what.<key>` entry in
// every locale file, resolved at render time so the noun follows the current UI locale.
const EMPTY_SELECTION_WHAT_KEYS: ReadonlySet<string> = new Set(['counted_types', 'provider_candidates'])

/**
 * Renders one Work Plan field error under the caller's current locale. Trusts only
 * `code`+`params`, never the server's request-locale `msg` text — the same principle
 * apiErrors.ts's resolveApiError follows. `msg` is kept only as the fallback for a code
 * this client build does not yet have a translation key for (T0007 §1.2).
 */
export function renderWpFieldError(err: WpFieldError, t: Translate): string {
  if (!err.code || !KNOWN_CODES.has(err.code)) return err.msg
  const params = { ...err.params }
  const whatKey = params.what_key
  if (err.code === 'empty_selection' && typeof whatKey === 'string' && EMPTY_SELECTION_WHAT_KEYS.has(whatKey)) {
    params.what = t(`main.work_plan.empty_selection_what.${whatKey}`)
  }
  return t(`main.work_plan.errors.${err.code}`, params)
}
