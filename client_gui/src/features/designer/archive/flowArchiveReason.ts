// v02test.0083 F83-5: the one place that says why a Flow's Archive is off. The toolbar's Archive button and the
// project tree's right-click Archive item both show this text (gui.designer.archive.* in ko / en / ja).
// '' = nothing blocks it here (the caller keeps its own label / no tooltip).

export interface FlowArchiveReasonState { readonly eligible: boolean; readonly reason?: string | null }

/**
 * `state`: the server's archive answer for the Flow (null = not read yet / read failed, which fails closed).
 * `runActive`: the Flow is actually running or stopping on the client (v02test.0083: that alone keeps Archive off).
 */
export function flowArchiveReason(
  state: FlowArchiveReasonState | null | undefined,
  runActive: boolean,
  t: (key: string) => string,
  te: (key: string) => boolean,
): string {
  if (state?.eligible) return runActive ? t('gui.designer.archive.work_unfinished') : ''
  const key = `gui.designer.archive.${state?.reason ?? 'unavailable'}`
  return t(te(key) ? key : 'gui.designer.archive.unavailable')
}
