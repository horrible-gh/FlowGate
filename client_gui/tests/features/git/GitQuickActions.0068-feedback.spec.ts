import { afterEach, describe, it, expect, vi } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { DesignerGitPanel } from '@/features/git'
import type { GitWorktreeSource } from '@/features/git/gitSource'
import { createFakeGitSource, gitError, G, P } from './fakeGitSource'
import { createFakeWorktreeSource, worktreeStatus } from './fakeWorktreeSource'
registerAppMessages(i18n)
let w: VueWrapper | null = null
afterEach(() => { w?.unmount(); document.body.innerHTML = ''; w = null })
async function setup(extra: Partial<GitWorktreeSource>) {
  i18n.global.locale.value = 'ko'
  const wt = createFakeWorktreeSource({ status: worktreeStatus({ files: [] }) }, extra)
  const fake = createFakeGitSource({}, { worktree: wt.source })
  w = mount(DesignerGitPanel, { props: { projectId: P, flow: { groupId: G, name: 'Flow' }, source: fake.source, readOnly: false }, attachTo: document.body, global: { plugins: [i18n] } })
  await flushPromises(); return wt
}
describe('0068 quick execution feedback', () => {
  it('says in words why a push-scope run stopped at update, with its code and a retry', async () => {
    const update = vi.fn(async () => { throw gitError(409, 'base_dirty') })
    await setup({ updateFromBase: update as unknown as GitWorktreeSource['updateFromBase'] })
    await w!.find('[data-test=git-execute]').trigger('click'); await flushPromises()
    const result = w!.find('[data-test=git-execution-result]')
    expect(result.text()).toContain('받아오기 단계에서 멈춤')
    expect(w!.find('[data-test=git-stop-reason]').text()).toContain('베이스 체크아웃에 커밋되지 않은 수정 파일')
    expect(w!.find('[data-test=git-stop-code]').text()).toContain('base_dirty')
    expect(w!.find('[data-test=git-fix-status]').exists()).toBe(true)
    update.mockImplementation(async () => ({ status: 'no_change', mergeId: null }) as never)
    await w!.find('[data-test=git-retry]').trigger('click'); await flushPromises()
    expect(update).toHaveBeenCalledTimes(2)
    expect(w!.find('[data-test=git-stop-reason]').exists()).toBe(false)
  })
  it('offers Git settings when fetching from the remote fails', async () => {
    await setup({ updateFromBase: (async () => { throw gitError(500, 'git_error') }) as GitWorktreeSource['updateFromBase'] })
    await w!.find('[data-test=git-execute]').trigger('click'); await flushPromises()
    expect(w!.find('[data-test=git-stop-reason]').text()).toContain('인증 정보나 네트워크')
    expect(w!.find('[data-test=git-fix-settings]').exists()).toBe(true)
  })
  it('shows what an individual update did right in the Git tab', async () => {
    await setup({ updateFromBase: async () => ({ status: 'no_change', mergeId: null }) })
    await w!.find('[data-test=git-action-update]').trigger('click'); await flushPromises()
    expect(w!.find('[data-test=git-quick-notice-apply]').text()).toContain('이미 최신입니다')
    expect(w!.find('[data-test=git-quick-notice-apply]').classes()).toContain('is-ok')
  })
  it('shows an individual update refusal as a reason, not a bare failure', async () => {
    await setup({ updateFromBase: (async () => { throw gitError(409, 'base_dirty') }) as GitWorktreeSource['updateFromBase'] })
    await w!.find('[data-test=git-action-update]').trigger('click'); await flushPromises()
    const line = w!.find('[data-test=git-quick-notice-apply]')
    expect(line.text()).toContain('베이스 체크아웃에 커밋되지 않은 수정 파일')
    expect(line.text()).toContain('base_dirty')
    expect(line.classes()).toContain('is-error')
  })
})
