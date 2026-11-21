<template>
  <!-- batch A — v7 ↗ (insp.js openQA / openWin): one modeless window per opened question — the
       full question, every answer and the full answer form; several may be open at once.
       the only place a question is answered, after its context above the form.
       the windows of ONE document, drawn apart from the Q&A tab: `target` is the target the windows
       were opened for (not the selection), so the questions read, the answers sent and the AI asked are always that
       document's, and a selection change, a tab change or a click elsewhere never closes them. A window closes only by
       its own × (`close`); Esc does not close it. -->
  <ModelessShell
    v-for="(win, index) in windows"
    :key="win.id"
    :open="true"
    :initial-x="win.x"
    :initial-y="win.y"
    :initial-width="520"
    :initial-height="520"
    :min-width="320"
    :min-height="200"
    :close-on-escape="false"
    @close="emit('close', win.itemId)"
  >
    <template #header>
      <span class="inspector-qna-window-heading">
        <span class="inspector-qna-window-title">{{ t(k('window.title')) }}</span>
        <span v-if="windowItem(win.itemId)" class="inspector-qna-window-sub" :title="windowSub(windowItem(win.itemId)!)" data-test="qna-window-sub">{{ windowSub(windowItem(win.itemId)!) }}</span>
        <span
          v-if="readOnlyReason"
          class="inspector-qna-window-mode"
          data-mode="view"
          :title="t(k(readOnlyReason))"
          data-test="qna-window-mode"
        >{{ t(k('window.view')) }}</span>
      </span>
    </template>
    <div class="inspector-qna-window" data-test="qna-window" :data-item="win.itemId" :data-index="index" :data-state="windowItem(win.itemId)?.state ?? ''">
      <template v-if="windowItem(win.itemId)">
        <div class="inspector-qna-window-head">
          <InspectorStatusChip :label="t(k(stateKey(windowItem(win.itemId)!.state)))" :tone="toneOf(windowItem(win.itemId)!.state)" data-test="qna-window-state" />
          <span class="inspector-qna-sub" data-test="qna-window-asker">{{ t(k('asked_by'), { side: sideText(windowItem(win.itemId)!.asker) }) }}</span>
          <span v-if="windowItem(win.itemId)!.createdAt" class="inspector-qna-sub" data-test="qna-window-asked-at">{{ t(k('detail.asked_at'), { at: formatAt(windowItem(win.itemId)!.createdAt!) }) }}</span>
          <InspectorStatusChip v-if="awaiting.has(win.itemId)" :label="t(k('ai_waiting'))" tone="running" dot data-test="qna-window-ai-waiting" />
        </div>
        <span v-if="windowItem(win.itemId)!.docId" class="inspector-qna-sub inspector-qna-window-doc" data-test="qna-window-doc"><LineIcon name="file" class="inspector-qna-doc-icon" />{{ t(k('detail.doc'), { docId: windowItem(win.itemId)!.docId }) }}</span>
        <p v-if="windowItem(win.itemId)!.title" class="inspector-qna-window-q">{{ windowItem(win.itemId)!.title }}</p>
        <p class="inspector-qna-text inspector-qna-qbody" data-test="qna-window-body">{{ windowItem(win.itemId)!.body }}</p>
        <!-- the stored options stay readable where they cannot be picked. -->
        <span
          v-if="windowItem(win.itemId)!.options.length && !(windowItem(win.itemId)!.answerable && availability.answer.visible)"
          class="inspector-qna-sub"
          data-test="qna-window-options"
        >{{ t(k('options_list'), { labels: windowItem(win.itemId)!.options.map((option) => option.label).join(', ') }) }}</span>

        <ul v-if="windowItem(win.itemId)!.answers.length" class="inspector-qna-answers">
          <li v-for="answer in windowItem(win.itemId)!.answers" :key="answer.key" data-test="qna-window-answer" :data-side="answer.side.kind" :data-side-class="answer.side.sideClass">
            <span class="inspector-qna-sub" data-test="qna-window-answer-side">{{ t(k('answered_by'), { side: sideText(answer.side) }) }}</span>
            <span v-if="answer.detail.answeredAt" class="inspector-qna-sub" data-test="qna-window-answer-at">{{ t(k('detail.answered_at'), { at: formatAt(answer.detail.answeredAt) }) }}</span>
            <span v-if="answer.detail.runId" class="inspector-qna-sub" data-test="qna-window-answer-run">{{ t(k('detail.run_id'), { runId: answer.detail.runId }) }}</span>
            <span v-if="answer.picked.length" class="inspector-qna-sub">{{ t(k('picked'), { labels: answer.picked.join(', ') }) }}</span>
            <p class="inspector-qna-text">{{ answer.body }}</p>
          </li>
        </ul>

        <div v-if="windowItem(win.itemId)!.answerable && availability.answer.visible" class="inspector-qna-answer" data-test="qna-window-form">
          <!-- 0016-CH: the options are a stacked radio list (mockup option A); the rest of the window follows option B. -->
          <div v-if="windowItem(win.itemId)!.options.length" class="inspector-qna-options" role="group" :aria-label="t(k('options'))">
            <button
              v-for="option in windowItem(win.itemId)!.options"
              :key="option.id"
              type="button"
              class="inspector-qna-option"
              :class="{ 'is-on': pickedId(win.itemId) === option.id }"
              :aria-pressed="pickedId(win.itemId) === option.id"
              :disabled="!availability.answer.enabled"
              data-test="qna-window-option"
              :data-option="option.id"
              @click="pick(win.itemId, option.id)"
            ><span class="inspector-qna-radio" aria-hidden="true"></span>{{ option.label }}</button>
          </div>
          <Textarea
            class="inspector-qna-input"
            :model-value="bodyOf(win.itemId)"
            :rows="4"
            :placeholder="t(k('answer_placeholder'))"
            :disabled="!availability.answer.enabled"
            data-test="qna-window-body-input"
            @update:model-value="(value: string) => setBody(win.itemId, value)"
          />
          <!-- The form's last row: the ask-an-AI controls on the left, Answer on the right. -->
          <div class="inspector-qna-foot">
          <div v-if="availability.ai_request.visible" class="inspector-qna-ai" data-test="qna-window-ai">
            <LineIcon name="bot" class="inspector-qna-ai-icon" />
            <Select
              class="inspector-qna-ai-pick"
              :model-value="aiPickOf(win.itemId)"
              :options="providerOptions"
              :placeholder="providerPlaceholder"
              :disabled="!availability.ai_request.enabled || !providers.length"
              :aria-label="t(k('ai_provider'))"
              data-test="qna-window-ai-provider"
              @update:model-value="(value: string) => setAiPick(win.itemId, value)"
            />
            <Button
              variant="secondary"
              :disabled="!availability.ai_request.enabled || !aiPickOf(win.itemId)"
              :loading="requesting === win.itemId"
              :title="aiTitle"
              data-test="qna-window-ai-request"
              @click="requestAiFor(win.itemId)"
            >{{ t(k('ai_request')) }}</Button>
            <InspectorFailureIcon v-if="aiFailure && aiFailure.itemId === win.itemId" :name="t(k('ai_failed'))" :reason="t(k(aiFailure.key))" data-test="qna-window-ai-failure" />
          </div>
          <div class="inspector-qna-line">
            <Button
              variant="primary"
              :disabled="!availability.answer.enabled || !canSend(win.itemId)"
              :loading="busy === win.itemId"
              :title="answerTitle"
              data-test="qna-window-submit"
              @click="submit(win.itemId)"
            ><LineIcon name="send" />{{ t(k('answer')) }}</Button>
          </div>
          </div>
          <div v-if="failure && failure.itemId === win.itemId" data-test="qna-window-failure">
            <InspectorReason tone="danger" :text="t(k(failure.key))" />
          </div>
        </div>
        <p v-else-if="windowItem(win.itemId)!.answerable && bodyLine" class="inspector-qna-text inspector-qna-sub" data-test="qna-window-blocked">{{ t(k(bodyLine)) }}</p>
      </template>
      <InspectorPaneState v-else state="empty" :message="t(k('window.gone'))" />
    </div>
  </ModelessShell>
</template>

<script setup lang="ts">
// the ↗ windows of one document (moved here from QnaTab): the Frame's window host draws them for
// every document with an open window (QnaWindowHost), and a history Q&A (a document window's aside) draws its own.
// The questions, the answer form and the AI line read and write through their own useQna of `target`.
import { computed, reactive, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '../../../../../components/ui/Button.vue'
import LineIcon from '../../../../../components/ui/LineIcon.vue'
import ModelessShell from '../../../../../components/ui/ModelessShell.vue'
import Select, { type SelectOption } from '../../../../../components/ui/Select.vue'
import Textarea from '../../../../../components/ui/Textarea.vue'
import InspectorFailureIcon from '../../parts/InspectorFailureIcon.vue'
import InspectorPaneState from '../../parts/InspectorPaneState.vue'
import InspectorReason from '../../parts/InspectorReason.vue'
import InspectorStatusChip, { type InspectorChipTone } from '../../parts/InspectorStatusChip.vue'
import type { InspectorTarget } from '../../inspectorTarget'
import { qnaBodyLine, type QnaAvailability, type QnaItemState, type QnaItemView, type QnaSide } from './qnaModel'
import { useQna } from './useQna'
import type { QnaWindow } from './useQnaWindows'

const props = defineProps<{
  /** The target the windows were opened for (its document's questions). */
  target: InspectorTarget
  /** The open windows of that document, in opening order. */
  windows: readonly QnaWindow[]
  /** read only history — the questions and answers are read, nothing is answered. */
  history?: boolean
}>()

const emit = defineEmits<{ close: [itemId: number] }>()

const { t, locale } = useI18n()

const k = (key: string) => `gui.inspector.qna.${key}`

const {
  items,
  availability: liveAvailability,
  busy,
  failure,
  ready,
  answer,
  requesting,
  awaiting,
  aiFailure,
  requestAi,
  providers,
  defaultProviderId,
  providersStatus,
} = useQna(() => props.target, { history: () => props.history === true })

/** History: only reading is offered (answering and the AI line close in the model, the earlier implementation body context `history`). */
const HISTORY_CLOSED = { visible: false, enabled: false, reason: null } as const
const availability = computed<QnaAvailability>(() =>
  props.history ? { ...liveAvailability.value, ask: HISTORY_CLOSED } : liveAvailability.value,
)

/** the window's one line while the body cannot be checked, or null. */
const bodyLine = computed(() => qnaBodyLine(availability.value))

// --- The AI line (0056 F1) ------------------------------------------------------------------------

const providerOptions = computed<SelectOption[]>(() => providers.value.map((provider) => ({ value: provider.id, label: provider.name })))

/** The picker's empty wording: the list is on its way, or the project has no AI. */
const providerPlaceholder = computed(() => (providersStatus.value === 'loading' ? t(k('ai_provider_loading')) : t(k('ai_provider_none'))))

/** [AI 답변 요청]'s tooltip: why it is closed, else nothing beyond its label. */
const aiTitle = computed(() => {
  const reason = availability.value.ai_request.reason
  if (reason) return t(k(reason))
  return providers.value.length ? undefined : t(k('ai_reason.no_provider'))
})

// The AI picked per question (written by the picker only); the project's default until the user picks.
const aiPicks = reactive<Record<number, string>>({})

function aiPickOf(itemId: number): string {
  const picked = aiPicks[itemId]
  if (picked && providers.value.some((provider) => provider.id === picked)) return picked
  const fallback = defaultProviderId.value ?? providers.value[0]?.id ?? ''
  return providers.value.some((provider) => provider.id === fallback) ? fallback : ''
}

function setAiPick(itemId: number, providerId: string) {
  aiPicks[itemId] = providerId
}

async function requestAiFor(itemId: number) {
  const providerId = aiPickOf(itemId)
  if (!providerId) return
  await requestAi(itemId, providerId)
}

// --- Drafts ----------------------------------------------------------------------------------------

interface Draft {
  body: string
  picked: string | null
}

// Unsent answers per question of this document (written by the handlers only, never in render). They stay while the
// windows stay — whatever is selected — and go with the last window of the document.
const drafts = reactive<Record<number, Draft>>({})

function draftOf(itemId: number): Draft {
  if (!drafts[itemId]) drafts[itemId] = { body: '', picked: null }
  return drafts[itemId]
}

const bodyOf = (itemId: number) => drafts[itemId]?.body ?? ''
const pickedId = (itemId: number) => drafts[itemId]?.picked ?? null
const pickedOf = (itemId: number) => {
  const picked = pickedId(itemId)
  return picked === null ? [] : [picked]
}

function setBody(itemId: number, value: string) {
  draftOf(itemId).body = value
}

/** Picks an option of the question; picking it again takes it back. */
function pick(itemId: number, optionId: string) {
  const draft = draftOf(itemId)
  draft.picked = draft.picked === optionId ? null : optionId
}

function canSend(itemId: number): boolean {
  return ready(itemId, bodyOf(itemId), pickedOf(itemId))
}

/** The send button's tooltip: why answering is closed, else nothing beyond its label. */
const answerTitle = computed(() => (availability.value.answer.reason ? t(k(availability.value.answer.reason)) : undefined))

async function submit(itemId: number) {
  if (await answer(itemId, bodyOf(itemId), pickedOf(itemId))) delete drafts[itemId]
}

// A closed window's unsent answer and AI pick go with it (the other windows of the document keep theirs).
watch(
  () => props.windows.map((win) => win.itemId),
  (open) => {
    for (const id of Object.keys(drafts).map(Number)) if (!open.includes(id)) delete drafts[id]
    for (const id of Object.keys(aiPicks).map(Number)) if (!open.includes(id)) delete aiPicks[id]
  },
)

// --- The window's question -------------------------------------------------------------------------

const windowItem = (itemId: number): QnaItemView | null => items.value?.find((item) => item.id === itemId) ?? null

/** `Q# · title` of the window's title line (the first body line without a title). */
function windowSub(item: QnaItemView): string {
  const title = item.title ?? firstLine(item.body)
  return item.seq === null ? title : `${t(k('question'), { seq: item.seq })} · ${title}`
}

/**
 * The Flow is read only (the first reason answering is closed on), or the questions are history (the earlier implementation; the earlier implementation: a
 * superseded document too): the window says so.
 */
const readOnlyReason = computed(() => {
  if (props.history) return 'window.view'
  const reason = availability.value.answer.reason
  return reason !== null && (reason.startsWith('reason.read_only_') || reason === 'reason.history') ? reason : null
})

// --- Display ---------------------------------------------------------------------------------------

function sideText(side: QnaSide): string {
  return t(k(side.key), side.params)
}

/** The answer time of the window's answer detail (0064 T-B5), in the UI locale; the raw value if unreadable. */
function formatAt(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString(locale.value)
}

/** The first line of a question without a title. */
function firstLine(body: string): string {
  return body.split(/\r?\n/, 1)[0] ?? ''
}

/** The response proves only answered or not: both unanswered states read Unanswered. */
function stateKey(state: QnaItemState): string {
  return state === 'answered' ? 'state.answered' : 'state.unanswered'
}

function toneOf(state: QnaItemState): InspectorChipTone {
  return state === 'answered' ? 'success' : 'neutral'
}
</script>

<style scoped>
/* 0016-CH (mockup basodrr4 option B, choices only option A): one type scale (theme.css --fg2-fs-*: 16 / 13 / 12 / 11) and three text
   colours; the question body sits in an inset block, the options are a stacked radio list, and the form ends with the
   ask-an-AI controls on the left and 답변 on the right. */
.inspector-qna-answers {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 0;
  padding: 0 0 0 12px;
  border-left: 2px solid var(--fg2-line-strong);
  list-style: none;
}

.inspector-qna-answers li {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

/* The window's answer form: the options, the field, then [AI line · 답변]. */
.inspector-qna-answer {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
  margin-top: 4px;
}

.inspector-qna-options {
  display: grid;
  gap: 6px;
  min-width: 0;
}

.inspector-qna-option {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  min-width: 0;
  padding: 9px 12px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius);
  background: transparent;
  color: var(--fg2-text);
  font-size: var(--fg2-fs-body);
  text-align: left;
  cursor: pointer;
  transition: 0.16s ease;
}

.inspector-qna-option:hover:not(:disabled) {
  border-color: var(--fg2-control-hover-border);
}

.inspector-qna-option.is-on {
  border-color: var(--fg2-blue);
  background: rgba(75, 163, 255, 0.08);
}

.inspector-qna-option:disabled {
  cursor: not-allowed;
  opacity: var(--fg2-control-disabled-opacity-strong);
}

.inspector-qna-radio {
  flex: none;
  width: 15px;
  height: 15px;
  border: 1.5px solid var(--fg2-text-3);
  border-radius: 50%;
}

.inspector-qna-option.is-on .inspector-qna-radio {
  border: 4.5px solid var(--fg2-blue);
}

.inspector-qna-answer .inspector-qna-input {
  min-height: 90px;
  border-radius: var(--fg2-inset-radius);
  background: rgba(5, 12, 20, 0.6);
  font-size: var(--fg2-fs-body);
  line-height: 1.55;
  resize: vertical;
}

.inspector-qna-foot {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  min-width: 0;
  margin-top: 2px;
}

/* The AI line (0056 F1): the picker, [AI 답변 요청], then the waiting chip or the refusal icon. */
.inspector-qna-ai {
  display: flex;
  flex: 1 1 260px;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.inspector-qna-ai-icon {
  color: var(--fg2-text-3);
}

.inspector-qna-ai-pick {
  flex: 0 1 170px;
  min-width: 96px;
  font-size: var(--fg2-fs-sub);
}

.inspector-qna-line {
  display: flex;
  flex: none;
  align-items: center;
  gap: 8px;
  margin-left: auto;
}

.inspector-qna-foot :deep(.ui-btn svg) {
  width: 15px;
  height: 15px;
}

.inspector-qna-sub {
  color: var(--fg2-text-3);
  font-size: var(--fg2-fs-sub);
}

.inspector-qna-text {
  margin: 0;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}

.inspector-qna-answers .inspector-qna-text {
  color: var(--fg2-text-2);
}

/* The question body: an inset block, one step larger than the rest (option B .qbody). */
.inspector-qna-qbody {
  padding: 12px 14px;
  border: 1px solid var(--fg2-line);
  border-radius: var(--fg2-inset-radius);
  background: var(--fg2-inset-bg);
  color: var(--fg2-text-2);
  font-size: 14px;
  line-height: 1.65;
}

/* The ↗ window's title line: [Q&A badge · Q# title (ellipsis) · 읽기 전용]. Only the title shrinks; the badges and
   the shell's buttons keep their width. */
.inspector-qna-window-heading {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.inspector-qna-window-title {
  flex: 0 0 auto;
  padding: 2px 8px;
  border-radius: 6px;
  background: rgba(255, 174, 87, 0.14);
  color: var(--fg2-orange);
  font-size: var(--fg2-fs-xs);
  font-weight: 700;
}

.inspector-qna-window-sub {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  color: var(--fg2-text);
  font-size: var(--fg2-fs-body);
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.inspector-qna-window-mode {
  flex: 0 0 auto;
  padding: 1px 8px;
  border: 1px solid #4a4f9a;
  border-radius: var(--fg2-control-radius-pill);
  background: #1a1d3e;
  color: #cfcbff;
  font-size: var(--fg2-fs-xs);
  font-weight: 700;
  white-space: nowrap;
}

.inspector-qna-window {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 0;
  color: var(--fg2-text);
  font-size: var(--fg2-fs-body);
  line-height: 1.55;
}

.inspector-qna-window-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

/* The full document id, wrapped rather than cut, after a file icon. */
.inspector-qna-window-doc {
  display: flex;
  align-items: center;
  gap: 6px;
  overflow-wrap: anywhere;
}

.inspector-qna-window-doc .inspector-qna-doc-icon {
  width: 14px;
  height: 14px;
}

.inspector-qna-window-q {
  margin: 0;
  color: var(--fg2-text);
  font-size: var(--fg2-fs-title);
  font-weight: 700;
  line-height: 1.4;
}
</style>
