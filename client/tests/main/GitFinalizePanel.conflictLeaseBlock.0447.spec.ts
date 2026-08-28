import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import GitFinalizePanel from '@main/components/GitFinalizePanel.vue'
import GitConflictResolverDialog from '@main/components/GitConflictResolverDialog.vue'
import { useProjectStore } from '@main/stores/project'

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
const state = {
  group_id: 'flowgate.default.0447', branch: 'group/0447', base_branch: 'main',
  status: 'conflict', choices: [], ahead_count: 1, behind_count: 0, merge_id: 7,
}
function mountPanel() {
  useProjectStore().setCurrentProject('flowgate')
  return mount(GitFinalizePanel, {
    props: { groupId: 'flowgate.default.0447' },
    global: { plugins: [i18n], stubs: { AppIcon: true, GitConflictResolverDialog: true } },
  })
}
beforeEach(() => {
  setActivePinia(createPinia())
  getRequest.mockReset()
  postRequest.mockReset()
  showToast.mockReset()
  writeText.mockReset().mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
  getRequest.mockImplementation((url: string) => {
    if (url.endsWith('/git/finalize')) return Promise.resolve({ data: { ok: true, state } })
    if (url.includes('/git/merge/7/conflicts')) return Promise.resolve({ data: { ok: true, files: [] } })
    return Promise.reject(new Error('unexpected GET ' + url))
  })
})
async function open(wrapper: ReturnType<typeof mount>) {
  await flushPromises()
  await (wrapper.vm as any).openConflictDialog()
  await flushPromises()
  return wrapper.findComponent(GitConflictResolverDialog)
}
describe('GitFinalizePanel resolve_conflict lease admission (0447)', () => {
  it('shows the 403 detail and releases busy', async () => {
    postRequest.mockRejectedValue({ response: { status: 403, data: { detail: DETAIL } } })
    const wrapper = mountPanel()
    const dialog = await open(wrapper)
    dialog.vm.$emit('copy-mention')
    await flushPromises()
    expect(showToast).toHaveBeenCalledWith(DETAIL, 'danger')
    expect(wrapper.findComponent(GitConflictResolverDialog).props('busy')).toBe(false)
    wrapper.unmount()
  })
  it('keeps normal mention copy working when admission passes', async () => {
    postRequest.mockResolvedValue({ data: { mention: 'mention body' } })
    const wrapper = mountPanel()
    const dialog = await open(wrapper)
    dialog.vm.$emit('copy-mention')
    await flushPromises()
    expect(writeText).toHaveBeenCalledWith('mention body')
    expect(showToast).toHaveBeenCalledWith(
      i18n.global.t('main.git_finalize.conflict_mention_copied'), 'success',
    )
    expect(wrapper.findComponent(GitConflictResolverDialog).props('busy')).toBe(false)
    wrapper.unmount()
  })
})