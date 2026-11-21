//  — the two confirm questions that end a conflict in one press, shared by the conflict card
// and the merge-approval card: [병합 중단] and the administrator's [강제 정리].
//
// Merge abort: close the conflict review AND cancel the approval parked on it, then direct the user to the check card
// and its [승인] action (`abortAndCancelApproval`; the same two steps [새 커밋으로 다시 승인] takes). The dialog says what is undone
// and what stays before the person presses the red button, and its two buttons never read the same ([돌아가기] / [병합 중단]).
// "강제 정리": administrators only; reads what would be put back (target workspace, HEAD, the work lost) and shows it.
import { inject } from 'vue'
import { useI18n } from 'vue-i18n'
import { abortAndCancelApproval, cancelOperationOfGroup, cancelParkedApproval, frozenShaOf, readConflictOperationOf, useGitGateOperations, type GateOperation } from './gateOperations'
import { useGitPanel, type ForceCleanupTarget } from './gitSession'
import { GIT_WORKTREE_CONTEXT } from './gitWorktreeSession'

const short = (sha: string) => sha.slice(0, 8)
/** The folder name of a workspace path (either slash style). */
const baseName = (path: string) => path.split(/[\\/]/).filter(Boolean).pop() ?? path

/**
 * How a forced cleanup of ANOTHER group's merge session ended (the shown Flow has no card for it, so the list names it):
 * `cancelled` = the approval parked on it was cancelled; `none` = the group held none; `unchecked` = its run could not be
 * read, so a parked approval may remain; `failed` = the cancel was refused (`code`). `nodeKey` is that group's check card.
 */
export interface ForceCleanupOutcome {
  mergeId: number
  groupId: string
  kind: 'cancelled' | 'none' | 'unchecked' | 'failed'
  code: string | null
  nodeKey: string | null
}

export function useAbortMergeConfirm() {
  const { t } = useI18n()
  const ctx = useGitPanel()
  const session = ctx.session
  const gateJobs = useGitGateOperations()
  // The group HEAD comes from the worktree area when the panel has one (no worktree source = no HEAD in the text).
  const worktree = inject(GIT_WORKTREE_CONTEXT, null)?.worktree ?? null

  /** [병합 중단]: the confirm question, then abort + cancel of the approval parked on this conflict. */
  function askAbort(): void {
    const operation = gateJobs?.conflictOperation.value ?? null
    const bound = session.pendingRow.value?.finalApprovalBound === true
    const frozen = frozenShaOf(operation?.gate)
    const head = worktree?.state.status?.head ?? null
    const parts = [t('gui.git.conflict.abort_message')]
    if (bound) parts.push(t('gui.git.conflict.abort_bound'))
    if (operation) {
      parts.push(frozen ? t('gui.git.conflict.abort_cancel_approval', { sha: short(frozen) }) : t('gui.git.conflict.abort_cancel_approval_unknown'))
      parts.push(head ? t('gui.git.conflict.abort_then_approve', { head: short(head) }) : t('gui.git.conflict.abort_then_approve_unknown'))
    }
    ctx.confirm({
      title: t('gui.git.conflict.abort_title'),
      message: parts.join(' '),
      confirmLabel: t('gui.git.conflict.abort'),
      danger: true,
      run: () => void (operation ? abortAndCancelApproval(session, gateJobs, operation) : session.abortMerge()),
    })
  }

  /** [강제 정리]: reads the preview, asks with what would be put back, then cleans up and cancels the parked approval. */
  async function askForceCleanup(target?: ForceCleanupTarget, done?: (outcome: ForceCleanupOutcome) => void): Promise<void> {
    const preview = await session.forceCleanupPreview(target)
    if (!preview) return
    // The approval parked on the session's conflict, read before the session closes. On the shown group's conflict it is
    // the one in the Flow's run; on another group's (the "정리 실패" list) that group's run is read here — the dialog only
    // promises the cancel it will really send.
    const mine = !target || target.mergeId === session.pendingRow.value?.mergeId
    const operation: GateOperation | null | undefined = mine ? gateJobs?.conflictOperation.value ?? null : await readConflictOperationOf(target.groupId)
    const where = preview.targetBranch ?? '—'
    const parts = [
      preview.workspace
        ? t('gui.git.force.workspace', { workspace: baseName(preview.workspace), target: where, head: preview.head ? short(preview.head) : '—' })
        : t('gui.git.force.workspace_unknown', { target: where }),
      preview.discardedPaths.length ? t('gui.git.force.discarded', { n: preview.discardedPaths.length }) : t('gui.git.force.discarded_none'),
      preview.groupBranchUntouched ? t('gui.git.force.branch_kept') : '',
      operation === null ? t('gui.git.force.closes_only', { id: preview.mergeId })
        : operation === undefined ? t('gui.git.force.closes_unchecked', { id: preview.mergeId })
          : t('gui.git.force.closes', { id: preview.mergeId }),
    ].filter(Boolean)
    ctx.confirm({
      title: t('gui.git.force.title'),
      message: parts.join(' '),
      confirmLabel: t('gui.git.force.action'),
      danger: true,
      run: () => void (async () => {
        const result = await session.forceCleanup(target)
        if (!result) return
        if (mine) { await cancelParkedApproval(session, gateJobs, operation ?? null); return }
        const groupId = target!.groupId
        const base = { mergeId: target!.mergeId, groupId, code: null, nodeKey: operation?.nodeKey ?? null }
        if (operation === undefined) done?.({ ...base, kind: 'unchecked' })
        else if (operation === null) done?.({ ...base, kind: 'none' })
        else {
          const cancel = await cancelOperationOfGroup(groupId, operation)
          done?.({ ...base, kind: cancel.code ? 'failed' : 'cancelled', code: cancel.code, nodeKey: cancel.nodeKey })
        }
        await session.refresh()
      })(),
    })
  }

  return { askAbort, askForceCleanup }
}
