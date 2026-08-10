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

  it('bridges answer SSE with the server remaining-item count', () => {
    const answered = vi.fn()
    const refresh = vi.fn()
    window.addEventListener('fg:q_answered', answered)
    window.addEventListener('fg:qa_refresh', refresh)
    const wrapper = mount(Harness, { global: { plugins: [i18n] } })

    MockEventSource.instance?.emit('qna_answer_registered', {
      project: 'test',
      doc_id: 'test.none.0002.0004-D',
      payload: {
        doc_id: 'test.none.0002.0004-D',
        unanswered_count: 1,
      },
    })

    expect((answered.mock.calls[0][0] as CustomEvent).detail).toMatchObject({
      doc_id: 'test.none.0002.0004-D',
      unanswered_count: 1,
    })
    expect(refresh).toHaveBeenCalledTimes(1)

    wrapper.unmount()
    window.removeEventListener('fg:q_answered', answered)
    window.removeEventListener('fg:qa_refresh', refresh)
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

// 0351 T2 / P0003 시나리오 6. A conversation turn is pushed with its BODY, so the open
// chat appends one bubble. Routing it through the explorer refresh — which is what used
// to carry chat updates — made every line of conversation re-read the whole document.
describe('useFlowGateSse conversation turn bridge', () => {
  it('re-broadcasts an appended turn as a doc-scoped window event', () => {
    const wrapper = mount(Harness, { global: { plugins: [i18n] } })
    const seen: CustomEvent[] = []
    const onTurn = (e: Event) => seen.push(e as CustomEvent)
    window.addEventListener('fg:conversation_turn', onTurn)

    const turn = {
      seq: 14, speaker: 'ai', participant_key: 'provider:cx_opus',
      display_name: 'Claude Opus 5', locale: null, body: 'reply',
      based_on_seq: 13, stale_since_seq: null,
      source_run_id: 'run_1', created_at: '2026-07-29T10:00:00+09:00',
    }
    MockEventSource.instance?.emit('conversation_turn_appended', {
      project: 'flowgate',
      group_id: 'flowgate.default.0351',
      doc_id: 'flowgate.default.0351.0002-CH',
      payload: {
        doc_id: 'flowgate.default.0351.0002-CH',
        head_seq: 14,
        turn,
        participant: { participant_key: 'provider:cx_opus', kind: 'ai', last_read_seq: 13 },
      },
    })

    expect(seen).toHaveLength(1)
    expect(seen[0].detail.doc_id).toBe('flowgate.default.0351.0002-CH')
    expect(seen[0].detail.turn).toEqual(turn)
    expect(seen[0].detail.head_seq).toBe(14)
    expect(seen[0].detail.participant.participant_key).toBe('provider:cx_opus')
    // A chat message is not an explorer change: no toast, no tree notification.
    expect(showToast).not.toHaveBeenCalled()

    window.removeEventListener('fg:conversation_turn', onTurn)
    wrapper.unmount()
  })

  it('survives a malformed payload without breaking the stream', () => {
    const wrapper = mount(Harness, { global: { plugins: [i18n] } })
    const seen: Event[] = []
    const onTurn = (e: Event) => seen.push(e)
    window.addEventListener('fg:conversation_turn', onTurn)

    MockEventSource.instance?.listeners.get('conversation_turn_appended')?.(
      { data: '{ not json' } as MessageEvent,
    )
    expect(seen).toHaveLength(0)

    // The stream keeps working afterwards.
    MockEventSource.instance?.emit('conversation_turn_appended', {
      payload: { doc_id: 'flowgate.default.0351.0002-CH', head_seq: 1, turn: { seq: 1 } },
    })
    expect(seen).toHaveLength(1)

    window.removeEventListener('fg:conversation_turn', onTurn)
    wrapper.unmount()
  })
})
