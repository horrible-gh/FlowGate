import { flushPromises, shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import GitActionMenu from '@main/components/GitActionMenu.vue'
import { useProjectStore } from '@main/stores/project'
import { useAiInvokeRunsStore } from '@main/stores/aiInvokeRuns'

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

function mountMenu(pendingCount = 1) {
  useProjectStore().setCurrentProject('flowgate')
  getRequest.mockResolvedValue({ data: { ok: true, status: gitStatus(pendingCount) } })
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
  // 0471 0020-TR rev3 rejection — "깃버튼은 눌러도 반응도 없고". The button used to
  // self-hide, then it rendered but was disabled whenever git status was unavailable.
  // Both read as a broken button, so it must be present AND answer a click.
  it('keeps the Git button visible and clickable before a Git status is available', async () => {
    const wrapper = shallowMount(GitActionMenu, {
      global: { plugins: [i18n], stubs: { GitStatusPanel: true, teleport: true } },
    })

    const button = wrapper.get('.git-menu-btn')
    expect(button.attributes('disabled')).toBeUndefined()

    await button.trigger('click')
    expect(wrapper.find('.git-menu-dd').exists()).toBe(true)
    expect(wrapper.get('.git-menu-dd').text()).toContain('Reading Git status')
    wrapper.unmount()
  })

  it('opens a dropdown explaining that git is unavailable for a non-git project', async () => {
    useProjectStore().setCurrentProject('hivework')
    getRequest.mockRejectedValue(new Error('403'))
    const wrapper = shallowMount(GitActionMenu, {
      global: { plugins: [i18n], stubs: { GitStatusPanel: true, teleport: true } },
    })
    await flushPromises()

    const button = wrapper.get('.git-menu-btn')
    expect(button.attributes('disabled')).toBeUndefined()

    await button.trigger('click')
    const dropdown = wrapper.get('.git-menu-dd')
    expect(dropdown.text()).toContain('Git integration is not set up for this project.')
    // The status panel link would open an empty control room here — hide it, but never
    // the dropdown itself.
    expect(wrapper.find('.git-menu-status-link').exists()).toBe(false)
    wrapper.unmount()
  })

  it('still lists finalize-pending groups once git status arrives', async () => {
    const wrapper = mountMenu()
    await flushPromises()

    await wrapper.get('.git-menu-btn').trigger('click')
    const dropdown = wrapper.get('.git-menu-dd')
    expect(dropdown.text()).toContain('flowgate.default.0170')
    expect(dropdown.text()).not.toContain('Git integration is not set up')
    expect(wrapper.find('.git-menu-status-link').exists()).toBe(true)
    wrapper.unmount()
  })

  it('renders recovered stale status as an openable, quiet empty Git menu', async () => {
    // Contract returned after project_git_status() resets stale awaiting_choice/
    // waiting ledger rows: the header must not keep an attention badge or a
    // finalize row merely because a prior response had one.
    const wrapper = mountMenu(0)
    await flushPromises()

    const button = wrapper.get('.git-menu-btn')
    expect(button.attributes('disabled')).toBeUndefined()
    expect(button.classes()).not.toContain('git-menu-attn')
    expect(wrapper.find('.git-menu-label').exists()).toBe(false)
    expect(wrapper.find('.git-menu-badge').exists()).toBe(false)

    await button.trigger('click')
    const dropdown = wrapper.get('.git-menu-dd')
    expect(dropdown.get('.git-menu-dd-hd').text()).toContain('(0)')
    expect(dropdown.find('.git-menu-row').exists()).toBe(false)
    expect(dropdown.find('.git-menu-empty').exists()).toBe(true)
    wrapper.unmount()
  })

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

  it('disables finalize for a group with an active AI run', async () => {
    const wrapper = mountMenu()
    await flushPromises()
    await wrapper.get('.git-menu-btn').trigger('click')
    useAiInvokeRunsStore().trackStarted({
      run_id: 'aiv_0424',
      group_id: 'flowgate.default.0170',
      doc_ref: 'flowgate.default.0170.0001-R',
      status: 'running',
    })
    await flushPromises()

    const execute = wrapper.get('.git-menu-row .btn-primary')
    expect(execute.attributes('disabled')).toBeDefined()
    expect(execute.attributes('title')).toContain('AI run')
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