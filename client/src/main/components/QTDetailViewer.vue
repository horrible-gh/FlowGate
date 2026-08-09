<template>
  <div class="qt-detail-viewer">
    <div v-if="loading" style="padding:32px; text-align:center; opacity:.6; font-size:.8rem;">{{ t('common.loading') }}</div>
    <div v-else-if="fetchError" style="padding:16px; color:var(--danger); font-size:.8rem;">{{ fetchError }}</div>

    <template v-else-if="q">
      <!-- Q header meta -->
      <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin-bottom:4px;">
        <span class="q-state-badge" :class="q.status">
          <AppIcon :name="q.status === 'done' ? 'check-circle' : 'clock'" />
          {{ q.status === 'done' ? t('main.qt_detail_viewer.answered') : t('main.qt_detail_viewer.in_progress') }}
        </span>
        <span style="font-size:.75rem; font-weight:700; color:var(--text-m);">{{ q.q_id }}</span>
        <span v-if="q.related_doc" style="font-size:.75rem; color:var(--text-m);">← {{ q.related_doc }}</span>
      </div>

      <!-- Accordion card list -->
      <div style="margin-top:14px;">
        <div
          v-for="item in q.items"
          :key="item.id"
          class="acc-card"
          :class="{ 'done-card': item.answers.length > 0, open: openStates[item.id] }"
        >
          <!-- Header -->
          <div class="acc-hd" @click="toggleCard(item.id)">
            <div class="acc-hd-left">
              <span class="acc-num">{{ item.seq }}</span>
              <span class="acc-hd-title">{{ t('main.qt_detail_viewer.question_title', { seq: item.seq, title: firstLine(item.body) }) }}</span>
            </div>
            <div class="acc-hd-right">
              <span class="q-state-badge" :class="item.answers.length > 0 ? 'done' : 'pending'" style="font-size:.68rem; padding:2px 8px;">
                <AppIcon :name="item.answers.length > 0 ? 'check-circle' : 'clock'" />
                {{ item.answers.length > 0 ? t('main.qt_detail_viewer.answered') : t('main.qt_detail_viewer.in_progress') }}
              </span>
              <AppIcon name="caret-down" class="acc-toggle-icon" />
            </div>
          </div>

          <!-- Preview (answered + collapsed) -->
          <div class="acc-preview">{{ item.answers.length > 0 ? firstLine(item.answers[item.answers.length - 1].body) : '' }}</div>

          <!-- Body -->
          <div
            v-show="visibleStates[item.id]"
            class="acc-body"
            :aria-hidden="!openStates[item.id]"
            :inert="!openStates[item.id]"
            :style="bodyStyles[item.id]"
          >
            <div class="acc-body-inner" :ref="(el) => setBodyInnerRef(item.id, el)">
              <div class="q-section-label"><AppIcon name="user" /> {{ t('main.qt_detail_viewer.question_body') }}</div>
              <div class="q-body-readonly">{{ item.body }}</div>
              <hr class="q-divider" />

              <!-- Existing answer tree -->
              <template v-if="item.answers.length > 0">
                <div class="q-section-label"><AppIcon name="check-circle" style="color:var(--success,#16a34a);" /> {{ t('main.qt_detail_viewer.answers') }}</div>
                <div v-for="ans in item.answers" :key="ans.id" class="q-answer-entry">
                  <div class="q-answer-entry-meta">
                    <AppIcon name="user-circle" style="color:var(--primary);" />
                    <span>{{ ans.answered_by }}</span>
                    <span v-if="ans.answered_at">· {{ ans.answered_at.slice(0, 10) }}</span>
                  </div>
                  <div class="q-answer-rendered" v-html="renderMd(ans.body)"></div>
                </div>
                <hr class="q-divider" />
              </template>

              <!-- Answer input -->
              <template v-if="!readOnly">
                <div class="q-section-label"><AppIcon name="user-circle" /> {{ t('main.qt_detail_viewer.answer_input') }}</div>
                <textarea
                  class="q-answer-textarea"
                  :placeholder="t('main.qt_detail_viewer.answer_placeholder', { seq: item.seq })"
                  v-model="answerDrafts[item.id]"
                ></textarea>
                <div class="md-format-hint">
                  <AppIcon name="markdown-logo" style="margin-right:4px;" />
                  <strong>{{ t('main.qt_detail_viewer.markdown_hints') }}</strong>:
                  <code>**bold**</code>&nbsp;
                  <code>*italic*</code>&nbsp;
                  <code>`code`</code>&nbsp;
                  <code>- list</code>&nbsp;
                  <code>```code block```</code>
                </div>
                <button
                  class="btn-save-ans"
                  :disabled="savingStates[item.id]"
                  @click="saveAnswer(item)"
                >
                  <AppIcon :name="savingStates[item.id] ? 'spinner' : 'floppy-disk'" :spin="savingStates[item.id]" />
                  {{ savingStates[item.id] ? t('main.qt_detail_viewer.saving') : t('main.qt_detail_viewer.save_answer') }}
                </button>
              </template>
            </div>
          </div>
        </div>
      </div>

      <!-- Follow-up question notice -->
      <div class="q-followup-notice">
        <AppIcon name="info" />
        <span>{{ t('main.qt_detail_viewer.followup_notice') }}</span>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import AppIcon from '@shared/AppIcon.vue'
import { nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { marked } from 'marked'
import { getRequest, postRequest } from '@shared/api'
import { useToast } from './common/useToast'
import { qApiPath } from '@shared/utils/docIdFormatter'

const props = defineProps<{ qId: string; readOnly?: boolean }>()
const emit = defineEmits<{
  'status-changed': [payload: { qId: string; status: string; done: boolean }]
}>()

const { showToast } = useToast()
const { t } = useI18n()

interface AnswerItem {
  id: number
  body: string
  answered_by: string
  answered_at?: string | null
}

interface QuestionItem {
  id: number
  seq: number
  body: string
  answer_count: number
  answers: AnswerItem[]
}

interface QDetail {
  q_id: string
  project_id?: string | null
  title: string
  status: string
  created_by?: string | null
  pm_id?: string | null
  related_doc?: string | null
  items: QuestionItem[]
}

const q = ref<QDetail | null>(null)
const loading = ref(false)
const fetchError = ref('')
const openStates = reactive<Record<number, boolean>>({})
const visibleStates = reactive<Record<number, boolean>>({})
const answerDrafts = reactive<Record<number, string>>({})
const savingStates = reactive<Record<number, boolean>>({})
const bodyStyles = reactive<Record<number, Record<string, string>>>({})
const bodyInnerEls = new Map<number, HTMLElement>()
const closeTimers = new Map<number, number>()
const OPEN_DURATION_MS = 1000
const CLOSE_DURATION_MS = 1000
const REDUCED_DURATION_MS = 200

function getEffectiveDuration(ms: number): number {
  return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    ? REDUCED_DURATION_MS
    : ms
}

function makeTransition(ms: number): string {
  return `height ${ms}ms ease, opacity ${ms}ms ease`
}

function firstLine(text: string): string {
  return (text || '').split('\n')[0].trim()
}

function renderMd(text: string): string {
  return marked.parse(text || '') as string
}

function getBodyHeight(itemId: number): number {
  const el = bodyInnerEls.get(itemId)
  return el ? Math.ceil(el.scrollHeight) : 0
}

function setBodyInnerRef(itemId: number, el: unknown) {
  if (el instanceof HTMLElement) {
    bodyInnerEls.set(itemId, el)
    return
  }

  bodyInnerEls.delete(itemId)
}

function clearCloseTimer(itemId: number) {
  const timer = closeTimers.get(itemId)
  if (timer !== undefined) {
    window.clearTimeout(timer)
    closeTimers.delete(itemId)
  }
}

async function openCard(itemId: number) {
  clearCloseTimer(itemId)
  visibleStates[itemId] = true
  openStates[itemId] = true
  bodyStyles[itemId] = {
    height: '0px',
    opacity: '0',
    transition: 'none',
  }

  await nextTick()
  const targetHeight = getBodyHeight(itemId)
  const duration = getEffectiveDuration(OPEN_DURATION_MS)
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      bodyStyles[itemId] = {
        height: `${targetHeight}px`,
        opacity: '1',
        transition: makeTransition(duration),
      }
    })
  })

  window.setTimeout(() => {
    if (!openStates[itemId]) return
    bodyStyles[itemId] = {
      height: 'auto',
      opacity: '1',
      transition: 'none',
    }
  }, duration + 50)
}

function closeCard(itemId: number) {
  clearCloseTimer(itemId)
  const currentHeight = getBodyHeight(itemId)
  openStates[itemId] = false
  visibleStates[itemId] = true
  bodyStyles[itemId] = {
    height: `${currentHeight}px`,
    opacity: '1',
    transition: 'none',
  }

  const duration = getEffectiveDuration(CLOSE_DURATION_MS)
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      bodyStyles[itemId] = {
        height: '0px',
        opacity: '0',
        transition: makeTransition(duration),
      }
    })
  })

  const timer = window.setTimeout(() => {
    if (openStates[itemId]) return
    visibleStates[itemId] = false
    bodyStyles[itemId] = {}
    closeTimers.delete(itemId)
  }, duration + 50)
  closeTimers.set(itemId, timer)
}

function closeAllCards(exceptItemId?: number) {
  Object.keys(openStates).forEach((key) => {
    const itemId = Number(key)
    if (itemId !== exceptItemId && (openStates[itemId] || visibleStates[itemId])) {
      closeCard(itemId)
    }
  })
}

function toggleCard(itemId: number) {
  const shouldOpen = !openStates[itemId]
  if (!shouldOpen) {
    closeCard(itemId)
    return
  }

  const hasOtherOpen = Object.keys(openStates).some((key) => Number(key) !== itemId && openStates[Number(key)])
  closeAllCards(itemId)
  if (hasOtherOpen) {
    window.setTimeout(() => openCard(itemId), getEffectiveDuration(CLOSE_DURATION_MS) + 50)
    return
  }
  openCard(itemId)
}

function initOpenStates(items: QuestionItem[]) {
  const itemIds = new Set(items.map((item) => item.id))
  Object.keys(openStates).forEach((key) => {
    const itemId = Number(key)
    if (!itemIds.has(itemId)) {
      clearCloseTimer(itemId)
      delete openStates[itemId]
      delete visibleStates[itemId]
      delete bodyStyles[itemId]
    }
  })

  const hasOpenCard = items.some((item) => openStates[item.id])
  const initialOpenItem = items.find((item) => item.answers.length === 0) ?? items[0]

  items.forEach((item) => {
    if (openStates[item.id] === undefined) {
      openStates[item.id] = false
    }
    if (visibleStates[item.id] === undefined) {
      visibleStates[item.id] = false
    }
    if (answerDrafts[item.id] === undefined) {
      answerDrafts[item.id] = ''
    }
  })

  if (!hasOpenCard && initialOpenItem) {
    openStates[initialOpenItem.id] = true
    visibleStates[initialOpenItem.id] = true
    bodyStyles[initialOpenItem.id] = {
      height: 'auto',
      opacity: '1',
    }
  }
}

function publishQStatus(detail: QDetail) {
  const status = detail.status || 'pending'
  const payload = {
    qId: detail.q_id || props.qId,
    status,
    done: status === 'done',
  }
  emit('status-changed', payload)
  window.dispatchEvent(new CustomEvent('fg:q_status_changed', { detail: payload }))
}

async function fetchQ(qId: string) {
  loading.value = true
  fetchError.value = ''
  q.value = null
  try {
    const res = await getRequest<any>(`/api/v1/q/${qApiPath(qId)}`)
    const data = (res.data as any)?.q ?? res.data
    q.value = data
    initOpenStates(data.items ?? [])
    publishQStatus(data)
  } catch (e: any) {
    fetchError.value = e?.response?.data?.error_message ?? t('main.qt_detail_viewer.load_failed')
  } finally {
    loading.value = false
  }
}

async function saveAnswer(item: QuestionItem) {
  const draft = (answerDrafts[item.id] ?? '').trim()
  if (!draft) {
    showToast(t('main.qt_detail_viewer.answer_empty'), 'warning')
    return
  }
  savingStates[item.id] = true
  try {
    await postRequest<any>(
      `/api/v1/q/${qApiPath(props.qId)}/items/${item.id}/answers`,
      { body: draft },
    )
    showToast(t('main.qt_detail_viewer.answer_saved', { seq: item.seq }), 'success')
    answerDrafts[item.id] = ''
    await fetchQ(props.qId)
    // After saving: collapse current card, auto-expand next pending card
    closeAllCards()
    const items = q.value?.items ?? []
    const nextPending = items.find((it) => it.seq > item.seq && it.answers.length === 0)
    if (nextPending) openCard(nextPending.id)
  } catch (e: any) {
    const msg = e?.response?.data?.error_message ?? t('main.qt_detail_viewer.save_failed')
    showToast(msg, 'danger')
  } finally {
    savingStates[item.id] = false
  }
}

// Live refresh: same gap as DocInfoPanel — a Q registered on the doc this viewer shows
// arrives via SSE but the viewer only fetched on mount / qId switch, so it needed F5
// (0059 B0001). Refetch when the SSE-bridged window event targets this q-doc.
function _onQRegistered(e: Event) {
  const detail = (e as CustomEvent).detail as { doc_id?: string } | undefined
  if (detail?.doc_id && detail.doc_id === props.qId) fetchQ(props.qId)
}

onMounted(() => {
  fetchQ(props.qId)
  window.addEventListener('fg:q_registered', _onQRegistered)
})
watch(() => props.qId, fetchQ)
onBeforeUnmount(() => {
  window.removeEventListener('fg:q_registered', _onQRegistered)
  closeTimers.forEach((timer) => window.clearTimeout(timer))
  closeTimers.clear()
})

defineExpose({ q, loading })
</script>
