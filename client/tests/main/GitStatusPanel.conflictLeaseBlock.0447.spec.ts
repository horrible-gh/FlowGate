import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import GitStatusPanel from '@main/components/GitStatusPanel.vue'
import GitConflictResolverDialog from '@main/components/GitConflictResolverDialog.vue'

const { getRequest, postRequest, showToast, writeText } = vi.hoisted(() => ({
  getRequest: vi.fn(), postRequest: vi.fn(), showToast: vi.fn(), writeText: vi.fn(),
}))
vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest, postRequest,
}))
vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast }),
}))

const DETAIL = "Cannot issue a resolve_conflict token while group 'flowgate.default.0447' has an active AI-run lease (run_id=aiv_1)."
function gitStatus() {
  return {
    enabled: true, base_branch: 'main', base_path_state: 'ready',
    ahead_count: 0, behind_count: 0, slots: [], pending_count: 1,
    pending: [{ group_id: 'flowgate.default.0447', branch: 'group/0447', status: 'conflict', default_action: 'merge', merge_id: 7 }],
  }
}
beforeEach(() => {
  setActivePinia(createPinia())
  getRequest.mockReset()
  postRequest.mockReset()
  showToast.mockReset()
  writeText.mockReset().mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
  getRequest.mockImplementation((url: string) => {
    if (url.endsWith('/git/status')) return Promise.resolve({ data: { ok: true, status: gitStatus() } })
    if (url.includes('/git/merge/7/conflicts')) return Promise.resolve({ data: { ok: true, files: [] } })
    return Promise.reject(new Error('unexpected GET ' + url))
  })
})
async function mountOpen() {
  const wrapper = mount(GitStatusPanel, {
    props: { projectId: 'flowgate' },
    global: { plugins: [i18n], stubs: { AppIcon: true, GitConflictResolverDialog: true } },
  })
  await flushPromises()
  await wrapper.find('.btn-danger-ol').trigger('click')
  await flushPromises()
  return wrapper
}
describe('GitStatusPanel resolve_conflict lease admission (0447)', () => {
  it('shows the 403 detail and releases busy', async () => {
    postRequest.mockRejectedValue({ response: { status: 403, data: { detail: DETAIL } } })
    const wrapper = await mountOpen()
    wrapper.findComponent(GitConflictResolverDialog).vm.$emit('copy-mention')
    await flushPromises()
    expect(showToast).toHaveBeenCalledWith(DETAIL, 'danger')
    expect(wrapper.findComponent(GitConflictResolverDialog).props('busy')).toBe(false)
    wrapper.unmount()
  })
  it('keeps normal mention copy working when admission passes', async () => {
    postRequest.mockResolvedValue({ data: { mention: 'mention body' } })
    const wrapper = await mountOpen()
    wrapper.findComponent(GitConflictResolverDialog).vm.$emit('copy-mention')
    await flushPromises()
    expect(writeText).toHaveBeenCalledWith('mention body')
    expect(showToast).toHaveBeenCalledWith(
      i18n.global.t('main.git_finalize.conflict_mention_copied'), 'success',
    )
    expect(wrapper.findComponent(GitConflictResolverDialog).props('busy')).toBe(false)
    wrapper.unmount()
  })
})