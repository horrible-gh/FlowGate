<template>
  <!-- flowgate.v02.0056 (0017-CH) — the card memo windows (useCardMemoWindows): one form per memo — title · body · [AI가
       참조] — with [삭제] · [저장] · [닫기]. The header names the memo (a new one: 새 메모) with the AI 참조 chip when
       the AI refers to it; no hint text. A read-only view of the window's card keeps the form but changes nothing.
       A written memo opened to read (the line's 열기) shows its body with [삭제] · [편집] · [닫기]; [편집] or the
       line's ✎ turns it into the form. -->
  <ModelessShell
    v-for="win in cardMemoWindows"
    :key="win.id"
    :open="true"
    :initial-x="win.x"
    :initial-y="win.y"
    :initial-width="460"
    :initial-height="420"
    :min-width="320"
    :min-height="260"
    @close="closeCardMemoWindow(win)"
  >
    <template #header>
      <span class="card-memo-window-heading">
        <span class="card-memo-window-title" data-test="card-memo-window-sub">{{ win.memo ? win.memo.title : t(k('window.new')) }}</span>
        <span v-if="win.memo?.aiRef" class="card-memo-window-chip" data-test="card-memo-window-ai-ref">{{ t(k('ai_ref_on')) }}</span>
      </span>
    </template>
    <div class="card-memo-window" data-test="card-memo-window" :data-memo="win.memoId ?? ''">
      <p v-if="win.gone" class="card-memo-window-note is-danger" data-test="card-memo-gone">{{ t(k('failure.memo_not_found')) }}</p>
      <div v-if="!win.editing" class="card-memo-window-read" data-test="card-memo-read">{{ win.memo?.body ?? '' }}</div>
      <template v-else>
        <label class="card-memo-window-field">
          <span class="card-memo-window-label">{{ t(k('field.title')) }}</span>
          <Input
            v-model="win.draft.title"
            :invalid="fieldText(win, 'title') !== ''"
            :disabled="win.busy || locked(win) || win.gone"
            data-test="card-memo-title-input"
            @input="clearField(win, 'title')"
          />
        </label>
        <p v-if="fieldText(win, 'title')" class="card-memo-window-error" data-test="card-memo-title-error">{{ fieldText(win, 'title') }}</p>
        <label class="card-memo-window-field card-memo-window-field--grow">
          <span class="card-memo-window-label">
            {{ t(k('field.body')) }}
            <span class="card-memo-window-length" :class="{ 'is-over': cardMemoLength(win.draft.body) > CARD_MEMO_BODY_MAX }" data-test="card-memo-body-length">
              {{ t(k('length'), { n: cardMemoLength(win.draft.body), max: CARD_MEMO_BODY_MAX }) }}
            </span>
          </span>
          <Textarea
            v-model="win.draft.body"
            class="card-memo-window-body"
            :rows="6"
            :invalid="fieldText(win, 'body') !== ''"
            :disabled="win.busy || locked(win) || win.gone"
            data-test="card-memo-body-input"
            @input="clearField(win, 'body')"
          />
        </label>
        <p v-if="fieldText(win, 'body')" class="card-memo-window-error" data-test="card-memo-body-error">{{ fieldText(win, 'body') }}</p>
        <label class="card-memo-window-toggle">
          <Toggle v-model="win.draft.aiRef" :aria-label="t(k('ai_ref'))" :disabled="win.busy || locked(win) || win.gone" data-test="card-memo-ai-ref-toggle" />
          <span>{{ t(k('ai_ref')) }}</span>
        </label>
      </template>
      <p v-if="win.failure && !win.failure.field" class="card-memo-window-note is-danger" data-test="card-memo-failure" :data-key="win.failure.key">
        {{ failureText(win.failure) }}
      </p>
      <div v-if="win.confirming" class="card-memo-window-confirm" data-test="card-memo-confirm">
        <span>{{ t(k('delete_confirm')) }}</span>
        <Button size="sm" variant="secondary" :disabled="win.busy" data-test="card-memo-confirm-cancel" @click="win.confirming = false">{{ t(k('cancel')) }}</Button>
        <Button size="sm" variant="danger" :loading="win.busy" data-test="card-memo-confirm-delete" @click="confirmDelete(win)">{{ t(k('delete')) }}</Button>
      </div>
      <div class="card-memo-window-bar">
        <Button
          v-if="win.memoId !== null"
          size="sm"
          variant="secondary"
          class="card-memo-window-delete"
          :disabled="win.busy || locked(win) || win.gone || win.confirming"
          :title="lockText(win)"
          data-test="card-memo-delete"
          @click="win.confirming = true"
        >{{ t(k('delete')) }}</Button>
        <Button
          v-if="!win.editing"
          size="sm"
          variant="primary"
          :disabled="locked(win) || win.gone || win.confirming"
          :title="lockText(win)"
          data-test="card-memo-window-edit"
          @click="editCardMemoWindow(win)"
        >{{ t(k('edit')) }}</Button>
        <Button
          v-else
          size="sm"
          variant="primary"
          :loading="win.busy && !win.confirming"
          :disabled="locked(win) || win.gone || win.confirming || unchanged(win)"
          :title="lockText(win)"
          data-test="card-memo-save"
          @click="save(win)"
        >{{ t(k('save')) }}</Button>
        <Button size="sm" variant="secondary" data-test="card-memo-close" @click="closeCardMemoWindow(win)">{{ t(k('close')) }}</Button>
      </div>
    </div>
  </ModelessShell>
</template>

<script setup lang="ts">
// flowgate.v02.0056 (0017-CH) — the memo windows' form and writes (the writes announce themselves, so the documents
// tab list follows them). `target` is the selected card: a window of that card is locked while its view is read only.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '../../../../../components/ui/Button.vue'
import Input from '../../../../../components/ui/Input.vue'
import ModelessShell from '../../../../../components/ui/ModelessShell.vue'
import Textarea from '../../../../../components/ui/Textarea.vue'
import Toggle from '../../../../../components/ui/Toggle.vue'
import type { InspectorTarget } from '../../inspectorTarget'
import {
  CARD_MEMO_BODY_MAX,
  cardMemoBodyProblem,
  cardMemoChanges,
  cardMemoLength,
  cardMemoLockedReason,
  cardMemoTitleProblem,
  type CardMemoDraft,
  type CardMemoFailure,
} from './cardMemoModel'
import { cardMemoWindows, closeCardMemoWindow, editCardMemoWindow, type CardMemoWindow } from './useCardMemoWindows'
import { cardMemoCardOf, cardMemoWriteCreate, cardMemoWriteRemove, cardMemoWriteUpdate, sameCard } from './useCardMemo'

const props = defineProps<{
  target: InspectorTarget | null
}>()

const { t } = useI18n()
const k = (key: string) => `gui.inspector.cardMemo.${key}`

const card = computed(() => cardMemoCardOf(props.target))
const lockedReason = computed(() => cardMemoLockedReason(props.target))

/** The window's card cannot be changed here (the selected view is read only). */
function locked(win: CardMemoWindow): boolean {
  return sameCard(win.card, card.value) && lockedReason.value !== null
}

function lockText(win: CardMemoWindow): string | undefined {
  return locked(win) && lockedReason.value ? t(k(lockedReason.value)) : undefined
}

/** A written memo whose form says what it already says: nothing to save. */
function unchanged(win: CardMemoWindow): boolean {
  return win.memo !== null && cardMemoChanges(win.memo, win.draft) === null
}

function failureText(failure: CardMemoFailure): string {
  const words = t(k(`failure.${failure.key}`))
  return failure.message ? `${words} — ${failure.message}` : words
}

/** A new edit of a field: an earlier refusal of that field no longer stands. */
function clearField(win: CardMemoWindow, field: 'title' | 'body'): void {
  if (win.failure?.field === field) win.failure = null
}

/** The refusal under a field: the server's, else what the form sees itself. */
function fieldText(win: CardMemoWindow, field: 'title' | 'body'): string {
  if (win.failure?.field === field) return failureText(win.failure)
  const problem = field === 'title' ? cardMemoTitleProblem(win.draft.title) : cardMemoBodyProblem(win.draft.body)
  // A title not written yet is not an error until [저장] is pressed (the refusal above says it then).
  if (!problem || problem === 'required') return ''
  return t(k(`failure.field.${field}.${problem}`))
}

/**
 * A refusal the form sees before sending, as the server would word it. `sent` names the fields a change sends:
 * a field kept as the server stored it is not checked again (changing only [AI가 참조] is never refused here).
 */
function problemOf(draft: CardMemoDraft, sent?: { title?: string; body?: string }): CardMemoFailure | null {
  const title = !sent || 'title' in sent ? cardMemoTitleProblem(draft.title) : null
  if (title) return { field: 'title', key: `field.title.${title}`, message: '', reread: false }
  const body = !sent || 'body' in sent ? cardMemoBodyProblem(draft.body) : null
  if (body) return { field: 'body', key: `field.body.${body}`, message: '', reread: false }
  return null
}

async function save(win: CardMemoWindow): Promise<void> {
  if (win.busy || locked(win) || win.gone) return
  if (win.memoId === null) {
    const problem = problemOf(win.draft)
    if (problem) {
      win.failure = problem
      return
    }
    win.failure = null
    win.busy = true
    const answer = await cardMemoWriteCreate(win.card, { title: win.draft.title.trim(), body: win.draft.body, aiRef: win.draft.aiRef })
    win.busy = false
    if (answer.ok) {
      win.memoId = answer.value.memoId
      win.memo = answer.value
      win.draft.title = answer.value.title
    } else win.failure = answer.failure
    return
  }
  const changes = win.memo ? cardMemoChanges(win.memo, win.draft) : null
  if (!changes) return
  const problem = problemOf(win.draft, changes)
  if (problem) {
    win.failure = problem
    return
  }
  win.failure = null
  win.busy = true
  const answer = await cardMemoWriteUpdate(win.card, win.memoId, changes)
  win.busy = false
  if (answer.ok) {
    win.memo = answer.value
    win.draft.title = answer.value.title
  } else {
    win.failure = answer.failure
    if (answer.failure.key === 'memo_not_found') win.gone = true
  }
}

async function confirmDelete(win: CardMemoWindow): Promise<void> {
  if (win.busy || win.memoId === null || locked(win)) return
  win.busy = true
  const answer = await cardMemoWriteRemove(win.card, win.memoId)
  win.busy = false
  // A memo already gone elsewhere is gone all the same.
  if (answer.ok || answer.failure.key === 'memo_not_found') {
    closeCardMemoWindow(win)
    return
  }
  win.confirming = false
  win.failure = answer.failure
}
</script>

<style scoped>
.card-memo-window-heading {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  max-width: 100%;
}

.card-memo-window-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.card-memo-window-chip {
  flex: 0 0 auto;
  padding: 1px 7px;
  border: 1px solid var(--fg2-status-info-border);
  border-radius: var(--fg2-control-radius-pill);
  background: var(--fg2-status-info-bg);
  color: var(--fg2-status-info-fg);
  font-size: 10.5px;
  font-weight: 700;
}

.card-memo-window {
  display: flex;
  flex-direction: column;
  gap: 10px;
  height: 100%;
  min-height: 0;
}

.card-memo-window-field {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.card-memo-window-field--grow {
  flex: 1;
  min-height: 0;
}

.card-memo-window-field--grow .card-memo-window-body {
  flex: 1;
  min-height: 96px;
}

/* A memo opened to read: its body as written, scrolling inside the window. */
.card-memo-window-read {
  flex: 1;
  min-height: 0;
  overflow: auto;
  color: var(--fg2-text);
  font-size: 12.5px;
  line-height: 1.6;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.card-memo-window-label {
  display: flex;
  justify-content: space-between;
  color: var(--fg2-muted);
  font-size: 11.5px;
  font-weight: 650;
}

.card-memo-window-length {
  font-weight: 500;
}

.card-memo-window-length.is-over,
.card-memo-window-error {
  color: var(--fg2-status-danger-fg);
}

.card-memo-window-error {
  margin: -4px 0 0;
  font-size: 12px;
}

.card-memo-window-toggle {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  align-self: flex-start;
  color: var(--fg2-text);
  font-size: 12px;
  cursor: pointer;
}

.card-memo-window-note {
  margin: 0;
  color: var(--fg2-muted);
  font-size: 12px;
}

.card-memo-window-note.is-danger {
  color: var(--fg2-status-danger-fg);
}

.card-memo-window-confirm {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid #71313d;
  border-radius: 8px;
  background: #2a1820;
  color: #ffc2cc;
  font-size: 12px;
}

.card-memo-window-confirm > span {
  flex: 1;
}

.card-memo-window-bar {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  margin-top: auto;
  padding-top: 10px;
  border-top: 1px solid var(--fg2-line);
}

/* [삭제]: a soft red button — the strong red stays with the confirm step. */
.card-memo-window-bar .card-memo-window-delete {
  border-color: rgba(255, 110, 130, 0.45);
  background: rgba(225, 75, 96, 0.14);
  color: #ff9eae;
}

.card-memo-window-bar .card-memo-window-delete:hover:not(:disabled) {
  border-color: rgba(255, 130, 150, 0.7);
  background: rgba(225, 75, 96, 0.24);
  color: #ffd0d7;
}
</style>
