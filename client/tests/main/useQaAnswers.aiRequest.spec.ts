import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, ref } from 'vue'
import { useQaAnswers } from '@main/composables/useQaAnswers'

const { getRequest, postRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
}))
vi.mock('@shared/api', () => ({ getRequest, postRequest }))
vi.mock('vue-i18n', () => ({ useI18n: () => ({ t: (k: string) => k }) }))

// group 0248 B0001 / NR0003 regression.
//
// [AI에게 답변 요청] was a silent no-op end to end. On this side the composable threw the
// POST response away and refetched Q&A ONCE, immediately — which could never show an answer,
// because the server run that writes it had only just started. The button therefore looked
// like it worked and nothing ever appeared.
//
// The contract now: hold the run_id the server returns, and refetch when THAT run reports
// finished over SSE (useFlowGateSse re-broadcasts every ai_invoke_* frame as fg:ai_invoke).

const DOC = 'p.none.0248.0001-B'
const AI_REQUEST_URL = `/api/v1/q/${encodeURIComponent(DOC)}/items/7/answers/ai-request`

// fg:ai_invoke is a global window event, so a host left mounted by an earlier test would
// keep answering it and double-count the refetches here. Track and unmount every host.
const hosts: VueWrapper<any>[] = []

function mountHost() {
  let api: ReturnType<typeof useQaAnswers> | undefined
  const Host = defineComponent({
    setup() {
      api = useQaAnswers(ref(DOC))
      return () => null
    },
  })
  const wrapper = mount(Host)
  hosts.push(wrapper)
  return { wrapper, api: api! }
}

function finishRun(runId: string, outcome = 'complete') {
  window.dispatchEvent(new CustomEvent('fg:ai_invoke', {
    detail: { kind: 'finished', payload: { run_id: runId, outcome } },
  }))
}

beforeEach(() => {
  getRequest.mockReset()
  postRequest.mockReset()
  getRequest.mockResolvedValue({ data: { qa: { items: [] } } })
  postRequest.mockResolvedValue({ data: { ok: true, run_id: 'run-42', status: 'running' } })
})

afterEach(() => {
  while (hosts.length) hosts.pop()!.unmount()
})

describe('useQaAnswers — [AI에게 답변 요청] dispatch (0248 B0001)', () => {
  it('POSTs ai-request and tracks the returned run instead of refetching immediately', async () => {
    const { api } = mountHost()

    const ok = await api.requestAiAnswer(7)
    await flushPromises()

    expect(ok).toBe(true)
    expect(postRequest).toHaveBeenCalledWith(AI_REQUEST_URL, {})
    // The run handle must be kept — discarding it was the defect.
    expect(api.aiRunId.value).toBe('run-42')
    expect(api.aiRunItemId.value).toBe(7)
    // ...and the pointless too-early refetch must be gone.
    expect(getRequest).not.toHaveBeenCalled()
  })

  it('refetches Q&A when its own run finishes, then clears the running state', async () => {
    const { api } = mountHost()
    await api.requestAiAnswer(7)
    await flushPromises()

    finishRun('run-42')
    await flushPromises()

    expect(getRequest).toHaveBeenCalledTimes(1)   // the answer is visible without an F5
    expect(api.aiRunId.value).toBeNull()
    expect(api.aiRunItemId.value).toBeNull()
    expect(api.qaError.value).toBe('')
  })

  it('ignores a finished event from an unrelated run', async () => {
    const { api } = mountHost()
    await api.requestAiAnswer(7)
    await flushPromises()

    finishRun('some-other-run')
    await flushPromises()

    expect(getRequest).not.toHaveBeenCalled()
    expect(api.aiRunItemId.value).toBe(7)         // still waiting on our own run
  })

  it('surfaces a message when the run finishes without registering an answer', async () => {
    const { api } = mountHost()
    await api.requestAiAnswer(7)
    await flushPromises()

    finishRun('run-42', 'none')
    await flushPromises()

    expect(api.qaError.value).toBe('main.doc_info_panel.qa_answer_ai_failed')
    expect(api.aiRunItemId.value).toBeNull()      // not stuck "running" forever
  })

  it('surfaces the server error when the run cannot start (no provider / run in progress)', async () => {
    postRequest.mockRejectedValueOnce({
      response: { data: { ok: false, code: 'no_enabled_provider', error_message: '사용 가능한 AI 공급자가 없습니다.' } },
    })
    const { api } = mountHost()

    const ok = await api.requestAiAnswer(7)
    await flushPromises()

    expect(ok).toBe(false)
    expect(api.qaError.value).toBe('사용 가능한 AI 공급자가 없습니다.')
    expect(api.aiRunItemId.value).toBeNull()      // nothing is running
  })

  it('does not start a second run while one is in flight', async () => {
    const { api } = mountHost()
    await api.requestAiAnswer(7)
    await flushPromises()

    const second = await api.requestAiAnswer(8)

    expect(second).toBe(false)
    expect(postRequest).toHaveBeenCalledTimes(1)
  })

  it('stops listening once unmounted', async () => {
    const { wrapper, api } = mountHost()
    await api.requestAiAnswer(7)
    await flushPromises()

    wrapper.unmount()
    finishRun('run-42')
    await flushPromises()

    expect(getRequest).not.toHaveBeenCalled()     // no refetch from a dead component
  })
})

// 0248 B0001 rework — [멘트 복사].
//
// Reviewer: "멘트복사도 없고 AI호출도 없고 사용자가 질문하고 사용자가 답하는 자문자답?"
// The document-bound Q&A had no copy path at all, so a project with no AI provider left the
// asker writing the answer themselves. This is the fetch half; DocInfoPanel owns the write.
describe('useQaAnswers — fetchAnswerMention (0248 B0001 rework)', () => {
  const MENTION_URL = `/api/v1/q/${encodeURIComponent(DOC)}/items/7/answers/ai-mention`

  it('returns the mention text for the item', async () => {
    postRequest.mockResolvedValue({ data: { ok: true, mention: '[작업] 질의에 답하십시오' } })
    const { api } = mountHost()

    const mention = await api.fetchAnswerMention(7)

    expect(postRequest).toHaveBeenCalledWith(MENTION_URL, {})
    expect(mention).toBe('[작업] 질의에 답하십시오')
  })

  it('does not start a run (the copy path must work with no provider configured)', async () => {
    postRequest.mockResolvedValue({ data: { ok: true, mention: 'm' } })
    const { api } = mountHost()

    await api.fetchAnswerMention(7)

    expect(postRequest).toHaveBeenCalledTimes(1)
    expect(postRequest).not.toHaveBeenCalledWith(AI_REQUEST_URL, expect.anything())
  })

  it('reports the server error and returns null so the caller can skip the copy', async () => {
    postRequest.mockRejectedValue({ response: { data: { error_message: '토큰 발급 실패' } } })
    const { api } = mountHost()

    const mention = await api.fetchAnswerMention(7)

    expect(mention).toBeNull()
    expect(api.qaError.value).toBe('토큰 발급 실패')
  })
})
