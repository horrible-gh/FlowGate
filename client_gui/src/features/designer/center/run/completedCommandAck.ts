// flowgate.v02.0156 T#4 (D7): a 다음 단계 / 전체 실행 command on a completed run that has no new definition to adopt is only
// acknowledged by the server (0085 T#4: no reconcile, no dispatch). The run stays idle `completed`; the person is told why
// nothing happened, once per command (the Shell tells it in the command's own answer, never from a run observation, so a
// closed notice does not come back by itself).

/** The run as a command found it / left it: enough to tell an acknowledged-only command. */
export interface AckRun {
  readonly runId: string
  readonly status: string
  readonly idleReason?: string | null
  /** The definition revision the run is on; a change means the command adopted a new definition (not acknowledge-only). */
  readonly revisionNo?: number | null
}

/**
 * The command was taken, the run was idle `completed` before it and is still the same idle `completed` run after it, on the
 * same definition revision. A revision that moved (the command adopted a confirmed new definition whose cards are all done)
 * is not acknowledge-only, and a revision that cannot be compared (only one side knows it) is not told either: a wrong
 * "nothing happened" notice is worse than none.
 */
export function isCompletedNoop(before: AckRun | null, after: AckRun | null, taken: boolean): boolean {
  if (!taken || !before || !after) return false
  const done = (run: AckRun) => run.status === 'idle' && run.idleReason === 'completed'
  const sameRevision = (before.revisionNo ?? null) === (after.revisionNo ?? null)
  return before.runId === after.runId && done(before) && done(after) && sameRevision
}
