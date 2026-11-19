<template>
  <!-- 0016-CH (시안 basodrr4 B안) card row: [state icon] [Q# question / state · type · Flow · document] [time / actions].
       The three actions are line icons (open window, answer, ask an AI) in place of the ↗ ✎ 🤖 glyphs. -->
  <li v-for="row in rows" :key="row.seq" class="question-row notification-card" data-test="notification-question" :data-state="itemOf(row.seq) ? stateOf(itemOf(row.seq)!) : 'loading'">
    <template v-if="itemOf(row.seq)">
      <span class="notification-status" :class="STATE_TONES[stateOf(itemOf(row.seq)!)]" aria-hidden="true"><LineIcon :name="STATE_ICONS[stateOf(itemOf(row.seq)!)]" /></span>
      <div class="notification-main">
        <strong class="question-content"><span class="question-seq">Q{{ row.seq }}</span>{{ ' ' + (itemOf(row.seq)!.title || itemOf(row.seq)!.body) }}</strong>
        <span class="notification-meta"><span class="question-state" :class="`is-${stateOf(itemOf(row.seq)!)}`" data-test="question-state">{{ stateLabel(itemOf(row.seq)!) }}</span><span class="question-dot" aria-hidden="true">·</span><span :title="target.docId || ''">{{ typeLabel(row.type_code) }} · {{ flowName }} · {{ target.title }}</span></span>
      </div>
      <div class="notification-side">
        <time v-if="itemOf(row.seq)!.createdAt" :datetime="itemOf(row.seq)!.createdAt!">{{ relativeNotificationTime(itemOf(row.seq)!.createdAt!, locale) }}</time>
        <div class="question-actions notification-row-actions">
          <button v-for="(action, index) in actions" :key="action" type="button" class="notification-icon-btn" :class="{ 'is-on': index === 2 && expanded === itemOf(row.seq)!.id }" :title="t(n(action))" :aria-label="t(n(action))" :aria-expanded="index === 2 ? expanded === itemOf(row.seq)!.id : undefined" :disabled="index > 0 && !canAnswer(itemOf(row.seq)!, index === 2)" @click="activate(itemOf(row.seq)!, index)"><LineIcon :name="ACTION_ICONS[index]!" /></button>
        </div>
      </div>
      <div v-if="expanded === itemOf(row.seq)!.id" class="question-ai">
        <LineIcon name="bot" class="question-ai-icon" />
        <select v-model="picked" :aria-label="t(k('ai_provider'))" :disabled="!availability.ai_request.enabled || !providers.length">
          <option value="">{{ t(k('ai_provider_none')) }}</option>
          <option v-for="provider in providers" :key="provider.id" :value="provider.id">{{ provider.name }}</option>
        </select>
        <button type="button" class="question-ai-request" :disabled="!availability.ai_request.enabled || !validPick || requesting !== null" @click="requestAi(itemOf(row.seq)!.id, picked)">{{ t(k('ai_request')) }}</button>
        <p v-if="aiFailure?.itemId === itemOf(row.seq)!.id" class="question-ai-failure" role="alert">{{ t(k(aiFailure.key)) }}</p>
      </div>
    </template>
    <template v-else>
      <span class="notification-status" aria-hidden="true"><LineIcon :name="errorKey || items !== null ? 'alert' : 'loader'" /></span>
      <div class="notification-main">
        <strong class="notification-title">Q{{ row.seq }} · {{ row.title }}</strong>
        <p v-if="errorKey || items !== null" class="notification-reason" role="alert">{{ errorKey ? t(k(errorKey)) : t(n('load_error')) }}</p>
        <span v-else class="notification-meta">{{ t(n('loading')) }}</span>
      </div>
      <div class="notification-side">
        <button v-if="errorKey || items !== null" type="button" class="question-retry" @click="reload">{{ t(n('refresh')) }}</button>
      </div>
    </template>
  </li>
</template>
<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { documentContentRead, documentRead } from '@/features/document'
import { useInspectorResource } from '../../inspector/useInspectorResource'
import type { QaOpenItem } from '@/features/document'
import type { InspectorTarget } from '../../inspector/inspectorTarget'
import type { QnaItemView } from '../../inspector/features/qna/qnaModel'
import { useQna } from '../../inspector/features/qna/useQna'
import { openQnaWindow } from '../../inspector/features/qna/useQnaWindows'
import { relativeNotificationTime } from './notificationAiModel'
import LineIcon, { type LineIconName } from '@/components/ui/LineIcon.vue'
const props = defineProps<{ target: InspectorTarget; rows: QaOpenItem[]; flowName: string; refreshKey: number }>()
const body = useInspectorResource({ target: () => props.target, read: documentContentRead, events: ['document_explorer_refresh'] })
const metadata = useInspectorResource({ target: () => props.target, read: documentRead, events: ['document_explorer_refresh'] })
const documentTitle = computed(() => metadata.status.value === 'error' ? props.target.docId ?? '' : metadata.data.value?.title || props.target.docId || '')
const target = computed<InspectorTarget>(() => ({ ...props.target, title: documentTitle.value, cardDocuments: [{ slot: 'notification', docId: props.target.docId, docType: props.target.docType, author: null, title: documentTitle.value, status: null, docReviewStatus: null, hasBody: body.status.value !== 'error' && body.data.value?.missing === false && !!body.data.value.content.trim(), bodyState: body.data.value?.reason ?? null }] }))
const { t, te, locale } = useI18n()
const n = (key: string) => `gui.designer.top.notifications.${key}`
const k = (key: string) => `gui.inspector.qna.${key}`
const actions = ['question_all', 'question_reply', 'question_robot']
const ACTION_ICONS: readonly LineIconName[] = ['external', 'pencil', 'bot']
type RowState = 'waiting' | 'running' | 'done'
const STATE_ICONS: Record<RowState, LineIconName> = { waiting: 'help-circle', running: 'loader', done: 'check-circle' }
const STATE_TONES: Record<RowState, string> = { waiting: 'is-wait', running: 'is-run', done: 'is-ok' }
const { items, availability, providers, defaultProviderId, requesting, awaiting, aiFailure, requestAi, errorKey, reload } = useQna(() => target.value)
const expanded = ref<number | null>(null), picked = ref('')
const validPick = computed(() => providers.value.some(p => p.id === picked.value))
watch([providers, defaultProviderId], () => { if (!validPick.value) picked.value = defaultProviderId.value ?? providers.value[0]?.id ?? '' }, { immediate: true })
watch(() => props.refreshKey, () => { void body.reload(); void metadata.reload(); void reload() })
const itemOf = (seq: number) => items.value?.find(item => item.seq === seq)
function canAnswer(item: QnaItemView, ai: boolean) { const a = ai ? availability.value.ai_request : availability.value.answer; return item.answerable && a.visible && a.enabled }
function stateOf(item: QnaItemView): RowState { return item.state === 'answered' ? 'done' : awaiting.value.has(item.id) ? 'running' : 'waiting' }
function stateLabel(item: QnaItemView) { return t(n(`question_${stateOf(item)}`)) }
function typeLabel(code: string) { const key = `gui.document_window.types.${code?.toUpperCase()}`; return te(key) ? t(key) : t('gui.document_window.types.other') }
async function activate(item: QnaItemView, index: number) {
  if (index > 0 && !canAnswer(item, index === 2)) return
  if (index === 2) { expanded.value = expanded.value === item.id ? null : item.id; return }
  openQnaWindow(target.value, item.id)
  if (index === 1) { await nextTick(); document.querySelector<HTMLTextAreaElement>(`[data-test="qna-window"][data-item="${item.id}"] textarea`)?.focus() }
}
</script>
<style scoped>
/* The shared card look (status box, title / meta, side column) comes from DesignerNotifications; only what is
   particular to a question row is here. */
.question-row { min-width: 0; }
.question-content { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; overflow-wrap: anywhere; color: var(--fg2-text); font-size: var(--fg2-fs-body); font-weight: 650; line-height: 1.4; }
.question-seq { margin-right: 1px; color: var(--fg2-orange); }
.question-state { color: var(--fg2-text-2); font-weight: 600; }
.question-state.is-waiting { color: var(--fg2-orange); }
.question-state.is-running { color: var(--fg2-blue); }
.question-state.is-done { color: var(--fg2-cyan); }
.question-dot { margin: 0 5px; }
.question-actions .is-on { border-color: rgba(124, 192, 255, 0.45); color: var(--fg2-text); }
/* The ask-an-AI line opens under the row, across the card. */
.question-ai { display: flex; flex-wrap: wrap; align-items: center; grid-column: 1 / -1; gap: 8px; margin-top: 8px; padding: 8px 10px; border: 1px solid var(--fg2-line); border-radius: var(--fg2-inset-radius); background: var(--fg2-inset-bg); }
.question-ai-icon { color: var(--fg2-text-3); }
.question-ai select { flex: 1 1 140px; min-width: 0; max-width: 100%; height: 30px; padding: 0 8px; border: 1px solid var(--fg2-line-strong); border-radius: var(--fg2-control-radius-sm); background: var(--fg2-control-surface-flat); color: var(--fg2-text); font-size: var(--fg2-fs-sub); }
.question-ai-request, .question-retry { flex: none; height: 30px; padding: 0 12px; border: 1px solid var(--fg2-line-strong); border-radius: var(--fg2-control-radius-sm); background: var(--fg2-control-surface-flat); color: var(--fg2-text); font-size: var(--fg2-fs-sub); font-weight: 600; }
.question-ai-request:not(:disabled) { border-color: var(--fg2-blue); background: var(--fg2-blue); color: #04121f; }
.question-ai-failure { width: 100%; margin: 0; color: var(--fg2-status-danger-fg); font-size: var(--fg2-fs-sub); }
</style>
