import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent, onMounted } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { useFlowGateSse } from '@main/composables/useFlowGateSse'

const { showToast } = vi.hoisted(() => ({ showToast: vi.fn() }))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast }),
}))

class MockEventSource {
  static instance: MockEventSource | null = null
  listeners = new Map<string, (event: Event) => void>()

  constructor(_url: string, _options?: EventSourceInit) {
    MockEventSource.instance = this
  }

  addEventListener(type: string, listener: EventListenerOrEventListenerObject) {
    this.listeners.set(type, listener as (event: Event) => void)
  }

  emit(type: string, data: object) {
    this.listeners.get(type)?.({ data: JSON.stringify(data) } as MessageEvent)
  }

  close() {}
}

const Harness = defineComponent({
  setup() {
    const sse = useFlowGateSse(vi.fn())
    onMounted(() => {
      // useFlowGateSse registers its own mounted hook.
    })
    return sse
  },
  template: '<div />',
})

beforeEach(() => {
  setActivePinia(createPinia())
  MockEventSource.instance = null
  showToast.mockReset()
  vi.stubGlobal('EventSource', MockEventSource)
})

describe('useFlowGateSse document content bridge', () => {
  it('shows a success toast when an undecided R workflow is decided', () => {
    const wrapper = mount(Harness, { global: { plugins: [i18n] } })

    MockEventSource.instance?.emit('doc_review_status_changed', {
      project: 'test',
      doc_id: 'test.test.0004.0001-R',
      payload: {
        doc_id: 'test.test.0004.0001-R',
        prev_status: null,
        next_status: 'wf_in_progress',
      },
    })

    expect(showToast).toHaveBeenCalledWith(
      'Workflow decided for test.test.0004.0001-R.',
      'info',
    )
    wrapper.unmount()
  })

  it('does not repeat the workflow toast for an in-progress status update', () => {
    const wrapper = mount(Harness, { global: { plugins: [i18n] } })

    MockEventSource.instance?.emit('doc_review_status_changed', {
      project: 'test',
      payload: {
        doc_id: 'test.test.0004.0001-R',
        prev_status: 'wf_in_progress',
        next_status: 'wf_in_progress',
      },
    })

    expect(showToast).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('toasts the modification and drives a reload for an updated document', () => {
    const listener = vi.fn()
    window.addEventListener('fg:document_content_changed', listener)
    const wrapper = mount(Harness, { global: { plugins: [i18n] } })

    // Server includes the human title in the updated payload — prefer it.
    MockEventSource.instance?.emit('document_explorer_refresh', {
      project: 'test',
      doc_id: 'test.none.0002.0004-D',
      payload: {
        operation: 'updated',
        doc_id: 'test.none.0002.0004-D',
        title: '설계 문서',
        revision_no: 1,
      },
    })

    // The modification notification fires on the SSE event itself, before (and
    // independently of) any open viewer's reload. (group 0035 R0001/NR0003)
    expect(showToast).toHaveBeenCalledTimes(1)
    expect(showToast).toHaveBeenCalledWith('Document updated: 설계 문서', 'info')

    // It still asks the open viewer (if any) to reload the new revision.
    expect(listener).toHaveBeenCalledTimes(1)
    expect((listener.mock.calls[0][0] as CustomEvent).detail).toEqual({
      project: 'test',
      doc_id: 'test.none.0002.0004-D',
      revision_no: 1,
      refresh_key: 'test.none.0002.0004-D:1',
    })

    // A successful reload adds no second toast (no duplicate for the one user
    // who has the doc open), even if multiple viewers report completion.
    window.dispatchEvent(new CustomEvent('fg:document_content_refresh_completed', {
      detail: { refresh_key: 'test.none.0002.0004-D:1', success: true },
    }))
    window.dispatchEvent(new CustomEvent('fg:document_content_refresh_completed', {
      detail: { refresh_key: 'test.none.0002.0004-D:1', success: true },
    }))
    expect(showToast).toHaveBeenCalledTimes(1)

    wrapper.unmount()
    window.removeEventListener('fg:document_content_changed', listener)
  })

  it('toasts an update even when a different (or no) document is open', () => {
    // Core R0001 regression: no MdViewer matches the edited doc, so the old
    // design never produced a toast. The notification must fire regardless.
    const listener = vi.fn()
    window.addEventListener('fg:document_content_changed', listener)
    const wrapper = mount(Harness, { global: { plugins: [i18n] } })

    MockEventSource.instance?.emit('document_explorer_refresh', {
      project: 'test',
      payload: {
        operation: 'updated',
        doc_id: 'test.none.0002.0004-D',
        revision_no: 3,
      },
    })

    // No completion event is ever dispatched (no matching viewer), yet:
    expect(showToast).toHaveBeenCalledTimes(1)
    // Falls back to the doc id when no title is present.
    expect(showToast).toHaveBeenCalledWith('Document updated: test.none.0002.0004-D', 'info')

    wrapper.unmount()
    window.removeEventListener('fg:document_content_changed', listener)
  })

  it('toasts document creation without dispatching a content reload', () => {
    const listener = vi.fn()
    window.addEventListener('fg:document_content_changed', listener)
    const wrapper = mount(Harness, { global: { plugins: [i18n] } })

    MockEventSource.instance?.emit('document_explorer_refresh', {
      project: 'test',
      payload: {
        operation: 'created',
        doc_id: 'test.none.0002.0005-D',
        title: '새 문서',
        revision_no: 0,
      },
    })

    // Registration toast fires for every created doc, regardless of whether a
    // separate next-action-candidate event is also emitted. (NR0003 #2)
    expect(showToast).toHaveBeenCalledTimes(1)
    expect(showToast).toHaveBeenCalledWith('Document registered: 새 문서', 'info')
    // Creation never triggers a viewer reload (there is no prior content).
    expect(listener).not.toHaveBeenCalled()

    wrapper.unmount()
    window.removeEventListener('fg:document_content_changed', listener)
  })

  it('surfaces a distinct error when the open viewer fails to reload', () => {
    const wrapper = mount(Harness, { global: { plugins: [i18n] } })

    MockEventSource.instance?.emit('document_explorer_refresh', {
      project: 'test',
      payload: {
        operation: 'updated',
        doc_id: 'test.none.0002.0004-D',
        revision_no: 2,
      },
    })
    // The modification notification already fired.
    expect(showToast).toHaveBeenCalledTimes(1)
    expect(showToast).toHaveBeenLastCalledWith('Document updated: test.none.0002.0004-D', 'info')

    // The open viewer could not reload the new revision: the edit notice and the
    // reload-failure state stay distinct. (NR0003 #5)
    window.dispatchEvent(new CustomEvent('fg:document_content_refresh_completed', {
      detail: { refresh_key: 'test.none.0002.0004-D:2', success: false },
    }))

    expect(showToast).toHaveBeenCalledTimes(2)
    expect(showToast).toHaveBeenLastCalledWith(
      'Failed to load the latest content of the updated document.',
      'error',
    )
    wrapper.unmount()
  })
})
