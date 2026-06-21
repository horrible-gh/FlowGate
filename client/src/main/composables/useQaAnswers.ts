// Shared document-bound Q&A logic (group 0093 R0001 / T0004).
//
// The query/answer flow used to live only inside DocInfoPanel, so the "full view"
// dialog (QaHistoryDialog) was read-only — it could show questions/answers but not
// add one. Extracting the data + actions here lets DocInfoPanel own a single source
// of truth and hand the SAME qaItems ref and bound action functions to the dialog,
// so answering in either surface refetches once and both views stay in sync.
import { ref, type Ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { getRequest, postRequest } from '@shared/api'

export interface QaAnswer {
  body: string
  author_kind: string
}
export interface QaItem {
  id: number
  seq: number
  title: string | null
  body: string
  asker_kind: string
  answer_count?: number
  answers?: QaAnswer[]
}

export function useQaAnswers(docId: Ref<string>) {
  const { t } = useI18n()

  const qaItems = ref<QaItem[]>([])
  const qaLoading = ref(false)
  const qaError = ref('')
  const qaBusy = ref(false)

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

  async function submitQuestion(title: string, body: string): Promise<boolean> {
    if (!body.trim() || qaBusy.value) return false
    qaBusy.value = true
    try {
      await postRequest(`/api/v1/q/${encodeURIComponent(docId.value)}/questions`, {
        asker_kind: 'human',
        questions: [{ title: title.trim() || null, body: body.trim() }],
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

  async function submitAnswer(itemId: number, body: string): Promise<boolean> {
    if (!body.trim() || qaBusy.value) return false
    qaBusy.value = true
    try {
      await postRequest(`/api/v1/q/${encodeURIComponent(docId.value)}/items/${itemId}/answers`, {
        body: body.trim(),
        author_kind: 'human',
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

  // [Request AI answer] — hands the item to an AI worker; the answer lands later as
  // author_kind='ai' (group 0022 D0005 §3.2). Dispatch wiring is server-side.
  async function requestAiAnswer(itemId: number): Promise<boolean> {
    if (qaBusy.value) return false
    qaBusy.value = true
    try {
      await postRequest(`/api/v1/q/${encodeURIComponent(docId.value)}/items/${itemId}/answers/ai-request`, {})
      await fetchQa()
      return true
    } catch (e: any) {
      qaError.value = e?.response?.data?.error_message ?? t('main.doc_info_panel.qa_error')
      return false
    } finally {
      qaBusy.value = false
    }
  }

  return {
    qaItems,
    qaLoading,
    qaError,
    qaBusy,
    itemAnswered,
    fetchQa,
    submitQuestion,
    submitAnswer,
    requestAiAnswer,
  }
}
