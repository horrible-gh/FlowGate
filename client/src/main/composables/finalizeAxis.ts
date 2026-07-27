// 0331 T0006 / NR0005 §8 — scope × push ↔ finalize action mapping.
//
// The approved v4 mockup drives the Git 반영 UI from two independent axes
// (반영 범위: 머지 → 커밋 → 대기, and 원격에 푸시 on/off) rather than a flat list
// of action cards. The mapping itself is served by the API
// (`state.action_axes`), so this module is only the pure translation between
// the axis position the user sees and the action value the server takes. It
// lives outside the SFC so all three finalize surfaces share one implementation
// and so the mapping can be unit-tested without mounting a component.

export interface FinalizeAxisPair {
  push: string
  no_push: string
}

export interface FinalizeAxes {
  scopes: string[]
  matrix: Record<string, FinalizeAxisPair>
  /** Actions that produce a commit and therefore need a commit subject. */
  commit_actions?: string[]
}

export interface AxisPosition {
  scope: string
  push: boolean
}

/** Approved default when nothing else applies: 머지 + 원격에 푸시. */
export const DEFAULT_AXIS_POSITION: AxisPosition = { scope: 'merge', push: true }

/**
 * Reverse-map an action to its axis position. An action the matrix does not
 * describe (stale client, contract change) falls back to the first offered
 * scope with push on rather than leaving both axes visually unset.
 */
export function positionOfAction(
  axes: FinalizeAxes | null | undefined,
  action: string,
): AxisPosition {
  const matrix = axes?.matrix ?? {}
  for (const [scope, pair] of Object.entries(matrix)) {
    if (pair.push === action) return { scope, push: true }
    if (pair.no_push === action) return { scope, push: false }
  }
  return { scope: axes?.scopes?.[0] ?? DEFAULT_AXIS_POSITION.scope, push: DEFAULT_AXIS_POSITION.push }
}

/**
 * Forward map. Returns `fallback` when the scope is unknown so a click can
 * never blank out the current selection.
 */
export function actionOfPosition(
  axes: FinalizeAxes | null | undefined,
  scope: string,
  push: boolean,
  fallback: string,
): string {
  const pair = axes?.matrix?.[scope]
  if (!pair) return fallback
  return push ? pair.push : pair.no_push
}

/**
 * Whether the chosen action asks the operator for a commit subject. Driven by
 * the server list; `push` is deliberately NOT in it — since the 0331 contract
 * fix a bare push ships only existing commits and 409s on a dirty worktree, so
 * prompting for a message there would promise a commit that never happens.
 */
const FALLBACK_COMMIT_ACTIONS = ['merge', 'merge_only', 'commit_push', 'commit_only']

export function actionNeedsCommitMessage(
  axes: FinalizeAxes | null | undefined,
  action: string,
): boolean {
  const list = axes?.commit_actions?.length ? axes.commit_actions : FALLBACK_COMMIT_ACTIONS
  return list.includes(action)
}
