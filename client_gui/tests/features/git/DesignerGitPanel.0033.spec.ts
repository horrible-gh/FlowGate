import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { DesignerGitPanel } from '@/features/git'
import { G, P, conflicts, createFakeGitSource, gitError, resolvingRun, review, row, status, trConflicts } from './fakeGitSource'

// flowgate.v02.0033 — DesignerGitPanel mounted with an in-memory source: cards per state,
// the public events / expose (0033-D §5.1) and the two modeless windows (§6.2, §6.3).
registerAppMessages(i18n)

let wrapper: VueWrapper | null = null

beforeEach(() => {
  i18n.global.locale.value = 'ko'
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
})

const FLOW = { groupId: G, name: '인증 오류 조사' }

async function mountPanel(fake: ReturnType<typeof createFakeGitSource>, props: Record<string, unknown> = {}) {
  wrapper = mount(DesignerGitPanel, {
    props: { projectId: P, flow: FLOW, source: fake.source, ...props },
    attachTo: document.body,
    global: { plugins: [i18n] },
  })
  await flushPromises()
  return wrapper
}

const q = (sel: string) => document.body.querySelector<HTMLElement>(`[data-test="${sel}"]`)
const qa = (sel: string) => [...document.body.querySelectorAll<HTMLElement>(sel)]
const text = (sel: string) => q(sel)?.textContent?.replace(/\s+/g, ' ').trim() ?? ''

async function click(sel: string) {
  const el = q(sel)
  if (!el) throw new Error(`missing ${sel}`)
  el.click()
  await flushPromises()
}

async function confirmDialog() {
  const button = qa('.ui-confirm-actions button').at(-1)
  if (!button) throw new Error('no confirm dialog')
  button.click()
  await flushPromises()
}

describe('DesignerGitPanel — header and nothing to do', () => {
  it('shows work branch / Base and no card; badge stays null', async () => {
    const w = await mountPanel(createFakeGitSource())
    expect(text('git-work-branch')).toBe('flowgate_v02_0008')
    expect(text('git-base-branch')).toBe('Base branch · v0.2')
    expect(q('git-exceptions')).toBeNull()
    expect(w.emitted('badge-change')).toBeUndefined()
  })

  it('an unconfigured Base reads 미설정; Git off reads Git 미사용', async () => {
    await mountPanel(createFakeGitSource({ status: status({ baseBranch: null, enabled: false }) }))
    expect(text('git-base-branch')).toBe('Base branch · 미설정')
    expect(text('git-header-line')).toBe('Git 미사용')
  })

  it('refresh reads the status again', async () => {
    const fake = createFakeGitSource()
    await mountPanel(fake)
    const reads = fake.source.loadStatus.mock.calls.length
    await click('git-refresh')
    expect(fake.source.loadStatus.mock.calls.length).toBe(reads + 1)
  })

  it('reads again when the tab comes into view, not while hidden', async () => {
    const fake = createFakeGitSource()
    const w = await mountPanel(fake, { active: false })
    const reads = fake.source.loadStatus.mock.calls.length
    await w.setProps({ active: true })
    await flushPromises()
    expect(fake.source.loadStatus.mock.calls.length).toBe(reads + 1)
  })
})

describe('DesignerGitPanel — cards', () => {
  it('conflict card: count, three files, more, buttons; badge !', async () => {
    const w = await mountPanel(createFakeGitSource({ status: status({ pending: [row()] }) }))
    expect(text('git-conflict-card')).toContain('병합 충돌 · 5개 파일')
    expect(qa('[data-test="git-conflict-card"] .gx-file')).toHaveLength(3)
    expect(text('git-conflict-more')).toBe('외 2개 보기')
    expect(w.emitted('badge-change')?.at(-1)).toEqual([{ tone: 'danger', text: '!' }])
  })

  it('a conflict file raises open-diff against the Base checkout', async () => {
    const w = await mountPanel(createFakeGitSource({ status: status({ pending: [row()] }) }))
    qa('[data-test="git-conflict-card"] .gx-file')[0].click()
    expect(w.emitted('open-diff')?.[0]).toEqual([
      { projectId: P, groupId: G, mergeId: null, path: 'src/f0.ts', status: 'C', origin: 'conflict', diffSource: 'base' },
    ])
  })

  it('abort asks first and says the final approval ends with it when bound', async () => {
    const fake = createFakeGitSource({ status: status({ pending: [row({ finalApprovalBound: true })] }) })
    await mountPanel(fake)
    await click('git-conflict-abort')
    expect(document.body.textContent).toContain('진행 중인 최종 승인 시도가 함께 종료됩니다.')
    expect(fake.source.abortMerge).not.toHaveBeenCalled()
    await confirmDialog()
    expect(fake.source.abortMerge).toHaveBeenCalledWith(G, 7)
  })

  it('a TR revert conflict shows the title and one line, no buttons', async () => {
    await mountPanel(createFakeGitSource({ status: status({ pending: [row({ reviewState: 'open' })] }) }))
    expect(q('git-conflict-unsupported')).not.toBeNull()
    expect(qa('[data-test="git-conflict-card"] button')).toHaveLength(0)
  })

  it('a TR revert conflict with review_state null (the real status answer) has no buttons either', async () => {
    const w = await mountPanel(createFakeGitSource({ status: status({ pending: [row({ reviewState: null })] }), conflicts: trConflicts() }))
    expect(q('git-conflict-unsupported')).not.toBeNull()
    expect(qa('[data-test="git-conflict-card"] button')).toHaveLength(0)
    expect(w.emitted('badge-change')?.at(-1)).toEqual([{ tone: 'danger', text: '!' }])
  })

  it('AI card: progress counted from the markers, watch / stop', async () => {
    const fake = createFakeGitSource({ status: status({ pending: [row()] }), ai: resolvingRun, conflicts: conflicts(100, 1) })
    const w = await mountPanel(fake)
    expect(text('git-ai-progress')).toBe('1 / 100')
    expect(w.emitted('badge-change')?.at(-1)).toEqual([{ tone: 'progress', text: '…' }])
    await click('git-ai-stop')
    expect(fake.source.cancelAiRun).toHaveBeenCalledWith('run-1')
  })

  it('review card: files from the frozen candidate open with diffSource review', async () => {
    const fake = createFakeGitSource({ status: status({ pending: [row({ reviewState: 'resolved_pending_review' })] }) })
    const w = await mountPanel(fake)
    expect(text('git-review-card')).toContain('병합 승인 대기 · 2개 파일')
    expect(text('git-review-resolver')).toBe('해결: Claude Opus 5')
    qa('[data-test="git-review-card"] .gx-file')[1].click()
    expect(w.emitted('open-diff')?.[0]).toEqual([
      { projectId: P, groupId: G, mergeId: 7, path: 'src/b.ts', status: 'A', origin: 'review', diffSource: 'review' },
    ])
  })

  it('review approve confirms with target and file count, then approves', async () => {
    const fake = createFakeGitSource({ status: status({ pending: [row({ reviewState: 'resolved_pending_review' })] }) })
    await mountPanel(fake)
    await click('git-review-approve')
    expect(document.body.textContent).toContain('main에 2개 파일을 반영합니다.')
    await confirmDialog()
    expect(fake.source.approveReview).toHaveBeenCalledTimes(1)
  })

  it('review buttons follow can_approve / can_reject; reconciling turns all off with its line', async () => {
    const fake = createFakeGitSource({
      status: status({ pending: [row({ reviewState: 'reconciling' })] }),
      review: review({ reviewState: 'reconciling', reconciliationKind: 'push_remote_unknown', canApprove: false, canReject: false }),
    })
    await mountPanel(fake)
    expect((q('git-review-approve') as HTMLButtonElement).disabled).toBe(true)
    expect((q('git-review-reject') as HTMLButtonElement).disabled).toBe(true)
    expect((q('git-review-abort') as HTMLButtonElement).disabled).toBe(true)
    expect(text('git-review-state')).toBe('결과 확인 중 — 원격 반영 결과를 확인하지 못했습니다.')
  })

  it('reject without a default AI is disabled with its reason', async () => {
    const fake = createFakeGitSource({
      status: status({ pending: [row({ reviewState: 'resolved_pending_review' })] }),
      providers: { providerIds: ['opus'], defaultProviderId: null },
    })
    await mountPanel(fake)
    expect((q('git-review-reject') as HTMLButtonElement).disabled).toBe(true)
    expect(text('git-review-no-provider')).toBe('기본 AI가 설정되지 않음')
  })

  it('reject opens a reason box and submits it', async () => {
    const fake = createFakeGitSource({ status: status({ pending: [row({ reviewState: 'resolved_pending_review' })] }) })
    await mountPanel(fake)
    await click('git-review-reject')
    const box = q('git-reject-reason') as HTMLTextAreaElement
    box.value = '테스트 삭제 금지'
    box.dispatchEvent(new Event('input'))
    await flushPromises()
    await click('git-reject-submit')
    expect(fake.source.rejectReview).toHaveBeenCalledWith(G, 7, '테스트 삭제 금지', 'opus')
  })

  it('hold card stage 1: 반영 방식 미확인, apply off, settings raises the intent', async () => {
    const fake = createFakeGitSource({ status: status({ pending: [row({ status: 'waiting', mergeId: null })] }) })
    const w = await mountPanel(fake)
    expect(text('git-hold-plan')).toBe('v0.2 · 반영 방식 미확인')
    expect((q('git-hold-apply') as HTMLButtonElement).disabled).toBe(true)
    expect(text('git-hold-reason')).toBe('확인 단계의 반영 방식이 없음 — 설정에서 확인')
    await click('git-hold-settings')
    expect(w.emitted('open-confirm-settings')?.[0]).toEqual([{ projectId: P, groupId: G, acDocId: 'p.m.0001.0009-AC' }])
    expect(w.emitted('badge-change')?.at(-1)).toEqual([{ tone: 'count', text: '1' }])
  })

  it('hold card stage 2: the plan shows and apply confirms then sends it', async () => {
    const fake = createFakeGitSource({
      status: status({ pending: [row({ status: 'waiting', mergeId: null, plan: { action: 'merge', targetBranch: 'main' } })] }),
    })
    await mountPanel(fake)
    expect(text('git-hold-plan')).toBe('main · 병합 + Push')
    await click('git-hold-apply')
    await confirmDialog()
    expect(fake.source.finalize).toHaveBeenCalledWith(G, 'merge', 'main')
  })

  it('dirty card: letters from the Base diff, ? for new files, "status unknown" until known', async () => {
    const fake = createFakeGitSource({
      status: status({
        baseDirty: { files: ['server/auth/session.py', 'ui/GitTab.vue'], mergeInProgress: false, aiRun: false },
        baseUntracked: { files: ['notes.md'], truncated: false, total: null },
      }),
      fileLetters: { 'server/auth/session.py': 'M' },
    })
    const w = await mountPanel(fake)
    expect(text('git-dirty-title')).toBe('미커밋 변경 · 3개 파일')
    const letters = qa('[data-test="git-dirty-card"] .gx-file-st').map((el) => el.textContent?.trim())
    expect(letters).toEqual(['M', '·', '?'])
    expect(qa('[data-test="git-dirty-card"] .st-unknown')[0].getAttribute('aria-label')).toBe('상태 미확인')
    qa('[data-test="git-dirty-card"] .gx-file')[2].click()
    expect(w.emitted('open-diff')?.[0]).toEqual([
      { projectId: P, groupId: null, mergeId: null, path: 'notes.md', status: '?', origin: 'dirty', diffSource: 'base' },
    ])
    expect(w.emitted('badge-change')?.at(-1)).toEqual([{ tone: 'count', text: '3' }])
  })

  it('dirty card stage 1 truncated: split count, both buttons off with the reason', async () => {
    const fake = createFakeGitSource({
      status: status({
        baseDirty: { files: ['a.ts', 'b.ts', 'c.ts'], mergeInProgress: false, aiRun: false },
        baseUntracked: { files: Array.from({ length: 200 }, (_, i) => `gen/${i}`), truncated: true, total: null },
      }),
    })
    const w = await mountPanel(fake)
    expect(text('git-dirty-title')).toBe('미커밋 변경 · 변경 3 · 새 파일 200개 이상')
    expect((q('git-dirty-commit') as HTMLButtonElement).disabled).toBe(true)
    expect((q('git-dirty-revert') as HTMLButtonElement).disabled).toBe(true)
    expect(text('git-dirty-reason')).toBe('새 파일이 200개 이상이라 전체 목록을 받을 수 없음')
    expect(w.emitted('badge-change')?.at(-1)).toEqual([{ tone: 'count', text: '203+' }])
  })

  it('dirty revert asks first and says new files cannot be recovered', async () => {
    const fake = createFakeGitSource({
      status: status({
        baseDirty: { files: ['a.ts'], mergeInProgress: false, aiRun: false },
        baseUntracked: { files: ['n.md'], truncated: false, total: null },
      }),
    })
    await mountPanel(fake)
    await click('git-dirty-revert')
    expect(document.body.textContent).toContain('새 파일 1개는 삭제되며 복구할 수 없습니다.')
    await confirmDialog()
    expect(fake.source.baseRevert).toHaveBeenCalledWith(P, ['a.ts'])
    expect(fake.source.baseRemove).toHaveBeenCalledWith(P, ['n.md'])
  })

  it('readOnly turns every action off', async () => {
    const fake = createFakeGitSource({ status: status({ pending: [row()] }) })
    const w = await mountPanel(fake, { readOnly: true })
    for (const id of ['git-conflict-ai', 'git-conflict-manual', 'git-conflict-abort']) {
      expect((q(id) as HTMLButtonElement).disabled, id).toBe(true)
    }
    await click('git-conflict-manual')
    expect(q('git-conflict-window')).toBeNull()
    expect(w.emitted('windows-change')?.at(-1) ?? [[]]).toEqual([[]])
    expect(fake.source.startConflictAi).not.toHaveBeenCalled()
    expect(fake.source.abortMerge).not.toHaveBeenCalled()
  })

  it('readOnly turns the hold card buttons off, settings included', async () => {
    const fake = createFakeGitSource({
      status: status({ pending: [row({ status: 'waiting', mergeId: null, plan: { action: 'merge', targetBranch: 'main' } })] }),
    })
    const w = await mountPanel(fake, { readOnly: true })
    expect((q('git-hold-apply') as HTMLButtonElement).disabled).toBe(true)
    expect((q('git-hold-settings') as HTMLButtonElement).disabled).toBe(true)
    await click('git-hold-settings')
    expect(w.emitted('open-confirm-settings')).toBeUndefined()
    expect(fake.source.finalize).not.toHaveBeenCalled()
  })

  it('readOnly turns the review and dirty card buttons off', async () => {
    const fake = createFakeGitSource({
      status: status({
        pending: [row({ reviewState: 'resolved_pending_review' })],
        baseDirty: { files: ['a.ts'], mergeInProgress: false, aiRun: false },
        baseUntracked: { files: ['n.md'], truncated: false, total: null },
      }),
    })
    await mountPanel(fake, { readOnly: true })
    for (const id of ['git-review-approve', 'git-review-reject', 'git-review-abort', 'git-dirty-commit', 'git-dirty-revert']) {
      expect((q(id) as HTMLButtonElement).disabled, id).toBe(true)
    }
  })
})

describe('DesignerGitPanel — remote apply failure and windows', () => {
  async function failed() {
    const fake = createFakeGitSource({
      status: status({ pending: [row({ status: 'waiting', mergeId: null, plan: { action: 'merge', targetBranch: 'main' } })] }),
    })
    fake.source.finalize.mockRejectedValueOnce(gitError(500, 'push_rejected'))
    const w = await mountPanel(fake)
    await click('git-hold-apply')
    await confirmDialog()
    return { fake, w }
  }

  it('the card holds only the title, ! and 다시 반영 — no server text', async () => {
    const { w } = await failed()
    const card = q('git-push-card') as HTMLElement
    expect(card.querySelectorAll('button')).toHaveLength(2)
    expect(card.textContent).not.toContain('rejected')
    expect(card.textContent).not.toContain('server text')
    expect(w.emitted('badge-change')?.at(-1)).toEqual([{ tone: 'danger', text: '!' }])
  })

  it('! opens the details window: code, target, "not provided", no retry button', async () => {
    const { w } = await failed()
    await click('git-push-detail')
    expect(text('git-push-code')).toBe('push_rejected')
    expect(text('git-push-target')).toBe('main')
    expect(text('git-push-diagnostic')).toBe('서버가 진단 정보를 제공하지 않았습니다.')
    const win = q('git-push-window') as HTMLElement
    expect(win.querySelectorAll('button')).toHaveLength(0)
    expect(w.emitted('windows-change')?.at(-1)).toEqual([
      [{ id: 'push-error', kind: 'push-error', title: '원격 반영 실패', minimized: false, groupId: null, mergeId: null }],
    ])
  })

  it('다시 반영 resends the recorded request', async () => {
    const { fake } = await failed()
    await click('git-push-retry')
    expect(fake.source.finalize).toHaveBeenCalledTimes(2)
    expect(fake.source.finalize).toHaveBeenLastCalledWith(G, 'merge', 'main')
    expect(q('git-push-card')).toBeNull()
  })

  it('the conflict window shows the v7 items and the expose controls it', async () => {
    const fake = createFakeGitSource({ status: status({ pending: [row()] }) })
    const w = await mountPanel(fake)
    await click('git-conflict-manual')
    expect(q('git-conflict-window')).not.toBeNull()
    await click('git-window-manual')
    expect(text('git-window-target')).toBe('main')
    expect(text('git-window-count')).toBe('5개')
    expect(text('git-window-branch')).toBe('flowgate_v02_0008')
    expect(qa('[data-test="git-window-files"] .gx-file')).toHaveLength(5)

    const id = `conflict:${G}:7`
    const minimize = qa('.ui-modeless-titlebar-actions button')[0]
    minimize.click()
    await flushPromises()
    expect(w.emitted('windows-change')?.at(-1)?.[0]).toEqual([
      { id, kind: 'conflict', title: '충돌 해결', minimized: true, groupId: G, mergeId: 7 },
    ])
    ;(w.vm as unknown as { restoreWindow(id: string): void }).restoreWindow(id)
    await flushPromises()
    expect(w.emitted('windows-change')?.at(-1)?.[0][0].minimized).toBe(false)
    ;(w.vm as unknown as { closeWindow(id: string): void }).closeWindow(id)
    await flushPromises()
    expect(q('git-conflict-window')).toBeNull()
    expect(w.emitted('windows-change')?.at(-1)).toEqual([[]])
  })

  it('the window AI button starts the resolver', async () => {
    const fake = createFakeGitSource({ status: status({ pending: [row()] }) })
    await mountPanel(fake)
    await click('git-conflict-manual')
    await click('git-window-ai')
    expect(fake.source.startConflictAi).toHaveBeenCalledWith(G, 7)
  })

  it('expose refresh reads the status again', async () => {
    const fake = createFakeGitSource()
    const w = await mountPanel(fake)
    const reads = fake.source.loadStatus.mock.calls.length
    await (w.vm as unknown as { refresh(): Promise<void> }).refresh()
    expect(fake.source.loadStatus.mock.calls.length).toBe(reads + 1)
  })
})
