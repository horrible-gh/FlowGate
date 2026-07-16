// flowgate.default.0241 (B0001): the L0004 §1 limits mirrored from
// server/modules/flow_gate/settings/ai_settings_service.py — keep both sides in step.
// The form checks these up front so over-limit input fails in the dialog instead of at
// save, and formatErrors() renders the P0003 422 `errors` array (index/field/reason) that
// the screens used to discard.

export const NAME_MAX = 100;
export const CLI_COMMAND_MAX = 4000;
export const API_BASE_URL_MAX = 500;
export const API_MODEL_MAX = 200;
export const API_KEY_MAX = 1000;
export const PROVIDERS_MAX = 20;

const FIELD_MAX = {
  name: NAME_MAX,
  cli_command: CLI_COMMAND_MAX,
  api_base_url: API_BASE_URL_MAX,
  api_model: API_MODEL_MAX,
  api_key: API_KEY_MAX,
};

function translate(i18n, key, params) {
  return i18n.te(key) ? i18n.t(key, params) : null;
}

/**
 * Turn one 422 error object into a human sentence.
 * An unmapped field/reason degrades to its raw wire value rather than an empty string —
 * a new server-side reason must still reach the user.
 */
function describe(err, providers, i18n) {
  const field = translate(i18n, `settings.ai.field.${err.field}`) || err.field;
  const reason =
    translate(i18n, `settings.ai.reason.${err.reason}`, {
      max: FIELD_MAX[err.field] ?? PROVIDERS_MAX,
    }) || err.reason;

  if (typeof err.index === 'number') {
    const name = providers?.[err.index]?.name || '';
    return name
      ? i18n.t('settings.ai.saveerr_row', { row: err.index + 1, name, field, reason })
      : i18n.t('settings.ai.saveerr_row_noname', { row: err.index + 1, field, reason });
  }
  return i18n.t('settings.ai.saveerr_field', { field, reason });
}

/** Format the whole 422 `errors` array; returns [] for anything that is not one. */
export function formatErrors(errors, providers, i18n) {
  if (!Array.isArray(errors)) return [];
  return errors.filter((e) => e && e.field).map((e) => describe(e, providers, i18n));
}
