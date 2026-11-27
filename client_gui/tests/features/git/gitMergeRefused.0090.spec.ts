import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { DesignerGitPanel } from '@/features/git'
import { createGitSession } from '@/features/git/gitSession'
import { worktreeReason, worktreeReasons, reasonMessage, type WorktreeReasonInput } from '@/features/git/worktreeReasons'
import { G, P, createFakeGitSource, row, status } from './fakeGitSource'
import { createFakeWorktreeSource, manualMergeView, mergeResultRow, mergeResults, worktreeFile, worktreeStatus } from './fakeWorktreeSource'
import { grantProject, resetCapabilities } from '../../helpers/capabilities'

// The Git panel follows TWO server answers: `project.git.write` (work-branch commit, work-base changes, update, fetch)
// and `project.git.merge` (merging into a base branch, Push, conflict handling, merge review). The roles that split them:
// a developer has write but not merge, a reviewer has merge but not write. Each control goes off only on the answer
// of its own permission, and the merge ones say why with the server's own sentence.

vi.mock('@shared/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@shared/api')>()),
  getRequest: () => new Promise(() => undefined),
}))

registerAppMessages(i18n)
let wrapper: VueWrapper | null = null
beforeEach(() => { i18n.global.locale.value = 'ko'; resetCapabilities() })
afterEach(() => { wrapper?.unmount(); wrapper = null; document.body.innerHTML = '' })

const WRITE_NO = { 'project.git.write': { reason: 'role_limit', minimum_role: 'role_developer' } }
const MERGE_NO = { 'project.git.merge': { reason: 'role_limit', minimum_role: 'role_reviewer' } }
type Refusal = Record<string, { reason: string; minimum_role?: string }>
const ROLES: Record<string, { role: string; refused: Refusal }> = {
  viewer: { role: 'role_viewer', refused: { ...WRITE_NO, ...MERGE_NO } },
  developer: { role: 'role_developer', refused: MERGE_NO },
  reviewer: { role: 'role_reviewer', refused: WRITE_NO },
  admin: { role: 'role_admin', refused: {} },
}
const grant = (name: keyof typeof ROLES) => grantProject(P, ROLES[name].role, ROLES[name].refused)

const FLOW = { groupId: G, name: '인증 오류 조사' }
const button = (id: string) => document.body.querySelector<HTMLButtonElement>(`[data-test="${id}"]`)
const off = (id: string) => { const b = button(id); expect(b, id).not.toBeNull(); return b!.disabled }

async function mountCards(pending: ReturnType<typeof row>[], props: Record<string, unknown> = {}) {
  const fake = createFakeGitSource({ status: status({ pending, baseDirty: { files: ['a.ts'], mergeInProgress: false, aiRun: false } }) })
  wrapper = mount(DesignerGitPanel, { props: { projectId: P, flow: FLOW, source: fake.source, ...props }, attachTo: document.body, global: { plugins: [i18n] } })
  await flushPromises()
  return fake
}

describe('review card (merge scope) and base dirty card (write scope)', () => {
  const MERGE_CONTROLS = ['git-review-approve', 'git-review-reject', 'git-review-abort']
  const WRITE_CONTROLS = ['git-dirty-commit', 'git-dirty-revert']

  it.each([
    ['viewer', true, true],
    ['developer', true, false],
    ['reviewer', false, true],
    ['admin', false, false],
  ] as const)('%s: merge controls off=%s, write controls off=%s', async (name, mergeOff, writeOff) => {
    grant(name)
    await mountCards([row({ reviewState: 'resolved_pending_review' })])
    for (const id of MERGE_CONTROLS) expect(off(id), `${name} ${id}`).toBe(mergeOff)
    for (const id of WRITE_CONTROLS) expect(off(id), `${name} ${id}`).toBe(writeOff)
  })

  it('a reviewer (merge only) can still approve: the server gets the approval', async () => {
    grant('reviewer')
    const fake = await mountCards([row({ reviewState: 'resolved_pending_review' })])
    button('git-review-approve')!.click()
    await flushPromises()
    const confirm = Array.from(document.body.querySelectorAll<HTMLButtonElement>('.ui-confirm-actions button')).at(-1)
    expect(confirm).toBeDefined()
    confirm!.click()
    await flushPromises()
    expect(fake.source.approveReview).toHaveBeenCalledTimes(1)
  })

  it('a developer (write, no merge) never sends an approval or an abort', async () => {
    grant('developer')
    const fake = await mountCards([row({ reviewState: 'resolved_pending_review' })])
    button('git-review-approve')!.click()
    button('git-review-abort')!.click()
    await flushPromises()
    expect(fake.source.approveReview).not.toHaveBeenCalled()
    expect(fake.source.abortMerge).not.toHaveBeenCalled()
  })

  it('a read-only Flow still turns both scopes off', async () => {
    grant('admin')
    await mountCards([row({ reviewState: 'resolved_pending_review' })], { readOnly: true })
    for (const id of [...MERGE_CONTROLS, ...WRITE_CONTROLS]) expect(off(id), id).toBe(true)
  })

  it('no answer yet: nothing is turned off', async () => {
    await mountCards([row({ reviewState: 'resolved_pending_review' })])
    for (const id of [...MERGE_CONTROLS, ...WRITE_CONTROLS]) expect(off(id), id).toBe(false)
  })
})

describe('conflict card (merge scope)', () => {
  const CONTROLS = ['git-conflict-ai', 'git-conflict-manual', 'git-conflict-abort']

  it.each([
    ['viewer', true],
    ['developer', true],
    ['reviewer', false],
    ['admin', false],
  ] as const)('%s: conflict controls off=%s', async (name, isOff) => {
    grant(name)
    await mountCards([row()])
    for (const id of CONTROLS) expect(off(id), `${name} ${id}`).toBe(isOff)
  })

  it('a developer is told why with the server\'s sentence (reviewer or above needed)', async () => {
    grant('developer')
    await mountCards([row()])
    const reason = document.body.querySelector('[data-test="git-conflict-blocked"]')?.textContent ?? ''
    expect(reason).toContain('개발자')
    expect(reason).toContain('리뷰어')
    expect(reason).not.toContain('읽기 전용')
  })

  it('a reviewer sees no conflict reason', async () => {
    grant('reviewer')
    await mountCards([row()])
    expect(document.body.querySelector('[data-test="git-conflict-blocked"]')).toBeNull()
  })
})

describe('worktree quick actions: commit / update under write, merge / Push under merge', () => {
  async function mountQuick(readOnly = false) {
    const files = [worktreeFile('src/a.ts')]
    const wt = createFakeWorktreeSource({
      status: worktreeStatus({ files }),
      merge: manualMergeView({ aheadOfTarget: 1 }),
      results: mergeResults([mergeResultRow({ pushAvailable: true })]),
    })
    const fake = createFakeGitSource({}, { worktree: wt.source })
    wrapper = mount(DesignerGitPanel, { props: { projectId: P, flow: FLOW, source: fake.source, readOnly }, attachTo: document.body, global: { plugins: [i18n] } })
    await flushPromises()
    return wt
  }

  it('viewer: commit and merge are off', async () => {
    grant('viewer')
    await mountQuick()
    expect(off('git-open-commit')).toBe(true)
    expect(off('git-open-merge')).toBe(true)
  })

  it('developer: commit stays on, merge is off with the server\'s sentence', async () => {
    grant('developer')
    await mountQuick()
    expect(off('git-open-commit')).toBe(false)
    expect(off('git-open-merge')).toBe(true)
    expect(off('git-open-push')).toBe(true)
    const why = document.body.querySelector('[data-test="git-merge-refused"]')?.textContent ?? ''
    expect(why).toContain('리뷰어')
  })

  it('reviewer: merge is not switched off by the missing write permission; commit is', async () => {
    grant('reviewer')
    await mountQuick()
    expect(off('git-open-commit')).toBe(true)
    expect(document.body.querySelector('[data-test="git-merge-refused"]')).toBeNull()
  })

  it('admin: nothing is refused by a permission', async () => {
    grant('admin')
    await mountQuick()
    expect(off('git-open-commit')).toBe(false)
    expect(document.body.querySelector('[data-test="git-merge-refused"]')).toBeNull()
  })

  it('the run button: a commit-scope run needs write only; merge / push scopes need merge too', async () => {
    grant('developer')
    await mountQuick()
    const scopes = document.body.querySelectorAll<HTMLButtonElement>('[data-test="git-scopes"] button')
    scopes[0].click() // commit scope
    await flushPromises()
    expect(off('git-execute')).toBe(false)
    scopes[1].click() // merge scope
    await flushPromises()
    expect(off('git-execute')).toBe(true)
  })
})

describe('session scopes', () => {
  it('setReadOnly with one argument sets both scopes; two arguments set them apart', () => {
    const session = createGitSession({ source: createFakeGitSource().source })
    session.setReadOnly(true)
    expect([session.state.readOnly, session.state.mergeReadOnly]).toEqual([true, true])
    expect([session.canAct.value, session.canMerge.value]).toEqual([false, false])
    session.setReadOnly(false, true, '합치기 못 함')
    expect([session.canAct.value, session.canMerge.value]).toEqual([true, false])
    expect(session.state.mergeReason).toBe('합치기 못 함')
    session.setReadOnly(true, false)
    expect([session.canAct.value, session.canMerge.value]).toEqual([false, true])
    expect(session.state.mergeReason).toBeNull()
  })
})

describe('worktreeReasons: each button under its own permission', () => {
  const input = (overrides: Partial<WorktreeReasonInput> = {}): WorktreeReasonInput => ({
    readOnly: false,
    forbidden: false,
    busy: false,
    status: null,
    sessionCard: false,
    hiddenStaged: 0,
    stagedProtectedRows: 0,
    message: '',
    providerId: null,
    ...overrides,
  })

  it('write refused only: merge and push are not read-only (they fall through to their own rules)', () => {
    const reasons = worktreeReasons(input({ readOnly: true, mergeReadOnly: false }))
    expect(reasons.commit.code).toBe('read_only')
    expect(reasons.stage.code).toBe('read_only')
    expect(reasons.fetch.code).toBe('read_only')
    expect(reasons.merge.code).not.toBe('read_only')
    expect(reasons.merge.code).not.toBe('merge_read_only')
    expect(reasons.push.code).not.toBe('merge_read_only')
  })

  it('merge refused only: merge and push say so, the others are untouched by it', () => {
    const reasons = worktreeReasons(input({ readOnly: false, mergeReadOnly: true }))
    expect(reasons.merge).toMatchObject({ disabled: true, code: 'merge_read_only' })
    expect(reasons.push).toMatchObject({ disabled: true, code: 'merge_read_only' })
    expect(reasons.fetch.disabled).toBe(false)
    expect(reasons.commit.code).not.toBe('read_only')
  })

  it('both refused: plain read-only everywhere; no mergeReadOnly given: readOnly decides both (as before)', () => {
    const both = worktreeReasons(input({ readOnly: true, mergeReadOnly: true }))
    expect(Object.values(both).every((r) => r.code === 'read_only')).toBe(true)
    expect(worktreeReason('merge', input({ readOnly: true })).code).toBe('read_only')
    expect(worktreeReason('push', input({ readOnly: true })).code).toBe('read_only')
  })

  it('merge_read_only has its own text, and the server\'s sentence wins when the reason carries one', () => {
    const plain = reasonMessage({ disabled: true, code: 'merge_read_only' })
    expect(plain?.key).toBe('gui.git.reason.merge_read_only')
    expect(plain?.text).toBeUndefined()
    expect(reasonMessage({ disabled: true, code: 'merge_read_only', text: '서버 문장' })?.text).toBe('서버 문장')
    for (const lang of ['ko', 'en', 'ja']) {
      i18n.global.locale.value = lang as 'ko'
      expect(i18n.global.t('gui.git.reason.merge_read_only')).not.toBe('gui.git.reason.merge_read_only')
      expect(i18n.global.t('gui.git.conflict.blocked.merge_read_only')).not.toBe('gui.git.conflict.blocked.merge_read_only')
    }
  })
})
