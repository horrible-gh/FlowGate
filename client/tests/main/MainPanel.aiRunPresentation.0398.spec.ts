import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'

const { getRequest, postRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
}))
vi.mock('@shared/api', () => ({
  getRequest: (...args: unknown[]) => getRequest(...args),
  postRequest: (...args: unknown[]) => postRequest(...args),
  patchRequest: (...args: unknown[]) => postRequest(...args),
  putRequest: (...args: unknown[]) => postRequest(...args),
  deleteRequest: (...args: unknown[]) => postRequest(...args),
  default: { get: (...args: unknown[]) => getRequest(...args) },
}))

import MainPanel from '@main/components/MainPanel.vue'
import { useAiInvokeRunsStore } from '@main/stores/aiInvokeRuns'

const GROUP_ID = 'flowgate.default.0398'
const DOC_ID = `${GROUP_ID}.0007-T`
const TAB = {
  id: DOC_ID,
  title: 'AI read-only implementation',
  path: 'documents/flowgate/main/default/0398/0007-T_document.md',
  type: 'md',
  typeCode: 'T',
  projectId: 'flowgate',
}

function seedTab() {
  localStorage.setItem('flowgate.user.guest.tabs', JSON.stringify({ tabs: [TAB], activeTabId: DOC_ID }))
}

describe('MainPanel AI-run rendered presentation (0398)', () => {
  let resolveBootstrap: ((value: unknown) => void) | null = null

  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    seedTab()
    postRequest.mockReset().mockResolvedValue({ data: {} })
    const bootstrap = new Promise((resolve) => { resolveBootstrap = resolve })
    getRequest.mockReset().mockImplementation((url: unknown) => {
      const value = String(url)
      if (value.includes('/ai-invoke/active-all')) return bootstrap
      if (value.includes('/documents/detail')) {
        return Promise.resolve({ data: {
          doc_id: DOC_ID,
          title: TAB.title,
          status: 'open',
          doc_review_status: 'pending_review',
          type_code: 'T',
          group_id: GROUP_ID,
          project_id: 'flowgate',
          owner_id: null,
          workflow_steps: ['T', 'TR'],
        } })
      }
      if (value.includes('/documents/content')) return Promise.resolve({ data: { content: '# Actual document body' } })
      if (value.includes('/groups')) return Promise.resolve({ data: { groups: [{ group_id: GROUP_ID, title: '0398 AI run screen' }] } })
      if (value.includes('/relations')) return Promise.resolve({ data: { workflow: { orphan: false } } })
      return Promise.resolve({ data: {} })
    })
  })

  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('fails closed during bootstrap, then renders the sticky card with locked readable content', async () => {
    const wrapper = mount(MainPanel, {
      attachTo: document.body,
      shallow: true,
      global: {
        plugins: [i18n],
        stubs: {
          teleport: false,
          AiInvokeInline: false,
          DocHeader: false,
          MdViewer: false,
        },
      },
    })
    await nextTick()

    expect(wrapper.find('.ai-run-bootstrap-pending').exists()).toBe(true)
    expect(wrapper.find('.doc-header').exists()).toBe(false)
    expect(wrapper.find('.md-viewer').exists()).toBe(false)

    resolveBootstrap?.({ data: { runs: [], paused: [] } })
    await flushPromises()
    useAiInvokeRunsStore().trackStarted({
      run_id: 'run-0398-visual',
      group_id: GROUP_ID,
      doc_ref: DOC_ID,
      status: 'running',
      provider_id: 'claude',
      provider_name: 'Claude',
    })
    await flushPromises()

    expect(wrapper.find('.ai-invoke-status-card').exists()).toBe(true)
    expect(wrapper.find('.ai-invoke-status-spinner').exists()).toBe(true)
    expect(wrapper.find('.ai-invoke-status-actions button').exists()).toBe(true)
    expect(wrapper.find('[data-test="ai-inline-running-meta"]').text()).toContain('Claude')
    expect(wrapper.find('[data-test="ai-inline-running-meta"]').text()).toMatch(/\d+:\d{2}/)
    expect(wrapper.find('.doc-header').exists()).toBe(true)
    expect(wrapper.find('.doc-header .ro-badge').text()).toContain(i18n.global.t('main.doc_header.read_only_ai'))
    expect(wrapper.find('.md-viewer').text()).toContain('Actual document body')
    expect(wrapper.find('.md-preview-card .ro-badge-sm').text()).toContain(i18n.global.t('main.document_preview.edit_locked'))
    expect(wrapper.findComponent({ name: 'DocWorkflow' }).exists()).toBe(false)
    expect(wrapper.findComponent({ name: 'TestRunStrip' }).exists()).toBe(false)
    expect(wrapper.findComponent({ name: 'TestFailStrip' }).exists()).toBe(false)
    expect(wrapper.findComponent({ name: 'GitFinalizePanel' }).exists()).toBe(false)
    expect(wrapper.findComponent({ name: 'DocInfoPanel' }).exists()).toBe(false)
    expect(wrapper.findComponent({ name: 'ReviewActionBar' }).exists()).toBe(false)

    if (process.env.FLOWGATE_DOM_DUMP === '1') {
      console.log('FLOWGATE_AI_RUN_DOM_BEGIN')
      console.log(wrapper.find('.content-panel').html())
      console.log('FLOWGATE_AI_RUN_DOM_END')
    }
  })
})