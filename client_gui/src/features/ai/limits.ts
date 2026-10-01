// Input limits the AI screens check before saving (flowgate.v02.0017 T0004 §2/§5). They mirror
// the server so a value the server would reject is caught here first:
//   PROVIDERS_MAX          server settings/ai_settings_service.py PROVIDERS_MAX
//   EVALUATION_SET_LIMITS  server services/ai_evaluation_service.py SET_*_MAX

/** Owned AI per list (the system list, or one user's own list). */
export const PROVIDERS_MAX = 300

export const EVALUATION_SET_LIMITS = Object.freeze({
  nameMax: 200,
  instructionMax: 4000,
  constraintMax: 4000,
  constraintsMax: 100,
})
