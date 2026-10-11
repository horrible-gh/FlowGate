<template>
  <!-- flowgate.v02.0048 T#7 — Q&A tab (D#1 §10.2 T#7 row): the questions of the selected Node's
       document — waiting for the user first — with who asked and who answered, and an answer form
       on each question without an answer: prose, one of the question's options, or both. A thin
       container: useQna reads and answers, qnaModel decides the order, the sides and availability.
       flowgate.v02.0052 묶음 A — drawn in the v7 shape (0035 insp.js qaRow / qaPane / openQA):
       one line per question — [state chip] Q# · title, a small line with who asked and `↳ answer ·
       답: side` — a ↗ button that opens the question in a modeless window (the full body, every
       answer, the full answer form), and a short inline answer line (field + 보내기, the options)
       under each question that still has no answer. Answered questions fold into a "완료 n" group,
       open when nothing is pending. Who waits for whom is not in the response (no "AI answering" /
       "handed to the user" / reviewer field), so every unanswered question is 미답변 whoever asked
       it and an answered one 완료 (0052 T#1) — the asker kind is never read as a hand-off.
       The window's title line keeps its badge and buttons in view: the title shrinks with an
       ellipsis, the badge does not. Answering is not editing the question, so there is no 편집
       badge; 읽기 전용 shows only when the Flow is read only (ownership conflict / revision view). -->
  <div class="inspector-qna" data-test="inspector-qna" :data-status="status">
    <InspectorPaneState v-if="status === 'loading' && !items" state="loading" />
    <InspectorPaneState v-else-if="errorKey && !items" state="error" :message="t(k(errorKey))" retryable @retry="reload" />
    <template v-else-if="items">
      <InspectorPaneState v-if="!items.length" state="empty" :message="t(k('empty'))" />
      <InspectorPaneState v-if="errorKey" state="error" :message="t(k(errorKey))" retryable @retry="reload" />
      <ul v-if="items.length" class="inspector-qna-list">
        <template v-for="(item, index) in items" :key="item.id">
          <!-- v7 `.qa-grp.tg`: the answered questions fold under one line with their number. -->
          <li v-if="index === firstDoneIndex" class="inspector-qna-group-row">
            <button
              type="button"
              class="inspector-qna-group"
              :aria-expanded="doneOpen"
              data-test="qna-done-group"
              @click="doneOpen = !doneOpen"
            >
              <span class="inspector-qna-caret" :class="{ 'is-open': doneOpen }" aria-hidden="true">▸</span>
              <span>{{ t(k('done_group')) }}</span>
              <b data-test="qna-done-count">{{ doneCount }}</b>
            </button>
          </li>
          <li
            v-show="item.state !== 'answered' || doneOpen"
            class="inspector-qna-item"
            :class="{ 'is-done': item.state === 'answered' }"
            data-test="qna-item"
            :data-item="item.id"
            :data-state="item.state"
          >
            <div class="inspector-qna-row">
              <InspectorStatusChip class="inspector-qna-chip" :label="t(k(stateKey(item.state)))" :tone="toneOf(item.state)" data-test="qna-item-state" />
              <div class="inspector-qna-main">
                <span class="inspector-qna-q" :title="item.body">
                  <span v-if="item.seq !== null" class="inspector-qna-seq">{{ t(k('question'), { seq: item.seq }) }}</span>
                  <span class="inspector-qna-title" data-test="qna-item-title">{{ item.title ?? firstLine(item.body) }}</span>
                </span>
                <small class="inspector-qna-small">
                  <span data-test="qna-item-asker" :data-side="item.asker.kind">{{ sideText(item.asker) }}</span>
                  <template v-if="lastAnswerOf(item)">
                    <span class="inspector-qna-sep" aria-hidden="true">·</span>
                    <span class="inspector-qna-last" data-test="qna-answer" :data-side="lastAnswerOf(item)!.side.kind">
                      <span aria-hidden="true">↳</span>
                      <span class="inspector-qna-last-body" data-test="qna-answer-body">{{ lastAnswerOf(item)!.body }}</span>
                      <span v-if="lastAnswerOf(item)!.picked.length" class="inspector-qna-by" data-test="qna-answer-picked">{{ t(k('picked'), { labels: lastAnswerOf(item)!.picked.join(', ') }) }}</span>
                      <span class="inspector-qna-by" data-test="qna-answer-side">{{ t(k('answered_by'), { side: sideText(lastAnswerOf(item)!.side) }) }}</span>
                    </span>
                  </template>
                </small>
              </div>
              <!-- flowgate.v02.0053 T#5 (0053 CH): the same document icon as the document lines (tooltip 열기). -->
              <InspectorOpenButton
                class="inspector-qna-open"
                :title="t(k('open'))"
                data-test="qna-open"
                @click="openWindow(item.id)"
              />
            </div>

            <!-- v7 `.q-ans`: the short inline answer under a question without an answer. -->
            <div v-if="item.answerable" class="inspector-qna-answer" data-test="qna-answer-form">
              <div v-if="item.options.length" class="inspector-qna-options" role="group" :aria-label="t(k('options'))">
                <Button
                  v-for="option in item.options"
                  :key="option.id"
                  size="sm"
                  :variant="pickedId(item.id) === option.id ? 'primary' : 'secondary'"
                  :aria-pressed="pickedId(item.id) === option.id"
                  :disabled="!availability.answer.enabled"
                  data-test="qna-option"
                  :data-option="option.id"
                  @click="pick(item.id, option.id)"
                >{{ option.label }}</Button>
              </div>
              <div class="inspector-qna-send">
                <Textarea
                  class="inspector-qna-field"
                  :model-value="bodyOf(item.id)"
                  :rows="1"
                  :placeholder="t(k('answer_short_placeholder'))"
                  :disabled="!availability.answer.enabled"
                  data-test="qna-answer-body-input"
                  @update:model-value="(value: string) => setBody(item.id, value)"
                />
                <Button
                  size="sm"
                  variant="primary"
                  :disabled="!availability.answer.enabled || !canSend(item.id)"
                  :loading="busy === item.id"
                  data-test="qna-answer-submit"
                  @click="submit(item.id)"
                >{{ t(k('send')) }}</Button>
              </div>
              <div v-if="availability.answer.reason" data-test="qna-answer-reason">
                <InspectorReason :text="t(k(availability.answer.reason))" />
              </div>
              <div v-if="failure && failure.itemId === item.id" data-test="qna-answer-failure">
                <InspectorReason tone="danger" :text="t(k(failure.key))" />
              </div>
            </div>
          </li>
        </template>
      </ul>
    </template>

    <!-- v7 ↗ (0035 insp.js openQA / openWin): one modeless window per opened question — the full
         question, every answer and the full answer form; several may be open at once. -->
    <ModelessShell
      v-for="(win, index) in windows"
      :key="win.id"
      :open="true"
      :initial-x="win.x"
      :initial-y="win.y"
      :initial-width="520"
      :initial-height="380"
      :min-width="320"
      :min-height="200"
      @close="closeWindow(win.id)"
    >
      <template #header>
        <span class="inspector-qna-window-heading">
          <span class="inspector-qna-window-title">{{ t(k('window.title')) }}</span>
          <span v-if="windowItem(win.id)" class="inspector-qna-window-sub" :title="windowSub(windowItem(win.id)!)" data-test="qna-window-sub">{{ windowSub(windowItem(win.id)!) }}</span>
          <span
            v-if="readOnlyReason"
            class="inspector-qna-window-mode"
            data-mode="view"
            :title="t(k(readOnlyReason))"
            data-test="qna-window-mode"
          >{{ t(k('window.view')) }}</span>
        </span>
      </template>
      <div class="inspector-qna-window" data-test="qna-window" :data-item="win.id" :data-index="index" :data-state="windowItem(win.id)?.state ?? ''">
        <template v-if="windowItem(win.id)">
          <div class="inspector-qna-window-head">
            <InspectorStatusChip :label="t(k(stateKey(windowItem(win.id)!.state)))" :tone="toneOf(windowItem(win.id)!.state)" data-test="qna-window-state" />
            <span class="inspector-qna-sub" data-test="qna-window-asker">{{ t(k('asked_by'), { side: sideText(windowItem(win.id)!.asker) }) }}</span>
          </div>
          <p v-if="windowItem(win.id)!.title" class="inspector-qna-window-q">{{ windowItem(win.id)!.title }}</p>
          <p class="inspector-qna-text" data-test="qna-window-body">{{ windowItem(win.id)!.body }}</p>

          <ul v-if="windowItem(win.id)!.answers.length" class="inspector-qna-answers">
            <li v-for="answer in windowItem(win.id)!.answers" :key="answer.key" data-test="qna-window-answer" :data-side="answer.side.kind">
              <span class="inspector-qna-sub">{{ t(k('answered_by'), { side: sideText(answer.side) }) }}</span>
              <span v-if="answer.picked.length" class="inspector-qna-sub">{{ t(k('picked'), { labels: answer.picked.join(', ') }) }}</span>
              <p class="inspector-qna-text">{{ answer.body }}</p>
            </li>
          </ul>

          <div v-if="windowItem(win.id)!.answerable" class="inspector-qna-answer" data-test="qna-window-form">
            <div v-if="windowItem(win.id)!.options.length" class="inspector-qna-options" role="group" :aria-label="t(k('options'))">
              <Button
                v-for="option in windowItem(win.id)!.options"
                :key="option.id"
                size="sm"
                :variant="pickedId(win.id) === option.id ? 'primary' : 'secondary'"
                :aria-pressed="pickedId(win.id) === option.id"
                :disabled="!availability.answer.enabled"
                data-test="qna-window-option"
                :data-option="option.id"
                @click="pick(win.id, option.id)"
              >{{ option.label }}</Button>
            </div>
            <Textarea
              :model-value="bodyOf(win.id)"
              :rows="4"
              :placeholder="t(k('answer_placeholder'))"
              :disabled="!availability.answer.enabled"
              data-test="qna-window-body-input"
              @update:model-value="(value: string) => setBody(win.id, value)"
            />
            <div class="inspector-qna-line">
              <Button
                size="sm"
                variant="primary"
                :disabled="!availability.answer.enabled || !canSend(win.id)"
                :loading="busy === win.id"
                data-test="qna-window-submit"
                @click="submit(win.id)"
              >{{ t(k('answer')) }}</Button>
            </div>
            <div v-if="availability.answer.reason" data-test="qna-window-reason">
              <InspectorReason :text="t(k(availability.answer.reason))" />
            </div>
            <div v-if="failure && failure.itemId === win.id" data-test="qna-window-failure">
              <InspectorReason tone="danger" :text="t(k(failure.key))" />
            </div>
          </div>
        </template>
        <InspectorPaneState v-else state="empty" :message="t(k('window.gone'))" />
      </div>
    </ModelessShell>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '../../../../../components/ui/Button.vue'
import ModelessShell from '../../../../../components/ui/ModelessShell.vue'
import Textarea from '../../../../../components/ui/Textarea.vue'
import InspectorOpenButton from '../../parts/InspectorOpenButton.vue'
import InspectorPaneState from '../../parts/InspectorPaneState.vue'
import InspectorReason from '../../parts/InspectorReason.vue'
import InspectorStatusChip, { type InspectorChipTone } from '../../parts/InspectorStatusChip.vue'
import type { InspectorTarget } from '../../inspectorTarget'
import type { QnaAnswerView, QnaItemState, QnaItemView, QnaSide } from './qnaModel'
import { useQna } from './useQna'

const props = defineProps<{
  target: InspectorTarget
}>()

const { t } = useI18n()

const k = (key: string) => `gui.inspector.qna.${key}`

const { items, availability, status, errorKey, busy, failure, ready, answer, reload } = useQna(() => props.target)

// --- The folded "완료" group (v7 qaPane: open when nothing is pending) ------------------------------

const firstDoneIndex = computed(() => items.value?.findIndex((item) => item.state === 'answered') ?? -1)
const doneCount = computed(() => items.value?.filter((item) => item.state === 'answered').length ?? 0)
const pendingCount = computed(() => (items.value?.length ?? 0) - doneCount.value)

const doneOpen = ref(false)
/** The user's choice stands while the document stays; a document without a pending question opens the group. */
let doneTouched = false
watch(
  [() => props.target.docId, pendingCount],
  (current, previous) => {
    if (current[0] !== previous?.[0]) doneTouched = false
    if (!doneTouched) doneOpen.value = pendingCount.value === 0
  },
  { immediate: true },
)
watch(doneOpen, () => {
  doneTouched = true
})

// --- The ↗ windows ---------------------------------------------------------------------------------

interface QnaWindow {
  readonly id: number
  readonly x: number
  readonly y: number
}

const windows = ref<QnaWindow[]>([])
let cascade = 0

/** Opens the question's window; a window already open stays where it is. */
function openWindow(itemId: number) {
  if (windows.value.some((win) => win.id === itemId)) return
  const offset = (cascade++ % 6) * 28
  windows.value = [...windows.value, { id: itemId, x: 160 + offset, y: 140 + offset }]
}

function closeWindow(itemId: number) {
  windows.value = windows.value.filter((win) => win.id !== itemId)
}

// --- Drafts ----------------------------------------------------------------------------------------

interface Draft {
  body: string
  picked: string | null
}

// Unsent answers per question of the shown document (written by the handlers only, never in render).
// The inline line and the ↗ window of one question share its draft.
const drafts = reactive<Record<number, Draft>>({})

function draftOf(itemId: number): Draft {
  if (!drafts[itemId]) drafts[itemId] = { body: '', picked: null }
  return drafts[itemId]
}

watch(
  () => props.target.docId,
  () => {
    for (const id of Object.keys(drafts)) delete drafts[Number(id)]
    windows.value = []
  },
)

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

async function submit(itemId: number) {
  if (await answer(itemId, bodyOf(itemId), pickedOf(itemId))) delete drafts[itemId]
}

// --- The window's question -------------------------------------------------------------------------

const windowItem = (itemId: number): QnaItemView | null => items.value?.find((item) => item.id === itemId) ?? null

/** `Q# · title` of the window's title line (the first body line without a title). */
function windowSub(item: QnaItemView): string {
  const title = item.title ?? firstLine(item.body)
  return item.seq === null ? title : `${t(k('question'), { seq: item.seq })} · ${title}`
}

/** The Flow is read only (the first reason answering is closed on): the window says so. */
const readOnlyReason = computed(() => {
  const reason = availability.value.answer.reason
  return reason !== null && reason.startsWith('reason.read_only_') ? reason : null
})

// --- Display ---------------------------------------------------------------------------------------

function sideText(side: QnaSide): string {
  return t(k(side.key), side.params)
}

const lastAnswerOf = (item: QnaItemView): QnaAnswerView | null => item.answers[item.answers.length - 1] ?? null

/** The first line of a question without a title. */
function firstLine(body: string): string {
  return body.split(/\r?\n/, 1)[0] ?? ''
}

/** The response proves only answered or not: both unanswered states read 미답변. */
function stateKey(state: QnaItemState): string {
  return state === 'answered' ? 'state.answered' : 'state.unanswered'
}

function toneOf(state: QnaItemState): InspectorChipTone {
  return state === 'answered' ? 'success' : 'neutral'
}
</script>

<style scoped>
.inspector-qna {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
  font-size: 12px;
}

.inspector-qna-list,
.inspector-qna-answers {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}

/* v7 `.qa-row`: [state] question + small | ↗, the inline answer line under it. */
.inspector-qna-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
  padding: 6px 9px;
  border: 1px solid var(--fg2-line);
  border-radius: 8px;
  background: #0e2034;
}

.inspector-qna-item.is-done {
  opacity: 0.85;
}

.inspector-qna-row {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 9px;
  align-items: center;
  min-width: 0;
}

.inspector-qna-chip {
  max-width: 96px;
}

.inspector-qna-main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.inspector-qna-q {
  display: flex;
  gap: 5px;
  min-width: 0;
  overflow: hidden;
  color: #c9d7e5;
  white-space: nowrap;
}

.inspector-qna-seq {
  flex: 0 0 auto;
  font-weight: 600;
}

.inspector-qna-title {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.inspector-qna-small {
  display: flex;
  gap: 5px;
  min-width: 0;
  overflow: hidden;
  color: #7189a2;
  font-size: 10.5px;
  white-space: nowrap;
}

.inspector-qna-last {
  display: inline-flex;
  gap: 4px;
  min-width: 0;
  overflow: hidden;
}

.inspector-qna-last-body {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* v7 `.qa-by`. */
.inspector-qna-by {
  flex: 0 0 auto;
  color: #c9b6ff;
}

.inspector-qna-open {
  flex: 0 0 auto;
}

/* v7 `.q-ans`: a short field and the send button in one line; the options above it. */
.inspector-qna-answer {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.inspector-qna-send {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.inspector-qna-send .inspector-qna-field {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 28px;
  height: 28px;
  padding: 4px 9px;
  resize: none;
  line-height: 18px;
}

.inspector-qna-send > :last-child {
  flex: 0 0 auto;
}

.inspector-qna-line,
.inspector-qna-options {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

/* v7 `.qa-grp.tg`: the fold line of the answered questions. */
.inspector-qna-group-row {
  margin-top: 2px;
  padding-top: 4px;
  border-top: 1px dashed #23405f;
}

.inspector-qna-group {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 4px 2px;
  border: 0;
  background: transparent;
  color: #9fb3c9;
  font-size: 11.5px;
  font-weight: 650;
  text-align: left;
  cursor: pointer;
}

.inspector-qna-group b {
  color: #72e4b8;
}

.inspector-qna-caret {
  display: inline-block;
  transition: transform 0.15s;
}

.inspector-qna-caret.is-open {
  transform: rotate(90deg);
}

.inspector-qna-sub {
  color: var(--fg2-muted);
  font-size: 11.5px;
}

.inspector-qna-text {
  margin: 0;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}

/* The ↗ window (v7 `.mw-tb`): title line [Q&A · Q# title (ellipsis) · 읽기 전용], the question, the
   answers, the form. Only the title shrinks; the badge and the shell's buttons keep their width. */
.inspector-qna-window-heading {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.inspector-qna-window-title {
  flex: 0 0 auto;
  font-weight: 700;
}

.inspector-qna-window-sub {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  color: #8fa7c0;
  font-size: 11px;
  font-weight: 500;
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
  font-size: 10.5px;
  font-weight: 700;
  white-space: nowrap;
}

.inspector-qna-window {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
  font-size: 12.5px;
  line-height: 1.6;
}

.inspector-qna-window-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.inspector-qna-window-q {
  margin: 0;
  color: var(--fg2-text);
  font-weight: 700;
}

.inspector-qna-answers {
  padding-left: 10px;
  border-left: 2px solid var(--fg2-line);
}

.inspector-qna-answers li {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
</style>
