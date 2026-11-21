/**
 * "Recovery paths and button cleanup": the Git tab's conflict recovery path, end to end on a mounted
 * DesignerGitPanel with an in-memory source and a mocked run API.
 *   (1) [병합 중단] on the conflict card: abort, THEN cancel the approval parked on it, then the guidance
 *   (2) a closed merge session: [재시도] / [다시 반영] are off with their reason, [이 승인 취소] leads (card and window)
 *   (3) the "해결 N · 남음 M" numbers come from the server's record, not from files that merely show no marker
 *   (4) [강제 정리] and the "정리 실패" list are for administrators only; git's reason is shown
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
const mocks = vi.hoisted(() => ({ read: vi.fn(), recover: vi.fn() }))
vi.mock('@/features/document', async original => ({ ...await original<typeof import('@/features/document')>(), runCurrentRead: (...a: unknown[]) => mocks.read(...a), runGateRecover: (...a: unknown[]) => mocks.recover(...a) }))
import { DesignerGitPanel } from '@/features/git'
import { G, P, conflicts, createFakeGitSource, row, status } from './fakeGitSource'
import { prepareInspectorI18n } from '../designer/inspector/support/inspectorSupport'

const i18n = prepareInspectorI18n()
let wrapper: VueWrapper | null = null
let current: any
const order: string[] = []
const FLOW = { groupId: G, name: '인증 오류 조사' }

/** A held decision as the run read sends it (`gate.recovery`, `job`, the receipt with its pinned commit). */
const gateNode = (recovery: Record<string, unknown> = {}, gate: Record<string, unknown> = {}) => ({
  nodeKey: 'g1', status: 'open', attemptId: 'a1',
  gate: {
    decision_in_progress: true, can_decide: false, settings_locked: true, archived: false, recovery_reset_required: false,
    git_active: false, parked: true,
    decision: { decision_id: 'd1', status: 'failed', frozen_sha: '97473b26aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', effects: [{ phase: 'merge', status: 'failed' }] },
    job: { job_id: 'j1', status: 'recovery_required', phase: 'merge', phase_note: 'gate_conflict', last_error_code: 'merge_conflict' },
    recovery: { decision_id: 'd1', decision_version: 7, status: 'conflict', retryable: false, cancelable: true, resolvable: false,
      evidence_version: 4, evidence_refs: [], phase: 'merge', error_code: 'merge_conflict', ...recovery },
    ...gate,
  },
})
/** The merge session is closed: the decision's error is `merge_conflict_aborted` and the server still calls it retryable. */
const closedNode = () => gateNode(
  { status: 'failed', retryable: true, error_code: 'merge_conflict_aborted' },
  { job: { job_id: 'j1', status: 'recovery_required', phase: 'merge', phase_note: 'gate_failed', last_error_code: 'merge_conflict_aborted' } })

const q = (sel: string) => document.body.querySelector<HTMLElement>(`[data-test="${sel}"]`)
const qa = (sel: string) => [...document.body.querySelectorAll<HTMLElement>(sel)]
const text = (sel: string) => q(sel)?.textContent?.replace(/\s+/g, ' ').trim() ?? ''
const settle = async () => { for (let i = 0; i < 6; i++) await flushPromises() }
async function click(sel: string) {
  const el = q(sel)
  if (!el) throw new Error(`missing ${sel}`)
  el.click()
  await settle()
}
const dialogButtons = () => qa('.ui-confirm-actions button').map(button => button.textContent!.trim())
async function confirmDialog() {
  const button = qa('.ui-confirm-actions button').at(-1)
  if (!button) throw new Error('no confirm dialog')
  button.click()
  await settle()
}
async function mountPanel(fake: ReturnType<typeof createFakeGitSource>, props: Record<string, unknown> = {}) {
  wrapper = mount(DesignerGitPanel, {
    props: { projectId: P, flow: FLOW, source: fake.source, ...props },
    attachTo: document.body,
    global: { plugins: [i18n] },
  })
  await settle()
  return wrapper
}

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  order.length = 0
  mocks.read.mockReset(); mocks.recover.mockReset()
  current = { runId: 'r1', status: 'idle', nodes: [gateNode()] }
  mocks.read.mockImplementation(async () => current)
  mocks.recover.mockImplementation(async (_run: string, _node: string, _decision: string, action: string) => {
    order.push(`gate:${action}`)
    return current = { ...current, nodes: [] }
  })
})
afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
})

describe('(1) [병합 중단] closes the conflict review and cancels the approval parked on it', () => {
  it('asks with what is undone and what stays; its two buttons are [돌아가기] and [병합 중단]', async () => {
    const fake = createFakeGitSource({ status: status({ pending: [row()] }) })
    await mountPanel(fake)
    expect(text('git-conflict-abort')).toBe('병합 중단')
    await click('git-conflict-abort')
    expect(dialogButtons()).toEqual(['돌아가기', '병합 중단'])
    const message = document.body.querySelector('.ui-confirm-message')!.textContent!
    expect(message).toContain('작업공간을 병합 전으로 되돌립니다. 그룹 브랜치는 그대로 남습니다.')
    expect(message).toContain('이 승인(97473b26 기준)도 취소합니다. 기록은 남습니다.')
    expect(message).toContain('그다음 확인 카드에서 [승인]을 누르면')
    expect(fake.source.abortMerge).not.toHaveBeenCalled()
    expect(mocks.recover).not.toHaveBeenCalled()
  })

  it('aborts first, then cancels the old approval (same cancel as [새 커밋으로 다시 승인]), then points at the check card', async () => {
    const fake = createFakeGitSource({ status: status({ pending: [row()] }) })
    fake.source.abortMerge.mockImplementation(async () => { order.push('abort') })
    await mountPanel(fake)
    await click('git-conflict-abort')
    await confirmDialog()
    expect(fake.source.abortMerge).toHaveBeenCalledWith(G, 7)
    expect(mocks.recover).toHaveBeenCalledWith('r1', 'g1', 'd1', 'cancel', { request_id: expect.any(String), expected_decision_version: 7 })
    expect(order).toEqual(['abort', 'gate:cancel'])
    expect(q('git-reapprove-outcome')).not.toBeNull()
    expect(q('git-reapprove-outcome')!.getAttribute('data-code')).toBe('')
    expect(text('git-reapprove-outcome')).toContain('확인 카드에서 [승인]')
    expect(q('git-reapprove-goto')).not.toBeNull()
  })

  it('a refused abort cancels nothing and shows git\'s reason, not git_error alone', async () => {
    const fake = createFakeGitSource({ status: status({ pending: [row()] }) })
    const reason = "error: Entry 'src/f1.ts' not uptodate. Cannot merge."
    fake.source.abortMerge.mockRejectedValue(Object.assign(new Error('git_error'), {
      isAxiosError: true,
      response: { status: 500, data: { ok: false, error: { code: 'git_error', message: 'Merge abort failed', params: { reason }, details: { reason, returncode: 128, merge_id: 7, recovery: 'force_cleanup' } } } },
    }))
    await mountPanel(fake)
    await click('git-conflict-abort')
    await confirmDialog()
    expect(mocks.recover).not.toHaveBeenCalled()
    const notice = text('git-notice')
    expect(notice).toContain(reason)
    expect(notice).toContain('관리자에게 [강제 정리]를 요청하세요')
    expect(notice).not.toContain('git_error')
    // the conflicts are read again, so the session's own record (abort_failure) shows next
    expect(fake.source.fetchConflicts.mock.calls.length).toBeGreaterThan(1)
  })

  it('abort done but the old approval cannot be cancelled: says so and names the button to press next', async () => {
    // the server does not allow cancelling the held decision (cancelable false): nothing is sent, the person is told what is left
    current = { runId: 'r1', status: 'idle', nodes: [gateNode({ cancelable: false })] }
    const fake = createFakeGitSource({ status: status({ pending: [row()] }) })
    fake.source.abortMerge.mockImplementation(async () => { order.push('abort') })
    await mountPanel(fake)
    await click('git-conflict-abort')
    await confirmDialog()
    expect(order).toEqual(['abort'])
    expect(mocks.recover).not.toHaveBeenCalled()
    const outcome = q('git-reapprove-outcome')!
    expect(outcome.getAttribute('data-code')).not.toBe('')
    expect(text('git-reapprove-outcome')).toContain('충돌 검토는 닫았지만 이전 승인을 취소하지 못함')
    expect(text('git-reapprove-outcome')).toContain('[이 승인 취소]')
    expect(q('git-reapprove-goto')).toBeNull()
  })

  it('without an approval parked on the conflict it only aborts', async () => {
    current = { runId: 'r1', status: 'idle', nodes: [] }
    const fake = createFakeGitSource({ status: status({ pending: [row()] }) })
    await mountPanel(fake)
    await click('git-conflict-abort')
    await confirmDialog()
    expect(fake.source.abortMerge).toHaveBeenCalledWith(G, 7)
    expect(mocks.recover).not.toHaveBeenCalled()
  })

  it('the merge-approval card\'s button is the same [병합 중단] with the same dialog', async () => {
    const fake = createFakeGitSource({ status: status({ pending: [row({ reviewState: 'resolved_pending_review' })] }) })
    await mountPanel(fake)
    expect(text('git-review-abort')).toBe('병합 중단')
    await click('git-review-abort')
    expect(dialogButtons()).toEqual(['돌아가기', '병합 중단'])
    await confirmDialog()
    expect(fake.source.abortMerge).toHaveBeenCalledWith(G, 7)
    expect(order).toEqual(['gate:cancel'])
  })
})

describe('(2) an approval whose merge session is closed cannot be retried', () => {
  beforeEach(() => { current = { runId: 'r1', status: 'idle', nodes: [closedNode()] } })

  it('the card turns [다시 반영] off with the reason and leads with [이 승인 취소] → check card', async () => {
    await mountPanel(createFakeGitSource())
    const retry = q('git-push-retry') as HTMLButtonElement
    expect(retry.disabled).toBe(true)
    expect(text('git-closed-note')).toContain('병합 세션이 이미 닫혔습니다')
    expect(text('git-retry-blocked')).toContain('닫힌 병합 세션을 다시 읽기만 해서 항상 같은 결과')
    expect(text('git-closed-next')).toContain('확인 카드에서 [승인]')
    const cancel = q('git-push-cancel') as HTMLButtonElement
    expect(cancel.disabled).toBe(false)
    expect(cancel.className).toContain('primary')
    expect(q('git-goto-gate')).not.toBeNull()
    // the server's own flag says retryable, the screen does not trust it for a closed session
    expect(current.nodes[0].gate.recovery.retryable).toBe(true)
    retry.click(); await settle()
    expect(mocks.recover).not.toHaveBeenCalled()
  })

  it('[이 승인 취소] cancels the decision and leaves the way to the check card', async () => {
    await mountPanel(createFakeGitSource())
    await click('git-push-cancel')
    expect(mocks.recover).toHaveBeenCalledWith('r1', 'g1', 'd1', 'cancel', { request_id: expect.any(String), expected_decision_version: 7 })
    expect(text('git-reapprove-outcome')).toContain('확인 카드에서 [승인]')
    expect(q('git-reapprove-goto')).not.toBeNull()
  })

  it('the detail window has the same set: [이 승인 취소] first, [재시도] off with the reason', async () => {
    await mountPanel(createFakeGitSource())
    await click('git-push-detail')
    const buttons = qa('[data-test="git-push-window"] .gx-actions button')
    expect(buttons.map(button => button.textContent!.trim())).toEqual(['이 승인 취소', '재시도'])
    expect((q('git-job-retry') as HTMLButtonElement).disabled).toBe(true)
    expect((q('git-job-cancel') as HTMLButtonElement).disabled).toBe(false)
    expect(text('git-retry-blocked')).toContain('닫힌 병합 세션')
  })

  it('a decision whose session is still open keeps [다시 반영] as the main button', async () => {
    current = { runId: 'r1', status: 'idle', nodes: [gateNode({ status: 'failed', retryable: true, error_code: 'push_remote_race' }, { job: { job_id: 'j1', status: 'recovery_required', phase: 'push', phase_note: 'gate_failed', last_error_code: 'push_remote_race' } })] }
    await mountPanel(createFakeGitSource())
    expect((q('git-push-retry') as HTMLButtonElement).disabled).toBe(false)
    expect(q('git-push-cancel')).toBeNull()
    expect(q('git-closed-note')).toBeNull()
  })
})

describe('(3) 해결 N · 남음 M is the server\'s record', () => {
  it('counts resolved_paths, not files without markers (DB 9 resolved · 4 open showed as "해결됨 12")', async () => {
    const session = conflicts(13, 9)
    // the 3 files whose markers are gone but the server never recorded a resolution (the 457 case)
    session.files.forEach((file, index) => { if (index >= 9 && index < 12) { file.conflictCount = 0; file.content = `edited ${index}\n` } })
    const fake = createFakeGitSource({ status: status({ pending: [row()] }), conflicts: session })
    await mountPanel(fake)
    expect(text('git-conflict-count')).toBe('해결 9 · 남음 4 (서버 기록 기준)')
    // open files are listed first: the three shown are all unresolved
    const shown = qa('[data-test="git-conflict-card"] .gx-file').map(el => el.textContent!)
    expect(shown.some(line => /f[0-8]\.ts/.test(line))).toBe(false)
  })
})

describe('(4) forced cleanup and the "정리 실패" list are for administrators', () => {
  const reason = "error: Entry 'src/f1.ts' not uptodate. Cannot merge."
  const failure = { trigger: 'user_abort', reason, returncode: 128, at: '2026-11-19T10:09:58+09:00' }
  const withFailure = () => ({ ...conflicts(), abortFailure: failure })
  const preview = {
    mergeId: 7, workspace: 'D:\\ws\\feature_v0.2.0-57fd', targetBranch: 'feature/v0.2.0', head: '81d1e0a916d65e39736164399eff1f2ae1c47185',
    baselineHead: '81d1e0a916d65e39736164399eff1f2ae1c47185', headMatchesBaseline: true, discardedPaths: ['a.ts', 'b.ts'], abortFailure: failure, groupBranchUntouched: true,
  }
  const forceSource = (admin: boolean, extra: Record<string, unknown> = {}) => {
    const fake = createFakeGitSource({ status: status({ pending: [row()] }), conflicts: withFailure() }, {
      fetchForceCleanupPreview: vi.fn(async () => preview),
      forceCleanup: vi.fn(async () => ({ status: 'aborted', forced: true, workspace: preview.workspace, targetBranch: preview.targetBranch, resetHead: preview.head, discardedPaths: preview.discardedPaths, branchPreserved: true })),
      fetchAbortFailures: vi.fn(async () => admin ? [{ mergeId: 451, groupId: 'p.m.0031', kind: 'merge', targetBranch: 'feature/v0.2.0', abortFailure: failure, touchedAt: null }] : []),
      ...extra,
    })
    return fake
  }

  it('shows git\'s reason and the next step on the card for everyone, [강제 정리] only to an administrator', async () => {
    const fake = forceSource(false)
    await mountPanel(fake, { isAdmin: false })
    expect(text('git-conflict-abort-reason')).toBe(reason)
    expect(text('git-conflict-abort-next')).toBe('다시 눌러도 같습니다. 관리자에게 [강제 정리]를 요청하세요.')
    expect(q('git-conflict-force')).toBeNull()
    // not an administrator: not even the list request is made
    expect(fake.source.fetchAbortFailures).not.toHaveBeenCalled()
    expect(q('git-abort-failures')).toBeNull()
  })

  it('an administrator gets [강제 정리]; the dialog says what is put back before anything is sent', async () => {
    const fake = forceSource(true)
    await mountPanel(fake, { isAdmin: true })
    expect(text('git-conflict-abort-next')).toContain('[강제 정리]로 작업공간을 되돌릴 수 있습니다')
    await click('git-conflict-force')
    expect(fake.source.fetchForceCleanupPreview).toHaveBeenCalledWith(G, 7)
    expect(fake.source.forceCleanup).not.toHaveBeenCalled()
    expect(dialogButtons()).toEqual(['돌아가기', '강제 정리'])
    const message = document.body.querySelector('.ui-confirm-message')!.textContent!
    expect(message).toContain('feature_v0.2.0-57fd')
    expect(message).toContain('feature/v0.2.0 HEAD(81d1e0a9)')
    expect(message).toContain('해결 중이던 내용 2개는 사라집니다')
    expect(message).toContain('그룹 브랜치와 그 커밋, 원래 승인 기록은 그대로입니다')
    await confirmDialog()
    expect(fake.source.forceCleanup).toHaveBeenCalledWith(G, 7)
    // the approval parked on the closed session is cancelled like after an abort
    expect(mocks.recover).toHaveBeenCalledWith('r1', 'g1', 'd1', 'cancel', expect.anything())
  })

  it('"돌아가기" sends nothing', async () => {
    const fake = forceSource(true)
    await mountPanel(fake, { isAdmin: true })
    await click('git-conflict-force')
    qa('.ui-confirm-actions button')[0].click(); await settle()
    expect(fake.source.forceCleanup).not.toHaveBeenCalled()
  })

  it('no recorded failure, no [강제 정리] — even for an administrator', async () => {
    const fake = createFakeGitSource({ status: status({ pending: [row()] }) }, { fetchAbortFailures: vi.fn(async () => []) })
    await mountPanel(fake, { isAdmin: true })
    expect(q('git-conflict-force')).toBeNull()
    expect(q('git-abort-failures')).toBeNull()
  })

  it('lists the sessions marked "정리 실패" for an administrator, with the reason and [강제 정리]', async () => {
    const fake = forceSource(true)
    await mountPanel(fake, { isAdmin: true })
    expect(fake.source.fetchAbortFailures).toHaveBeenCalledWith(P)
    expect(qa('[data-test="git-abort-failure-item"]')).toHaveLength(1)
    expect(text('git-abort-failure-item')).toContain('451 · p.m.0031 → feature/v0.2.0')
    expect(text('git-abort-failure-reason')).toBe(reason)
    await click('git-abort-failure-force')
    expect(fake.source.fetchForceCleanupPreview).toHaveBeenCalledWith('p.m.0031', 451)
    await confirmDialog()
    expect(fake.source.forceCleanup).toHaveBeenCalledWith('p.m.0031', 451)
  })

  describe('a session of ANOTHER group in the list (the shown Flow has no card for it)', () => {
    const OTHER = 'p.m.0031'
    let other: any
    const dialogMessage = () => document.body.querySelector('.ui-confirm-message')!.textContent!
    /** The list item's [강제 정리] on a panel whose own conflict is a different one (merge 7 of the shown group). */
    async function openOtherDialog(extra: Record<string, unknown> = {}) {
      const fake = forceSource(true, extra)
      await mountPanel(fake, { isAdmin: true })
      await click('git-abort-failure-force')
      expect(fake.source.fetchForceCleanupPreview).toHaveBeenCalledWith(OTHER, 451)
      return fake
    }
    beforeEach(() => {
      other = { runId: 'r9', status: 'idle', nodes: [gateNode()] }
      mocks.read.mockImplementation(async (group: string) => group === OTHER ? other : current)
      mocks.recover.mockImplementation(async (run: string, _node: string, _decision: string, action: string) => {
        order.push(`gate:${action}:${run}`)
        if (run === 'r9') return other = { ...other, nodes: [] }
        return current = { ...current, nodes: [] }
      })
    })

    it('reads that group\'s run, cancels the approval parked there, and leads to ITS check card', async () => {
      const fake = await openOtherDialog()
      // the dialog promises the cancel only because that group\'s approval was found
      expect(dialogMessage()).toContain('이 승인도 취소합니다')
      expect(mocks.read).toHaveBeenCalledWith(OTHER)
      expect(mocks.recover).not.toHaveBeenCalled()
      await confirmDialog()
      expect(fake.source.forceCleanup).toHaveBeenCalledWith(OTHER, 451)
      // the OTHER group's decision is cancelled in the OTHER group's run (r9), not in the shown Flow's (r1)
      expect(mocks.recover).toHaveBeenCalledTimes(1)
      expect(mocks.recover).toHaveBeenCalledWith('r9', 'g1', 'd1', 'cancel', expect.objectContaining({ expected_decision_version: 7 }))
      expect(order).toEqual(['gate:cancel:r9'])
      expect(text('git-abort-failure-done-text')).toContain('이전 승인을 취소했습니다')
      expect(text('git-abort-failure-done-text')).toContain('확인 카드에서 [승인]')
      const seen: unknown[] = []
      const listener = (event: Event) => seen.push((event as CustomEvent).detail)
      window.addEventListener('fg:select_gate_node', listener)
      await click('git-abort-failure-open-card')
      window.removeEventListener('fg:select_gate_node', listener)
      expect(seen).toEqual([{ flowId: OTHER, nodeKey: 'g1' }])
    })

    it('a group with no approval parked on it: the dialog and the result say so, nothing is cancelled', async () => {
      other = { runId: 'r9', status: 'idle', nodes: [] }
      const fake = await openOtherDialog()
      expect(dialogMessage()).not.toContain('이 승인도 취소합니다')
      expect(dialogMessage()).toContain('취소할 승인은 없습니다')
      await confirmDialog()
      expect(fake.source.forceCleanup).toHaveBeenCalledWith(OTHER, 451)
      expect(mocks.recover).not.toHaveBeenCalled()
      expect(text('git-abort-failure-done-text')).toContain('취소할 승인은 없었습니다')
      expect(q('git-abort-failure-open-card')).toBeNull()
    })

    it('a run that cannot be read: no promise of a cancel, and the result names the button that still does it', async () => {
      mocks.read.mockImplementation(async (group: string) => { if (group === OTHER) throw new Error('forbidden'); return current })
      const fake = await openOtherDialog()
      expect(dialogMessage()).not.toContain('이 승인도 취소합니다')
      expect(dialogMessage()).toContain('확인하지 못했습니다')
      await confirmDialog()
      expect(fake.source.forceCleanup).toHaveBeenCalledWith(OTHER, 451)
      expect(mocks.recover).not.toHaveBeenCalled()
      expect(text('git-abort-failure-done-text')).toContain('[이 승인 취소]')
    })

    it('a refused cancel after the cleanup: says the session is closed but the approval stays, and where to cancel it', async () => {
      const fake = await openOtherDialog()
      // after the cleanup the server no longer lets that decision be cancelled from here
      mocks.read.mockImplementation(async (group: string) => group === OTHER
        ? { runId: 'r9', status: 'idle', nodes: [gateNode({ cancelable: false, error_code: 'gate_decision_not_cancelable' })] }
        : current)
      await confirmDialog()
      expect(fake.source.forceCleanup).toHaveBeenCalledWith(OTHER, 451)
      expect(mocks.recover).not.toHaveBeenCalled()
      const done = text('git-abort-failure-done-text')
      expect(done).toContain('이전 승인을 취소하지 못했습니다(gate_decision_not_cancelable)')
      expect(done).toContain('[이 승인 취소]')
      expect(q('git-abort-failure-open-card')).not.toBeNull()
    })

    it('the shown group\'s own conflict with no approval parked does not promise a cancel either', async () => {
      current = { runId: 'r1', status: 'idle', nodes: [] }
      const fake = forceSource(true)
      await mountPanel(fake, { isAdmin: true })
      await click('git-conflict-force')
      expect(dialogMessage()).not.toContain('이 승인도 취소합니다')
      expect(dialogMessage()).toContain('취소할 승인은 없습니다')
      await confirmDialog()
      expect(fake.source.forceCleanup).toHaveBeenCalledWith(G, 7)
      expect(mocks.recover).not.toHaveBeenCalled()
    })
  })
})
