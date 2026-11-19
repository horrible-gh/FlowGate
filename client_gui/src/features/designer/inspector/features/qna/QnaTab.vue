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
       open when nothing is pending. flowgate.v02test.0051 C: the chip says what the server recorded about the
       automatic responder (`responder`): 대기 중 (no answerer assigned yet) / AI 답변 중 / 기술 실패 (with its error code
       and message) / 사람 답변 필요 (only a `user_decision`); a question a person asked with nothing recorded is 미답변
       and an answered one 완료 - the asker kind alone is never read as a hand-off. The small line also shows the
       past-answer search record (searched / found count / reused answer and its source, "검색 기록 없음" for legacy).
       The window's title line keeps its badge and buttons in view: the title shrinks with an
       ellipsis, the badge does not. Answering is not editing the question, so there is no 편집
       badge; 읽기 전용 shows only when the Flow is read only (ownership conflict).
       flowgate.v02.0053 T#6 (0053 CH §2) — the question line at the top: a person writes a question
       and [+] (or Enter) registers it. The line is always there, and a document without questions
       shows only the line — no "no questions" sentence. Why [+] is closed is its tooltip. A refusal is a
       red ! icon on the line whose name says what failed and whose tooltip says why.
       flowgate.v02.0056 F1 (0056 CH §3) — registering never asks an AI by itself. Every open question
       carries its own AI line under the answer form: an AI picker (the project's providers, the project
       default picked first) and [AI 답변 요청]; the button shows the request on its way, a running chip
       says the AI answer is awaited once the run started, and a refusal is an amber ! icon beside the
       button with the reason as its tooltip. A person still answers directly, as before.
       flowgate.v02.0063 — no reason sentence under a closed form; a Flow whose answers stay closed shows
       no question line, answer form or AI line (qnaAvailability `visible`).
       flowgate.v02.0064 T-B5 (P0006 §5.2·§6.4) — who answered reads one of four classes (person by name /
       AI · provider / AI run not linked / AI no run information) plus unknown; the list shows the side only,
       the window's answer adds the answer time and the run id when there is one.
       flowgate.v02.0070 T#4 (B4) — `history`: the questions of a document read as history (the document an R↔B
       succession came from, in its exact document window): the same list and ↗ windows, read only — no question
       line, answer form or AI line, and the ↗ window says 읽기 전용.
       flowgate.v02.0070 T#5 (B5) — the list is for looking over and opening: each line keeps the question (one line,
       ellipsis), its state, who asked and [열기] — no options, answer field, send or AI line. The ↗ window is where a
       question is answered: the document id (full), who asked and when, the full question, the answers, then the
       stored options, the answer field, send and the AI line. While the body cannot be checked (unreadable, not known,
       history) the window shows one line instead of them (history: the 읽기 전용 badge); reading stays.
       flowgate.v02.0150 — the ↗ windows moved to QnaWindows (drawn by the Frame's QnaWindowHost): a window keeps its
       document whatever is selected and closes only by its own ×. -->
  <div class="inspector-qna" data-test="inspector-qna" :data-status="status">
    <div v-if="availability.ask.visible" class="inspector-qna-ask" data-test="qna-ask">
      <Input
        class="inspector-qna-ask-field"
        :model-value="askBody"
        :placeholder="t(k('ask_placeholder'))"
        :aria-label="t(k('ask'))"
        :disabled="!availability.ask.enabled"
        data-test="qna-ask-input"
        @update:model-value="(value: string) => (askBody = value)"
        @keydown.enter="onAskEnter"
      />
      <InspectorAddButton
        :label="t(k('ask'))"
        :title="askTitle"
        :disabled="!availability.ask.enabled || !askReady"
        :data-busy="asking ? 'true' : 'false'"
        data-test="qna-ask-submit"
        @click="submitAsk"
      />
      <InspectorFailureIcon v-if="askFailure" :name="t(k('ask_failed'))" :reason="t(k(askFailure.key))" data-test="qna-ask-failure" />
    </div>
    <InspectorPaneState v-if="status === 'loading' && !items" state="loading" />
    <InspectorPaneState v-else-if="errorKey && !items" state="error" :message="t(k(errorKey))" retryable @retry="reload" />
    <template v-else-if="items">
      <InspectorPaneState v-if="errorKey" state="error" :message="t(k(errorKey))" retryable @retry="reload" />
      <!-- flowgate.v02test.0051 C: searches that registered no question (a reused answer, a refused resend, a failed
           search). No question line carries them, so they are listed here from `search_prechecks` and stay after a reload.
           The reuse the last ask just came to shows at once, until the read after it lists the same record. -->
      <ul v-if="askReused || prechecks.length" class="inspector-qna-prechecks" data-test="qna-prechecks">
        <li v-if="askReused" class="inspector-qna-precheck" data-test="qna-ask-reused" :data-outcome="askReused.outcome">
          <p v-for="(line, index) in precheckLines(askReused)" :key="index" data-test="qna-precheck-line">{{ line }}</p>
        </li>
        <li v-for="precheck in prechecks" :key="precheck.key" class="inspector-qna-precheck" data-test="qna-precheck" :data-outcome="precheck.outcome" :data-precheck="precheck.precheckId ?? ''">
          <p v-for="(line, index) in precheckLines(precheck)" :key="index" data-test="qna-precheck-line">{{ line }}</p>
        </li>
      </ul>
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
            :data-awaiting="awaiting.has(item.id) ? 'true' : 'false'"
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
                  <span v-if="item.responder" class="inspector-qna-failure" :title="responderText(item.responder)" data-test="qna-item-responder">{{ responderText(item.responder) }}</span>
                  <span :title="searchText(item.search)" data-test="qna-item-search" :data-search="item.search.kind">{{ searchText(item.search) }}</span>
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
          </li>
        </template>
      </ul>
    </template>

    <!-- flowgate.v02.0150 — the ↗ windows are not drawn here: the Frame's window host (QnaWindowHost) draws them with the
         target they were opened for, so a selection change, a tab change or a click elsewhere never closes them. A history
         Q&A (a document window's aside) draws its own, which live with that document window. -->
    <QnaWindows v-if="history" :key="historyKey" :target="target" :windows="historyWindows" history @close="closeHistoryWindow" />
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Input from '../../../../../components/ui/Input.vue'
import InspectorAddButton from '../../parts/InspectorAddButton.vue'
import InspectorFailureIcon from '../../parts/InspectorFailureIcon.vue'
import InspectorOpenButton from '../../parts/InspectorOpenButton.vue'
import InspectorPaneState from '../../parts/InspectorPaneState.vue'
import InspectorStatusChip, { type InspectorChipTone } from '../../parts/InspectorStatusChip.vue'
import type { InspectorTarget } from '../../inspectorTarget'
import QnaWindows from './QnaWindows.vue'
import {
  qnaAskReady,
  qnaPrecheckLines,
  qnaResponderLines,
  qnaSearchLines,
  qnaStateKey,
  qnaStateTone,
  type QnaAvailability,
  type QnaItemState,
  type QnaPrecheckView,
  type QnaResponderView,
  type QnaSearchView,
  type QnaSide,
} from './qnaModel'
import { useQna } from './useQna'
import { nextQnaWindowPlace, openQnaWindow, qnaWindowKey, type QnaWindow } from './useQnaWindows'

const props = defineProps<{
  target: InspectorTarget
  /** flowgate.v02.0070 T#4: read only history — the questions and answers are read, nothing is asked or answered. */
  history?: boolean
}>()

const { t } = useI18n()

const k = (key: string) => `gui.inspector.qna.${key}`

const {
  items,
  prechecks,
  askReused,
  availability: liveAvailability,
  status,
  errorKey,
  asking,
  askFailure,
  ask,
  awaiting,
  reload,
} = useQna(() => props.target, { history: () => props.history === true })

/**
 * History: only reading is offered. Answering and the AI line close in the model (0070 T#5 body context `history`);
 * the question line closes here (asking is not this rule's).
 */
const HISTORY_CLOSED = { visible: false, enabled: false, reason: null } as const
const availability = computed<QnaAvailability>(() =>
  props.history ? { ...liveAvailability.value, ask: HISTORY_CLOSED } : liveAvailability.value,
)

// --- The question line (0053 T#6) ------------------------------------------------------------------

/** The question being written; it belongs to the shown document and clears once it is stored. */
const askBody = ref('')
const askReady = computed(() => qnaAskReady(askBody.value))

/** [+]'s tooltip: what it does, or why it is closed. */
const askTitle = computed(() => (availability.value.ask.reason ? t(k(availability.value.ask.reason)) : t(k('ask'))))

async function submitAsk() {
  const docId = props.target.docId
  if (await ask(askBody.value)) {
    if (props.target.docId === docId) askBody.value = ''
  }
}

/** Enter registers the question; Enter that ends an IME composition (Korean / Japanese input) does not. */
function onAskEnter(event: KeyboardEvent) {
  if (event.isComposing || event.keyCode === 229) return
  event.preventDefault()
  if (availability.value.ask.enabled && askReady.value) void submitAsk()
}

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

// flowgate.v02.0150: a question's window is opened with this target and drawn by the Frame's window host, which keeps
// it whatever is selected. A history Q&A keeps its windows here: they belong to its document window.
const historyWindows = ref<QnaWindow[]>([])
const historyKey = computed(() => qnaWindowKey(props.target) ?? '')

/** Opens the question's window; a window already open stays where it is. */
function openWindow(itemId: number) {
  if (!props.history) {
    openQnaWindow(props.target, itemId)
    return
  }
  if (historyWindows.value.some((win) => win.itemId === itemId)) return
  historyWindows.value = [...historyWindows.value, { id: String(itemId), itemId, ...nextQnaWindowPlace() }]
}

function closeHistoryWindow(itemId: number) {
  historyWindows.value = historyWindows.value.filter((win) => win.itemId !== itemId)
}

// The question being written belongs to the shown document; a history Q&A's windows to the document it shows.
watch(
  () => props.target.docId,
  () => {
    historyWindows.value = []
    askBody.value = ''
  },
)

// --- Display ---------------------------------------------------------------------------------------

function sideText(side: QnaSide): string {
  return t(k(side.key), side.params)
}

/** The first line of a question without a title. */
function firstLine(body: string): string {
  return body.split(/\r?\n/, 1)[0] ?? ''
}

/** flowgate.v02test.0051 C: the chip says what the server recorded — queued / AI answering / technical failure / person needed. */
function stateKey(state: QnaItemState): string {
  return qnaStateKey(state)
}

function toneOf(state: QnaItemState): InspectorChipTone {
  return qnaStateTone(state)
}

const text = (key: string, params?: Record<string, string | number>) => t(k(key), params ?? {})

/** A failed question's error code and message (one line; the full text is the tooltip). */
function responderText(view: QnaResponderView): string {
  return qnaResponderLines(view, text).join(' · ')
}

/** A search that registered no question, line by line: what came of it, the count, the source, the reused answer. */
function precheckLines(view: QnaPrecheckView): string[] {
  return qnaPrecheckLines(view, text)
}

/** The past-answer search record as one line: searched / found count / reused answer, or "검색 기록 없음". */
function searchText(view: QnaSearchView): string {
  return qnaSearchLines(view, text).join(' · ')
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

/* The question line: a field and [+] in one row, always at the top. */
.inspector-qna-ask {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.inspector-qna-ask-field {
  flex: 1 1 auto;
  min-width: 0;
  height: 28px;
  font-size: 12px;
}

.inspector-qna-list {
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

.inspector-qna-prechecks {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 160px;
  margin: 0;
  padding: 0;
  overflow-y: auto;
  list-style: none;
}

.inspector-qna-precheck {
  padding: 5px 9px;
  border: 1px dashed var(--fg2-line);
  border-radius: 8px;
  color: #9db2c8;
  font-size: 10.5px;
  overflow-wrap: anywhere;
}

.inspector-qna-precheck p {
  margin: 0;
}

.inspector-qna-precheck p:first-child {
  color: #c9d7e5;
  font-weight: 600;
}

.inspector-qna-failure {
  overflow: hidden;
  color: #f0a3a3;
  text-overflow: ellipsis;
}

.inspector-qna-open {
  flex: 0 0 auto;
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
</style>
