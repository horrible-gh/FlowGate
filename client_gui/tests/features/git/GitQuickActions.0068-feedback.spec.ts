import { afterEach, describe, it, expect, vi } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { DesignerGitPanel } from '@/features/git'
import type { GitWorktreeSource } from '@/features/git/gitSource'
import { createFakeGitSource, gitError, G, P } from './fakeGitSource'
import { createFakeWorktreeSource, worktreeStatus, manualMergeView, mergeResults, mergeResultRow } from './fakeWorktreeSource'
registerAppMessages(i18n)
let w: VueWrapper | null = null
afterEach(() => { w?.unmount(); document.body.innerHTML = ''; w = null })
// A Flow with nothing left to merge or push, so a run that gets past 받아오기 finishes.
const done = () => ({ merge: manualMergeView({ aheadOfTarget: 0 }), results: mergeResults({ results: [mergeResultRow()] }) })
async function setup(extra: Partial<GitWorktreeSource>, seed: Parameters<typeof createFakeWorktreeSource>[0] = {}) {
  i18n.global.locale.value = 'ko'
  const wt = createFakeWorktreeSource({ status: worktreeStatus({ files: [] }), ...seed }, extra)
  const fake = createFakeGitSource({}, { worktree: wt.source })
  w = mount(DesignerGitPanel, { props: { projectId: P, flow: { groupId: G, name: 'Flow' }, source: fake.source, readOnly: false }, attachTo: document.body, global: { plugins: [i18n] } })
  await flushPromises(); return wt
}
describe('0068 quick execution feedback', () => {
  it('says in words why a push-scope run stopped at update, with its code and a retry', async () => {
    const update = vi.fn(async () => { throw gitError(409, 'group_untracked_conflict') })
    await setup({ updateFromBase: update as unknown as GitWorktreeSource['updateFromBase'] }, done())
    await w!.find('[data-test=git-execute]').trigger('click'); await flushPromises()
    const result = w!.find('[data-test=git-execution-result]')
    expect(result.text()).toContain('받아오기에서 멈춤')
    expect(w!.find('[data-test=git-stop-reason]').text()).toContain('작업 파일이 받아오기를 막음')
    expect(w!.find('[data-test=git-stop-reason]').attributes('title')).toContain('group_untracked_conflict')
    expect(w!.find('[data-test=git-fix-files]').exists()).toBe(true)
    update.mockImplementation(async () => ({ status: 'no_change', mergeId: null }) as never)
    await w!.find('[data-test=git-retry]').trigger('click'); await flushPromises()
    expect(update).toHaveBeenCalledTimes(2)
    expect(w!.find('[data-test=git-stop-reason]').exists()).toBe(false)
  })
  it('passes over an update the server refuses for its own state and says so in one word', async () => {
    for (const code of ['git_error', 'base_dirty', 'group_work_base_unverified']) {
      w?.unmount()
      await setup({ updateFromBase: (async () => { throw gitError(409, code) }) as GitWorktreeSource['updateFromBase'] }, done())
      await w!.find('[data-test=git-execute]').trigger('click'); await flushPromises()
      expect(w!.find('[data-test=git-stop-reason]').exists()).toBe(false)
      const result = w!.find('[data-test=git-execution-result]')
      expect(result.text()).toContain('받아오기 건너뜀')
      expect(result.attributes('title')).toContain(code)
    }
  })
  it('shows what an individual update did right in the Git tab', async () => {
    await setup({ updateFromBase: async () => ({ status: 'no_change', mergeId: null }) })
    await w!.find('[data-test=git-action-update]').trigger('click'); await flushPromises()
    expect(w!.find('[data-test=git-quick-notice-apply]').text()).toContain('이미 최신')
    expect(w!.find('[data-test=git-quick-notice-apply]').classes()).toContain('is-ok')
  })
  it('shows an individual update refusal as a reason, not a bare failure', async () => {
    await setup({ updateFromBase: (async () => { throw gitError(409, 'base_dirty') }) as GitWorktreeSource['updateFromBase'] })
    await w!.find('[data-test=git-action-update]').trigger('click'); await flushPromises()
    const line = w!.find('[data-test=git-quick-notice-apply]')
    expect(line.text()).toContain('베이스에 커밋 안 한 수정 파일')
    expect(line.text()).toContain('base_dirty')
    expect(line.classes()).toContain('is-error')
  })
})

