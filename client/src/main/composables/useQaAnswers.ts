// Shared document-bound Q&A logic (group 0093 R0001 / T0004).
//
// The query/answer flow used to live only inside DocInfoPanel, so the "full view"
// dialog (QaHistoryDialog) was read-only — it could show questions/answers but not
// add one. Extracting the data + actions here lets DocInfoPanel own a single source
// of truth and hand the SAME qaItems ref and bound action functions to the dialog,
// so answering in either surface refetches once and both views stay in sync.
import { onBeforeUnmount, onMounted, ref, type Ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { getRequest, postRequest } from '@shared/api'

// group 0243 R0001: a query may carry reference options the answerer can click instead of
// writing prose. ids are server-assigned and unique within their item; the array order is
// the display order.
export interface QaOption {
  id: string
  label: string
}
export interface QaAnswer {
  body: string
  author_kind: string
  selected_options?: string[]
}
export interface QaItem {
  id: number
  seq: number
  title: string | null
  body: string
  asker_kind: string
  options?: QaOption[]
  answer_count?: number
  answers?: QaAnswer[]
}

export function useQaAnswers(docId: Ref<string>) {
  const { t } = useI18n()

  const qaItems = ref<QaItem[]>([])
  const qaLoading = ref(false)
  const qaError = ref('')
  const qaBusy = ref(false)
  // In-flight [Request AI answer] run (0248 B0001). qaBusy only covers the POST itself;
  // these outlive it — the run keeps going after the request returns, and the item stays
  // marked as being answered until its ai_invoke_finished event arrives.
  const aiRunId = ref<string | null>(null)
  const aiRunItemId = ref<number | null>(null)

  function itemAnswered(item: QaItem): boolean {
    return (item.answer_count ?? item.answers?.length ?? 0) > 0
  }

  async function fetchQa() {
    if (!docId.value) return
    qaLoading.value = true
    qaError.value = ''
    try {
      const res = await getRequest<any>(`/api/v1/q/${encodeURIComponent(docId.value)}`)
      qaItems.value = (res.data as any)?.qa?.items ?? []
    } catch (e: any) {
      qaError.value = e?.response?.data?.error_message ?? t('main.doc_info_panel.qa_error')
    } finally {
      qaLoading.value = false
    }
  }

  // `options` are plain labels — the server assigns each an id (L0008 §2.3). Blank entries
  // are dropped here so a half-filled option row in the compose form is simply ignored
  // rather than rejected as an empty label.
  async function submitQuestion(title: string, body: string, options: string[] = []): Promise<boolean> {
    if (!body.trim() || qaBusy.value) return false
    qaBusy.value = true
    try {
      await postRequest(`/api/v1/q/${encodeURIComponent(docId.value)}/questions`, {
        asker_kind: 'human',
        questions: [{
          title: title.trim() || null,
          body: body.trim(),
          options: options.map((o) => o.trim()).filter(Boolean),
        }],
      })
      await fetchQa()
      return true
    } catch (e: any) {
      qaError.value = e?.response?.data?.error_message ?? t('main.doc_info_panel.qa_error')
      return false
    } finally {
      qaBusy.value = false
    }
  }

  // An answer picks an option, writes prose, or does both — so a blank body is valid as long
  // as something was picked (the server then fills the body with the chosen label, L0008 §2.4).
  async function submitAnswer(
    itemId: number,
    body: string,
    selectedOptionIds: string[] = [],
  ): Promise<boolean> {
    if ((!body.trim() && selectedOptionIds.length === 0) || qaBusy.value) return false
    qaBusy.value = true
    try {
      await postRequest(`/api/v1/q/${encodeURIComponent(docId.value)}/items/${itemId}/answers`, {
        body: body.trim(),
        author_kind: 'human',
        selected_option_ids: selectedOptionIds,
      })
      await fetchQa()
      return true
    } catch (e: any) {
      qaError.value = e?.response?.data?.error_message ?? t('main.doc_info_panel.qa_error')
      return false
    } finally {
      qaBusy.value = false
    }
  }

  // [멘트 복사] — mint this item's worker mention so the user can paste it into their own
  // AI session (0248 B0001 rework). The document-bound Q&A had no hand-off at all: the only
  // button was [답변], so whoever asked the question was left answering it themselves. The
  // legacy Q-document flow has offered ment_copy alongside ai since the start (AnswerEditor);
  // this is that path. It is also the ONLY one that works with no provider configured.
  //
  // Returns the mention text, or null if the request failed (qaError carries the reason).
  // The caller hands this to copyToClipboardDeferred, so it must not be awaited behind
  // anything else — the click's transient activation has to survive to the clipboard write.
  async function fetchAnswerMention(itemId: number): Promise<string | null> {
    qaError.value = ''
    try {
      const res = await postRequest<any>(
        `/api/v1/q/${encodeURIComponent(docId.value)}/items/${itemId}/answers/ai-mention`, {},
      )
      return ((res as any)?.data?.mention as string) || null
    } catch (e: any) {
      qaError.value = e?.response?.data?.error_message ?? t('main.doc_info_panel.qa_error')
      return null
    }
  }

  // [Request AI answer] — starts a server-side AI run that answers this item; the answer
  // lands later as author_kind='ai' (group 0022 D0005 §3.2).
  //
  // 0248 B0001: the run is ASYNCHRONOUS. This used to refetch once, immediately, which was
  // guaranteed to be too early — the answer cannot exist yet when the POST returns. Hold the
  // run id instead and refetch when its ai_invoke_finished event arrives (onAiInvoke below).
  async function requestAiAnswer(itemId: number, providerId?: string): Promise<boolean> {
    if (qaBusy.value || aiRunItemId.value !== null) return false
    qaBusy.value = true
    qaError.value = ''
    try {
      const res = await postRequest<any>(
        `/api/v1/q/${encodeURIComponent(docId.value)}/items/${itemId}/answers/ai-request`,
        providerId ? { provider_id: providerId } : {},
      )
      const data = (res as any)?.data ?? {}
      aiRunId.value = data.run_id ?? null
      aiRunItemId.value = itemId
      return true
    } catch (e: any) {
      // Run admission failures carry the ai-invoke envelope (no_enabled_provider /
      // run_in_progress); error_message is set for both those and the plain _fail() shape.
      qaError.value = e?.response?.data?.error_message ?? t('main.doc_info_panel.qa_error')
      return false
    } finally {
      qaBusy.value = false
    }
  }

  // The run finished somewhere on the server — refetch so the new AI answer shows without
  // an F5. useFlowGateSse re-broadcasts every ai_invoke_* SSE frame as this window event.
  function onAiInvoke(e: Event) {
    const detail = (e as CustomEvent).detail
    if (!detail || detail.kind !== 'finished') return
    const payload = detail.payload ?? {}
    // Match on run_id: any group can have a run in flight, and only ours touches this Q&A.
    if (aiRunId.value === null || payload.run_id !== aiRunId.value) return
    const failed = payload.outcome !== 'complete'
    aiRunId.value = null
    aiRunItemId.value = null
    // Refetch even on a failed outcome: it costs one GET and keeps the list truthful if
    // the worker did land an answer the oracle happened to miss. Report the failure only
    // AFTER it settles — fetchQa clears qaError on entry, so setting it first would wipe
    // the message. A real fetch error wins: it is the more immediate problem.
    void fetchQa().then(() => {
      if (failed && !qaError.value) qaError.value = t('main.doc_info_panel.qa_answer_ai_failed')
    })
  }

  onMounted(() => window.addEventListener('fg:ai_invoke', onAiInvoke))
  onBeforeUnmount(() => window.removeEventListener('fg:ai_invoke', onAiInvoke))

  return {
    qaItems,
    qaLoading,
    qaError,
    qaBusy,
    aiRunId,
    aiRunItemId,
    itemAnswered,
    fetchQa,
    submitQuestion,
    submitAnswer,
    fetchAnswerMention,
    requestAiAnswer,
  }
}
