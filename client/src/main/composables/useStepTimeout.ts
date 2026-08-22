/**
 * The run-duration option list, shared by every dialog that lets a person choose one.
 *
 * flowgate.default.0400 M0005 introduced the list inside ContinuousWorkDialog.vue, where it
 * was the per-hop budget of an unmanned chain. 0446 NR0003 R5 (T0010 §3-5) gave the same
 * choice to AiInvokeDialog's single rejection rework, which until then was pinned to exactly
 * 3600 seconds by the server's min(3600 × max(1, docs_target), 14400) formula — 264 of 264
 * measured rework runs had timeout_sec=3600 and three of them died at that boundary.
 *
 * The list lives HERE and nowhere else on purpose. Two dialogs each holding their own copy is
 * how one of them silently loses 240 minutes, and how both start drifting away from the
 * server's own bounds (ai_invoke_service.STEP_TIMEOUT_MIN_SEC / STEP_TIMEOUT_MAX_SEC =
 * 1800..14400, enforced with a 422 by ai_invoke_routes). 30 min × 60 = 1800 and
 * 240 min × 60 = 14400 are exactly those two edges.
 *
 * There is deliberately NO unlimited (∞) entry: 0400 M0005 rejected it because the wall clock
 * is the only automatic guard an unmanned run has against never stopping at all.
 */
export const STEP_TIMEOUT_OPTIONS_MIN = [30, 45, 60, 90, 120, 180, 240] as const

/** What a dialog offers before anyone has chosen anything. */
export const STEP_TIMEOUT_DEFAULT_MIN = 120

/**
 * One key per surface, never shared (T0010 §3-5).
 *
 * "How long may one hop of an unmanned chain run?" and "how long may this one rework run?"
 * are different questions asked at different moments; letting one dialog's pick silently
 * become the other's default would answer a question the person never asked.
 */
export const CONTINUOUS_WORK_STEP_TIMEOUT_KEY = 'flowgate.continuousWork.stepTimeoutMinutes'
export const AI_INVOKE_STEP_TIMEOUT_KEY = 'flowgate.aiInvoke.stepTimeoutMinutes'

/** The last pick stored under `storageKey`, or the default when there is nothing usable there. */
export function loadStoredStepTimeoutMin(storageKey: string): number {
  try {
    const stored = Number(window.localStorage.getItem(storageKey))
    if ((STEP_TIMEOUT_OPTIONS_MIN as readonly number[]).includes(stored)) return stored
  } catch {
    // localStorage unavailable (private mode, SSR, ...) — fall back to the default below.
  }
  return STEP_TIMEOUT_DEFAULT_MIN
}

/** Best-effort persist. A storage failure must never block the dialog it was typed into. */
export function storeStepTimeoutMin(storageKey: string, minutes: number): void {
  try {
    window.localStorage.setItem(storageKey, String(minutes))
  } catch {
    // Remembering the pick is a convenience, not a precondition for starting a run.
  }
}
