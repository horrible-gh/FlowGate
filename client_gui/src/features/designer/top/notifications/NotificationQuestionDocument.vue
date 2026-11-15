<template>
  <li v-for="row in rows" :key="row.seq" class="question-row" data-test="notification-question">
    <template v-if="itemOf(row.seq)">
      <span data-test="question-state">● {{ stateLabel(itemOf(row.seq)!) }}</span>
      <time v-if="itemOf(row.seq)!.createdAt" :datetime="itemOf(row.seq)!.createdAt!">{{ relativeNotificationTime(itemOf(row.seq)!.createdAt!, locale) }}</time>
      <strong class="question-content">Q{{ row.seq }} {{ itemOf(row.seq)!.title || itemOf(row.seq)!.body }}</strong>
      <span :title="target.docId || ''">{{ typeLabel(row.type_code) }} · {{ flowName }} · {{ target.title }}</span>
      <div class="question-actions">
        <button v-for="(action, index) in actions" :key="action" :title="t(n(action))" :aria-label="t(n(action))" :disabled="index > 0 && !canAnswer(itemOf(row.seq)!, index === 2)" @click="activate(itemOf(row.seq)!, index)">{{ ['↗', '✎', '🤖'][index] }}</button>
      </div>
      <div v-if="expanded === itemOf(row.seq)!.id" class="question-ai">
        <select v-model="picked" :aria-label="t(k('ai_provider'))" :disabled="!availability.ai_request.enabled || !providers.length">
          <option value="">{{ t(k('ai_provider_none')) }}</option>
          <option v-for="provider in providers" :key="provider.id" :value="provider.id">{{ provider.name }}</option>
        </select>
        <button :disabled="!availability.ai_request.enabled || !validPick || requesting !== null" @click="requestAi(itemOf(row.seq)!.id, picked)">{{ t(k('ai_request')) }}</button>
        <p v-if="aiFailure?.itemId === itemOf(row.seq)!.id" role="alert">{{ t(k(aiFailure.key)) }}</p>
      </div>
    </template>
    <template v-else>
      <strong>Q{{ row.seq }} · {{ row.title }}</strong>
      <p v-if="errorKey || items !== null" role="alert">{{ errorKey ? t(k(errorKey)) : t(n('load_error')) }}</p>
      <button v-if="errorKey || items !== null" @click="reload">{{ t(n('refresh')) }}</button>
      <span v-else>{{ t(n('loading')) }}</span>
    </template>
  </li>
</template>
<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { documentContentRead, documentRead } from '@/features/document/api/document'
import { useInspectorResource } from '../../inspector/useInspectorResource'
import type { QaOpenItem } from '@/features/document/api/qa'
import type { InspectorTarget } from '../../inspector/inspectorTarget'
import type { QnaItemView } from '../../inspector/features/qna/qnaModel'
import { useQna } from '../../inspector/features/qna/useQna'
import { openQnaWindow } from '../../inspector/features/qna/useQnaWindows'
import { relativeNotificationTime } from './notificationAiModel'
const props = defineProps<{ target: InspectorTarget; rows: QaOpenItem[]; flowName: string; refreshKey: number }>()
const body = useInspectorResource({ target: () => props.target, read: documentContentRead, events: ['document_explorer_refresh'] })
const metadata = useInspectorResource({ target: () => props.target, read: documentRead, events: ['document_explorer_refresh'] })
const documentTitle = computed(() => metadata.status.value === 'error' ? props.target.docId ?? '' : metadata.data.value?.title || props.target.docId || '')
const target = computed<InspectorTarget>(() => ({ ...props.target, title: documentTitle.value, cardDocuments: [{ slot: 'notification', docId: props.target.docId, docType: props.target.docType, author: null, title: documentTitle.value, status: null, docReviewStatus: null, hasBody: body.status.value !== 'error' && body.data.value?.missing === false && !!body.data.value.content.trim(), bodyState: body.data.value?.reason ?? null }] }))
const { t, te, locale } = useI18n()
const n = (key: string) => `gui.designer.top.notifications.${key}`
const k = (key: string) => `gui.inspector.qna.${key}`
const actions = ['question_all', 'question_reply', 'question_robot']
const { items, availability, providers, defaultProviderId, requesting, awaiting, aiFailure, requestAi, errorKey, reload } = useQna(() => target.value)
const expanded = ref<number | null>(null), picked = ref('')
const validPick = computed(() => providers.value.some(p => p.id === picked.value))
watch([providers, defaultProviderId], () => { if (!validPick.value) picked.value = defaultProviderId.value ?? providers.value[0]?.id ?? '' }, { immediate: true })
watch(() => props.refreshKey, () => { void body.reload(); void metadata.reload(); void reload() })
const itemOf = (seq: number) => items.value?.find(item => item.seq === seq)
function canAnswer(item: QnaItemView, ai: boolean) { const a = ai ? availability.value.ai_request : availability.value.answer; return item.answerable && a.visible && a.enabled }
function stateLabel(item: QnaItemView) { return t(n(item.state === 'answered' ? 'question_done' : awaiting.value.has(item.id) ? 'question_running' : 'question_waiting')) }
function typeLabel(code: string) { const key = `gui.document_window.types.${code?.toUpperCase()}`; return te(key) ? t(key) : t('gui.document_window.types.other') }
async function activate(item: QnaItemView, index: number) {
  if (index > 0 && !canAnswer(item, index === 2)) return
  if (index === 2) { expanded.value = expanded.value === item.id ? null : item.id; return }
  openQnaWindow(target.value, item.id)
  if (index === 1) { await nextTick(); document.querySelector<HTMLTextAreaElement>(`[data-test="qna-window"][data-item="${item.id}"] textarea`)?.focus() }
}
</script>
<style scoped>
.question-row { min-width: 0; }
.question-content { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; overflow-wrap: anywhere; }
.question-actions, .question-ai { display: flex; flex-wrap: wrap; gap: 6px; }
.question-ai { width: 100%; } select { min-width: 0; max-width: 100%; }
time { margin-left: auto; }
</style>
