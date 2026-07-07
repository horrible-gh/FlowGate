import { flushPromises, shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import GitActionMenu from '@main/components/GitActionMenu.vue'
import { useProjectStore } from '@main/stores/project'

const { getRequest, postRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
}))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  postRequest,
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))

function gitStatus(pendingCount = 1) {
  return {
    enabled: true,
    base_branch: 'main',
    pending_count: pendingCount,
    pending: pendingCount > 0
      ? [{ group_id: 'flowgate.default.0170', branch: 'main', status: 'waiting', default_action: 'push' }]
      : [],
  }
}

function mountMenu() {
  useProjectStore().setCurrentProject('flowgate')
  getRequest.mockResolvedValue({ data: { ok: true, status: gitStatus() } })
  return shallowMount(GitActionMenu, {
    global: {
      plugins: [i18n],
      stubs: { GitStatusPanel: true, teleport: true },
    },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'en'
  localStorage.clear()
  getRequest.mockReset()
  postRequest.mockReset()
})

describe('GitActionMenu approval-result events', () => {
  it('refreshes status when the approval flow dispatches fg:git_status_refresh', async () => {
    const wrapper = mountMenu()
    await flushPromises()
    expect(getRequest).toHaveBeenCalledTimes(1)

    window.dispatchEvent(new CustomEvent('fg:git_status_refresh', { detail: { project: 'flowgate' } }))
    await flushPromises()

    expect(getRequest).toHaveBeenCalledTimes(2)
    expect(getRequest).toHaveBeenLastCalledWith('/api/v1/projects/flowgate/git/status')
    wrapper.unmount()
  })

  it('opens the status panel and refreshes when the approval flow dispatches fg:git_status_open', async () => {
    const wrapper = mountMenu()
    await flushPromises()
    expect((wrapper.vm as any).panelOpen).toBe(false)

    window.dispatchEvent(new CustomEvent('fg:git_status_open', { detail: { project: 'flowgate' } }))
    await flushPromises()

    expect(getRequest).toHaveBeenCalledTimes(2)
    expect((wrapper.vm as any).panelOpen).toBe(true)
    wrapper.unmount()
  })

  it('ignores approval-result events for other projects', async () => {
    const wrapper = mountMenu()
    await flushPromises()

    window.dispatchEvent(new CustomEvent('fg:git_status_refresh', { detail: { project: 'other' } }))
    window.dispatchEvent(new CustomEvent('fg:git_status_open', { detail: { project: 'other' } }))
    await flushPromises()

    expect(getRequest).toHaveBeenCalledTimes(1)
    expect((wrapper.vm as any).panelOpen).toBe(false)
    wrapper.unmount()
  })
})