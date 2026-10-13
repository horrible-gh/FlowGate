<template>
  <!-- flowgate.v02.0056 F2-2 — the card's memos in its documents tab (O3 0009-TR §4-5; v0.2 memos, not the v0.1 M
       document): the list in written order (title · [AI가 참조] · time), [+] for a new memo, and one modeless window
       per memo — the memo as written with [편집] · [삭제], or its editor (title on one line · body · [AI가 참조]
       toggle, OFF by default). A stored card only; a read-only view lists without changing. -->
  <section v-if="card" class="inspector-card-memo" data-test="card-memo" :data-node="card.nodeKey" :data-status="status">
    <div class="inspector-card-memo-head">
      <span class="inspector-card-memo-label">{{ t(k('label')) }}</span>
      <span v-if="memos.length" class="inspector-card-memo-count" data-test="card-memo-count">{{ memos.length }}</span>
      <span class="inspector-card-memo-push" />
      <InspectorAddButton
        :label="t(k('add'))"
        :title="lockedReason ? t(k(lockedReason)) : t(k('add'))"
        :disabled="lockedReason !== null"
        data-test="card-memo-add"
        @click="openNew"
      />
    </div>

    <p v-if="status === 'loading'" class="inspector-card-memo-note" data-test="card-memo-loading">{{ t(k('loading')) }}</p>
    <p v-else-if="status === 'error'" class="inspector-card-memo-note is-danger" data-test="card-memo-read-error">
      <span>{{ readFailure ? failureText(readFailure) : t(k('failure.other')) }}</span>
      <Button size="sm" variant="secondary" data-test="card-memo-retry" @click="reload">{{ t(k('retry')) }}</Button>
    </p>
    <p v-else-if="!memos.length" class="inspector-card-memo-note" data-test="card-memo-empty">{{ t(k('empty')) }}</p>
    <ul v-else class="inspector-card-memo-list" data-test="card-memo-list">
      <li v-for="memo in memos" :key="memo.memoId" class="inspector-card-memo-item" data-test="card-memo-item" :data-memo="memo.memoId">
        <button type="button" class="inspector-card-memo-title" :title="memo.title" data-test="card-memo-open" @click="openMemo(memo)">{{ memo.title }}</button>
        <span v-if="memo.aiRef" class="inspector-card-memo-chip" :title="t(k('ai_ref_hint'))" data-test="card-memo-ai-ref">{{ t(k('ai_ref_on')) }}</span>
        <span class="inspector-card-memo-when" data-test="card-memo-when">{{ cardMemoWhen(memo.updatedAt ?? memo.createdAt) }}</span>
      </li>
    </ul>

    <ModelessShell
      v-for="win in windows"
      :key="win.id"
      :open="true"
      :initial-x="win.x"
      :initial-y="win.y"
      :initial-width="460"
      :initial-height="420"
      :min-width="320"
      :min-height="240"
      @close="close(win)"
    >
      <template #header>
        <span class="inspector-card-memo-window-heading">
          <span>{{ t(k('window.title')) }}</span>
          <span class="inspector-card-memo-window-sub" data-test="card-memo-window-sub">{{ win.memoId === null ? t(k('window.new')) : shownOf(win)?.title }}</span>
        </span>
      </template>
      <div class="inspector-card-memo-window" data-test="card-memo-window" :data-memo="win.memoId ?? ''" :data-mode="win.mode">
        <template v-if="win.mode === 'view'">
          <p v-if="win.gone || !shownOf(win)" class="inspector-card-memo-note is-danger" data-test="card-memo-gone">{{ t(k('failure.memo_not_found')) }}</p>
          <template v-else>
            <p class="inspector-card-memo-view-title" data-test="card-memo-view-title">{{ shownOf(win)!.title }}</p>
            <p class="inspector-card-memo-view-ai" :class="{ 'is-on': shownOf(win)!.aiRef }" data-test="card-memo-view-ai-ref">
              {{ t(k(shownOf(win)!.aiRef ? 'ai_ref_on' : 'ai_ref_off')) }}
            </p>
            <div class="inspector-card-memo-view-body" data-test="card-memo-view-body">{{ shownOf(win)!.body }}</div>
          </template>
          <div v-if="win.confirming" class="inspector-card-memo-confirm" data-test="card-memo-confirm">
            <span>{{ t(k('delete_confirm')) }}</span>
            <Button size="sm" variant="ghost" :disabled="win.busy" data-test="card-memo-confirm-cancel" @click="win.confirming = false">{{ t(k('cancel')) }}</Button>
            <Button size="sm" variant="danger" :loading="win.busy" data-test="card-memo-confirm-delete" @click="confirmDelete(win)">{{ t(k('delete')) }}</Button>
          </div>
          <p v-if="win.failure" class="inspector-card-memo-note is-danger" data-test="card-memo-failure" :data-key="win.failure.key">{{ failureText(win.failure) }}</p>
          <div class="inspector-card-memo-bar">
            <Button size="sm" variant="ghost" data-test="card-memo-close" @click="close(win)">{{ t(k('close')) }}</Button>
            <Button
              size="sm"
              variant="secondary"
              :disabled="locked(win) || win.gone || win.confirming || !shownOf(win)"
              :title="lockedReason ? t(k(lockedReason)) : undefined"
              data-test="card-memo-delete"
              @click="win.confirming = true"
            >{{ t(k('delete')) }}</Button>
            <Button
              size="sm"
              variant="primary"
              :disabled="locked(win) || win.gone || win.confirming || !shownOf(win)"
              :title="lockedReason ? t(k(lockedReason)) : undefined"
              data-test="card-memo-edit"
              @click="edit(win)"
            >{{ t(k('edit')) }}</Button>
          </div>
        </template>

        <template v-else>
          <label class="inspector-card-memo-field">
            <span class="inspector-card-memo-field-label">{{ t(k('field.title')) }}</span>
            <Input
              v-model="win.draft.title"
              :invalid="fieldText(win, 'title') !== ''"
              :disabled="win.busy"
              data-test="card-memo-title-input"
              @input="clearField(win, 'title')"
            />
          </label>
          <p v-if="fieldText(win, 'title')" class="inspector-card-memo-field-error" data-test="card-memo-title-error">{{ fieldText(win, 'title') }}</p>
          <label class="inspector-card-memo-field inspector-card-memo-field--grow">
            <span class="inspector-card-memo-field-label">
              {{ t(k('field.body')) }}
              <span class="inspector-card-memo-length" :class="{ 'is-over': cardMemoLength(win.draft.body) > CARD_MEMO_BODY_MAX }" data-test="card-memo-body-length">
                {{ t(k('length'), { n: cardMemoLength(win.draft.body), max: CARD_MEMO_BODY_MAX }) }}
              </span>
            </span>
            <Textarea
              v-model="win.draft.body"
              :rows="6"
              :invalid="fieldText(win, 'body') !== ''"
              :disabled="win.busy"
              data-test="card-memo-body-input"
              @input="clearField(win, 'body')"
            />
          </label>
          <p v-if="fieldText(win, 'body')" class="inspector-card-memo-field-error" data-test="card-memo-body-error">{{ fieldText(win, 'body') }}</p>
          <div class="inspector-card-memo-toggle">
            <Toggle v-model="win.draft.aiRef" :aria-label="t(k('ai_ref'))" :disabled="win.busy" data-test="card-memo-ai-ref-toggle" />
            <span class="inspector-card-memo-toggle-label">{{ t(k('ai_ref')) }}</span>
          </div>
          <p class="inspector-card-memo-hint" data-test="card-memo-ai-ref-hint">{{ t(k('ai_ref_hint')) }}</p>
          <p v-if="win.failure && !win.failure.field" class="inspector-card-memo-note is-danger" data-test="card-memo-failure" :data-key="win.failure.key">
            {{ failureText(win.failure) }}
          </p>
          <div class="inspector-card-memo-bar">
            <Button size="sm" variant="ghost" :disabled="win.busy" data-test="card-memo-cancel" @click="cancel(win)">{{ t(k('cancel')) }}</Button>
            <Button size="sm" variant="primary" :loading="win.busy" :disabled="locked(win) || win.gone" data-test="card-memo-save" @click="save(win)">{{ t(k('save')) }}</Button>
          </div>
        </template>
      </div>
    </ModelessShell>
  </section>
</template>

<script setup lang="ts">
// flowgate.v02.0056 F2-2 — display of the card memos (useCardMemo reads and writes; cardMemoModel holds the rules).
// The windows are this part's own (several may be open, each keeps the card it was opened for); a memo window
// shows the memo as the list has it now, or as it was answered when its card is no longer the selected one.
import { reactive } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '../../../../../components/ui/Button.vue'
import Input from '../../../../../components/ui/Input.vue'
import ModelessShell from '../../../../../components/ui/ModelessShell.vue'
import Textarea from '../../../../../components/ui/Textarea.vue'
import Toggle from '../../../../../components/ui/Toggle.vue'
import InspectorAddButton from '../../parts/InspectorAddButton.vue'
import type { InspectorTarget } from '../../inspectorTarget'
import {
  CARD_MEMO_BODY_MAX,
  cardMemoBodyProblem,
  cardMemoChanges,
  cardMemoDraftOf,
  cardMemoLength,
  cardMemoTitleProblem,
  cardMemoWhen,
  type CardMemoDraft,
  type CardMemoFacts,
  type CardMemoFailure,
  type CardMemoWindowMode,
} from './cardMemoModel'
import { useCardMemo, type CardMemoCard } from './useCardMemo'

const props = defineProps<{
  target: InspectorTarget
}>()

const { t } = useI18n()
const k = (key: string) => `gui.inspector.cardMemo.${key}`

const { card, memos, status, readFailure, lockedReason, reload, create, update, remove } = useCardMemo(() => props.target)

interface MemoWindow {
  readonly id: string
  readonly card: CardMemoCard
  memoId: string | null
  /** The memo as last answered (shown when its card is no longer the selected one). */
  memo: CardMemoFacts | null
  mode: CardMemoWindowMode
  draft: { title: string; body: string; aiRef: boolean }
  busy: boolean
  failure: CardMemoFailure | null
  confirming: boolean
  /** The memo is gone (deleted elsewhere). */
  gone: boolean
  readonly x: number
  readonly y: number
}

const windows = reactive<MemoWindow[]>([])
let opened = 0

function placed(): { x: number; y: number } {
  const offset = (opened++ % 6) * 28
  return { x: 320 + offset, y: 140 + offset }
}

const sameCard = (a: CardMemoCard, b: CardMemoCard | null) => !!b && a.flowId === b.flowId && a.nodeKey === b.nodeKey

/** The memo a window shows: the list's while its card is selected, else the last answer. */
function shownOf(win: MemoWindow): CardMemoFacts | null {
  if (win.memoId === null) return null
  if (sameCard(win.card, card.value) && status.value === 'ready') return memos.value.find((memo) => memo.memoId === win.memoId) ?? (win.gone ? null : win.memo)
  return win.memo
}

/** The window's card cannot be changed here (the selected view is read only). */
function locked(win: MemoWindow): boolean {
  return sameCard(win.card, card.value) && lockedReason.value !== null
}

function failureText(failure: CardMemoFailure): string {
  const words = t(k(`failure.${failure.key}`))
  return failure.message ? `${words} — ${failure.message}` : words
}

/** A new edit of a field: an earlier refusal of that field no longer stands. */
function clearField(win: MemoWindow, field: 'title' | 'body'): void {
  if (win.failure?.field === field) win.failure = null
}

/** The refusal under a field: the server's, else what the editor sees itself. */
function fieldText(win: MemoWindow, field: 'title' | 'body'): string {
  if (win.failure?.field === field) return failureText(win.failure)
  const problem = field === 'title' ? cardMemoTitleProblem(win.draft.title) : cardMemoBodyProblem(win.draft.body)
  // A title not written yet is not an error until [저장] is pressed (the refusal above says it then).
  if (!problem || problem === 'required') return ''
  return t(k(`failure.field.${field}.${problem}`))
}

function openNew(): void {
  if (!card.value || lockedReason.value !== null) return
  windows.push({ id: `new:${opened}`, card: card.value, memoId: null, memo: null, mode: 'edit', draft: { ...cardMemoDraftOf(null) }, busy: false, failure: null, confirming: false, gone: false, ...placed() })
}

function openMemo(memo: CardMemoFacts): void {
  if (!card.value) return
  const open = windows.find((win) => win.memoId === memo.memoId && sameCard(win.card, card.value))
  if (open) return
  windows.push({ id: `memo:${memo.memoId}:${opened}`, card: card.value, memoId: memo.memoId, memo, mode: 'view', draft: { ...cardMemoDraftOf(memo) }, busy: false, failure: null, confirming: false, gone: false, ...placed() })
}

function close(win: MemoWindow): void {
  const index = windows.indexOf(win)
  if (index !== -1) windows.splice(index, 1)
}

function edit(win: MemoWindow): void {
  const shown = shownOf(win)
  if (!shown) return
  win.draft = { ...cardMemoDraftOf(shown) }
  win.failure = null
  win.mode = 'edit'
}

function cancel(win: MemoWindow): void {
  if (win.memoId === null) close(win)
  else {
    win.failure = null
    win.mode = 'view'
  }
}

/**
 * A refusal the editor sees before sending, as the server would word it. `sent` names the fields a change sends:
 * a field kept as the server stored it is not checked again (changing only [AI가 참조] is never refused here).
 */
function problemOf(draft: CardMemoDraft, sent?: { title?: string; body?: string }): CardMemoFailure | null {
  const title = !sent || 'title' in sent ? cardMemoTitleProblem(draft.title) : null
  if (title) return { field: 'title', key: `field.title.${title}`, message: '', reread: false }
  const body = !sent || 'body' in sent ? cardMemoBodyProblem(draft.body) : null
  if (body) return { field: 'body', key: `field.body.${body}`, message: '', reread: false }
  return null
}

async function save(win: MemoWindow): Promise<void> {
  if (win.busy || locked(win)) return
  if (win.memoId === null) {
    const problem = problemOf(win.draft)
    if (problem) {
      win.failure = problem
      return
    }
    win.failure = null
    win.busy = true
    const answer = await create(win.card, { title: win.draft.title.trim(), body: win.draft.body, aiRef: win.draft.aiRef })
    win.busy = false
    if (answer.ok) {
      win.memoId = answer.value.memoId
      win.memo = answer.value
      win.mode = 'view'
    } else win.failure = answer.failure
    return
  }
  const shown = shownOf(win)
  const changes = shown ? cardMemoChanges(shown, win.draft) : null
  if (!changes) {
    win.failure = null
    win.mode = 'view'
    return
  }
  const problem = problemOf(win.draft, changes)
  if (problem) {
    win.failure = problem
    return
  }
  win.failure = null
  win.busy = true
  const answer = await update(win.card, win.memoId, changes)
  win.busy = false
  if (answer.ok) {
    win.memo = answer.value
    win.mode = 'view'
  } else {
    win.failure = answer.failure
    if (answer.failure.key === 'memo_not_found') win.gone = true
  }
}

async function confirmDelete(win: MemoWindow): Promise<void> {
  if (win.busy || win.memoId === null || locked(win)) return
  win.busy = true
  const answer = await remove(win.card, win.memoId)
  win.busy = false
  // A memo already gone elsewhere is gone all the same.
  if (answer.ok || answer.failure.key === 'memo_not_found') {
    close(win)
    return
  }
  win.confirming = false
  win.failure = answer.failure
}
</script>

<style scoped>
.inspector-card-memo {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.inspector-card-memo-head {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.inspector-card-memo-label {
  color: var(--fg2-text);
  font-size: 12px;
  font-weight: 750;
}

.inspector-card-memo-count {
  color: var(--fg2-muted);
  font-size: 11.5px;
}

.inspector-card-memo-push {
  flex: 1;
}

.inspector-card-memo-note {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0;
  color: var(--fg2-muted);
  font-size: 12px;
}

.inspector-card-memo-note.is-danger {
  color: var(--fg2-status-danger-fg);
}

.inspector-card-memo-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.inspector-card-memo-item {
  display: flex;
  align-items: center;
  gap: 8px;
  box-sizing: border-box;
  min-width: 0;
  height: 32px;
  padding: 0 10px;
  border: 1px solid var(--fg2-line);
  border-radius: 8px;
}

.inspector-card-memo-title {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  padding: 0;
  border: 0;
  background: none;
  color: var(--fg2-text);
  font: inherit;
  font-size: 12px;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
}

.inspector-card-memo-title:hover {
  text-decoration: underline;
  text-underline-offset: 3px;
}

.inspector-card-memo-title:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 2px;
}

.inspector-card-memo-chip {
  flex: 0 0 auto;
  padding: 1px 7px;
  border: 1px solid var(--fg2-status-info-border);
  border-radius: var(--fg2-control-radius-pill);
  background: var(--fg2-status-info-bg);
  color: var(--fg2-status-info-fg);
  font-size: 10.5px;
  font-weight: 700;
}

.inspector-card-memo-when {
  flex: 0 0 auto;
  color: var(--fg2-muted);
  font-size: 11px;
}

.inspector-card-memo-window-heading {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  max-width: 100%;
}

.inspector-card-memo-window-sub {
  overflow: hidden;
  color: var(--fg2-muted);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.inspector-card-memo-window {
  display: flex;
  flex-direction: column;
  gap: 8px;
  height: 100%;
  min-height: 0;
}

.inspector-card-memo-view-title {
  margin: 0;
  color: var(--fg2-text);
  font-size: 13px;
  font-weight: 700;
  overflow-wrap: anywhere;
}

.inspector-card-memo-view-ai {
  margin: 0;
  color: var(--fg2-muted);
  font-size: 11.5px;
}

.inspector-card-memo-view-ai.is-on {
  color: var(--fg2-status-info-fg);
}

.inspector-card-memo-view-body {
  flex: 1;
  min-height: 0;
  overflow: auto;
  color: var(--fg2-text);
  font-size: 12.5px;
  line-height: 1.6;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.inspector-card-memo-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.inspector-card-memo-field--grow {
  flex: 1;
  min-height: 0;
}

.inspector-card-memo-field-label {
  display: flex;
  justify-content: space-between;
  color: var(--fg2-muted);
  font-size: 12px;
  font-weight: 650;
}

.inspector-card-memo-length {
  font-weight: 500;
}

.inspector-card-memo-length.is-over,
.inspector-card-memo-field-error {
  color: var(--fg2-status-danger-fg);
}

.inspector-card-memo-field-error {
  margin: 0;
  font-size: 12px;
}

.inspector-card-memo-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
}

.inspector-card-memo-toggle-label {
  color: var(--fg2-text);
  font-size: 12px;
}

.inspector-card-memo-hint {
  margin: 0;
  color: var(--fg2-muted);
  font-size: 11.5px;
}

.inspector-card-memo-confirm {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--fg2-status-warning-fg);
  font-size: 12px;
}

.inspector-card-memo-bar {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  margin-top: auto;
}
</style>
