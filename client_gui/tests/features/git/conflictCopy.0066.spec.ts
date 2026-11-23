import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import GitConflictCard from '@/features/git/GitConflictCard.vue'
import { createGitSession, GIT_PANEL_CONTEXT, type GitSession } from '@/features/git/gitSession'
import { GATE_OPERATIONS_CONTEXT } from '@/features/git/gateOperations'
import { GIT_WORKTREE_CONTEXT } from '@/features/git/gitWorktreeSession'
import { G, P, conflicts, createFakeGitSource, gitError, manualSleep, networkError, row, status } from './fakeGitSource'

registerAppMessages(i18n)
let wrapper: VueWrapper | null = null
let session: GitSession | null = null
beforeEach(() => { i18n.global.locale.value = 'ko' })
afterEach(() => { wrapper?.unmount(); session?.dispose(); wrapper = null; session = null })

async function open(fake = createFakeGitSource({ status: status({ pending: [row()] }) }), approval?: { frozen?: string; head?: string; code?: string }) {
  session = createGitSession({ source: fake.source, sleep: manualSleep().sleep })
  session.setContext(P, { groupId: G, name: 'Conflict copy' })
  await flushPromises()
  const confirm = vi.fn()
  const operation = ref(approval ? {
    runId: 'run', nodeKey: 'check', gate: {
      decision: { frozen_sha: approval.frozen },
      recovery: { error_code: approval.code ?? 'gate_conflict' },
    },
  } : null)
  wrapper = mount(GitConflictCard, { global: {
    plugins: [i18n],
    provide: {
      [GIT_PANEL_CONTEXT as symbol]: { session, confirm, openDiff: vi.fn(), openSettings: vi.fn() },
      [GATE_OPERATIONS_CONTEXT as symbol]: { conflictOperation: operation, busy: ref(false), readOnly: () => false },
      [GIT_WORKTREE_CONTEXT as symbol]: { worktree: { state: { status: { head: approval?.head ?? null } } } },
    },
  } })
  return { wrapper, session, fake, confirm }
}

describe('conflict card copy', () => {
  it('shows loading once while a real request is pending, then enables actions', async () => {
    const fake = createFakeGitSource({ status: status({ pending: [row()] }) })
    let release!: (value: ReturnType<typeof conflicts>) => void
    fake.source.fetchConflicts.mockImplementationOnce(() => new Promise(resolve => { release = resolve }))
    const { wrapper } = await open(fake)
    expect(wrapper.text().split('충돌 파일을 불러오는 중…')).toHaveLength(2)
    expect(wrapper.get('[data-test="git-conflict-ai"]').attributes('disabled')).toBeDefined()
    release(conflicts())
    await flushPromises()
    expect(wrapper.text()).not.toContain('불러오는 중')
    expect(wrapper.get('[data-test="git-conflict-ai"]').attributes('disabled')).toBeUndefined()
  })

  it.each([['server', gitError(500, 'internal')], ['network', networkError()]])('shows a failed read instead of perpetual loading: %s', async (_label, error) => {
    const fake = createFakeGitSource({ status: status({ pending: [row()] }) })
    fake.source.fetchConflicts.mockRejectedValueOnce(error)
    const { wrapper, session } = await open(fake)
    expect(session.state.conflictLoad).toBe('error')
    expect(wrapper.get('[data-test="git-conflict-blocked"]').text()).toContain('충돌 파일을 불러오지 못해')
    expect(wrapper.find('[data-test="git-notice"]').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('불러오는 중')
    expect(wrapper.get('[data-test="git-conflict-manual"]').attributes('disabled')).toBeDefined()
  })

  it('keeps a missing merge ID visible without requesting or enabling a merge action', async () => {
    const fake = createFakeGitSource({ status: status({ pending: [row({ mergeId: null })] }) })
    const { wrapper, session } = await open(fake)
    expect(session.groupCard.value).toBe('conflict')
    expect(fake.source.fetchConflicts).not.toHaveBeenCalled()
    expect(wrapper.get('[data-test="git-conflict-blocked"]').text()).toContain('충돌 번호 없음')
    expect(wrapper.get('[data-test="git-conflict-abort"]').attributes('disabled')).toBeDefined()
    await session.abortMerge()
    await session.startAi()
    expect(fake.source.abortMerge).not.toHaveBeenCalled()
    expect(fake.source.startConflictAi).not.toHaveBeenCalled()
  })

  it('shows permission denial only once', async () => {
    const fake = createFakeGitSource({ status: status({ pending: [row()] }) })
    fake.source.fetchConflicts.mockRejectedValueOnce(gitError(403, 'forbidden'))
    const { wrapper } = await open(fake)
    expect(wrapper.text().split('권한 없음')).toHaveLength(2)
    expect(wrapper.text()).not.toContain('불러오는 중')
  })

  it('explains read-only actions without claiming to be loading', async () => {
    const { wrapper, session } = await open()
    session.setReadOnly(true)
    await flushPromises()
    expect(wrapper.get('[data-test="git-conflict-blocked"]').text()).toBe('읽기 전용이라 조작할 수 없음')
    expect(wrapper.text()).not.toContain('불러오는 중')
  })

  it.each(['ko', 'en', 'ja'] as const)('has translated title and abort tooltip in %s', async locale => {
    i18n.global.locale.value = locale
    const { wrapper } = await open()
    expect(wrapper.get('.gx-title').text()).toBe(i18n.global.t('gui.git.conflict.title_target', { target: 'main' }))
    const hint = wrapper.get('[data-test="git-conflict-abort"]').attributes('title')
    expect(hint).toBe(i18n.global.t('gui.git.conflict.abort_hint'))
    expect(hint).not.toContain('gui.git.')
  })
})

describe('reapproval copy', () => {
  const sha = 'aecdcd26' + '1'.repeat(32)
  it.each(['merge_conflict_open', 'gate_conflict', 'merge_conflict'])('hides equal commits and repeated conflict reason %s', async code => {
    const { wrapper, confirm } = await open(undefined, { frozen: sha, head: sha, code })
    expect(wrapper.find('[data-test="git-reapprove-pin"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="git-reapprove-blocked"]').exists()).toBe(false)
    const button = wrapper.get('[data-test="git-reapprove-start"]')
    expect(button.text()).toBe('처음부터 다시 승인')
    expect(button.attributes('title')).toContain('확인 카드에서 [승인]')
    await button.trigger('click')
    expect(confirm).toHaveBeenCalledWith(expect.objectContaining({ title: '처음부터 다시 승인', confirmLabel: '처음부터 다시 승인' }))
  })

  it('shows changed commits and a non-conflict reason', async () => {
    const { wrapper } = await open(undefined, { frozen: sha, head: 'bbbbbbbb' + '2'.repeat(32), code: 'gate_effects_unknown' })
    expect(wrapper.get('[data-test="git-reapprove-pin"]').text()).toBe('승인한 뒤 새 커밋 있음 · aecdcd26 → bbbbbbbb')
    expect(wrapper.get('[data-test="git-reapprove-blocked"]').text()).toContain('바깥 반영 여부를 확인할 수 없음')
    expect(wrapper.get('[data-test="git-reapprove-start"]').text()).toBe('새 커밋으로 다시 승인')
    expect(wrapper.get('[data-test="git-reapprove-start"]').attributes('title')).toContain('이전 승인을 취소')
  })

  it('does not equate two full hashes merely because their first seven characters match', async () => {
    const { wrapper } = await open(undefined, { frozen: sha, head: 'aecdcd27' + '2'.repeat(32) })
    expect(wrapper.get('[data-test="git-reapprove-pin"]').text()).toContain('aecdcd26 → aecdcd27')
  })

  it('accepts a shortened prefix of the same commit', async () => {
    const { wrapper } = await open(undefined, { frozen: sha.slice(0, 8), head: sha })
    expect(wrapper.find('[data-test="git-reapprove-pin"]').exists()).toBe(false)
    expect(wrapper.get('[data-test="git-reapprove-start"]').text()).toBe('처음부터 다시 승인')
  })

  it.each([{ frozen: sha }, { head: sha }, {}])('does not claim commit movement with missing SHA data: %j', async approval => {
    const { wrapper } = await open(undefined, approval)
    expect(wrapper.find('[data-test="git-reapprove-pin"]').exists()).toBe(false)
    expect(wrapper.get('[data-test="git-reapprove-start"]').text()).toBe('다시 승인')
  })
})
