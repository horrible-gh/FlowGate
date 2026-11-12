<template>
  <!-- flowgate.v02.0031 — one user / AI message of the timeline (R0001 §4). -->
  <div
    ref="rootEl"
    class="tl-msg"
    :class="[isUser ? 'tl-msg--user' : 'tl-msg--ai', { 'tl-msg--system': isSystem, 'is-pending': pending, 'is-failed': failed }]"
    data-test="tl-message"
    :data-speaker="isUser ? 'user' : isSystem ? 'system' : 'ai'"
    :data-seq="turn.seq || undefined"
  >
    <div class="tl-bubble">
      <div class="tl-meta">
        <span class="tl-speaker">
          <span class="tl-avatar" aria-hidden="true">{{ isUser ? '●' : isSystem ? '■' : '◆' }}</span>
          {{ isUser ? t('gui.conversation.message.user') : isSystem ? t('gui.conversation.message.system') : t('gui.conversation.message.ai') }}
        </span>
        <!-- 0293 R0001: the model name is a badge only; identity is participant_key. -->
        <span v-if="!isUser && !isSystem && turn.displayName" class="tl-provider" data-test="tl-provider" :title="turn.displayName">{{ turn.displayName }}</span>
        <span v-if="pending" class="tl-time">{{ t('gui.conversation.message.sending') }}</span>
        <span v-else-if="turn.createdAt" class="tl-time" :title="turn.createdAt">{{ formatTimelineTime(turn.createdAt) }}</span>
        <!-- flowgate.v02.0157: the message as written (Markdown source, not the rendered page), on both sides. -->
        <button
          v-if="turn.body"
          type="button"
          class="tl-copy"
          :class="copyState === 'idle' ? null : `is-${copyState}`"
          data-test="tl-copy"
          :data-state="copyState"
          :aria-label="copyLabel"
          :title="copyLabel"
          @click="copyBody"
        >{{ copyState === 'done' ? '✓' : copyState === 'failed' ? '✕' : '⧉' }}</button>
        <span v-if="copyState !== 'idle'" class="tl-sr" role="status">{{ copyLabel }}</span>
        <!-- flowgate.v02test.0025: start this chat's AI context at this message (a stored message only). -->
        <button
          v-if="canCheckpoint && turn.seq > 0 && !pending"
          type="button"
          class="tl-copy tl-checkpoint"
          :class="{ 'is-on': isCheckpoint }"
          data-test="tl-checkpoint"
          :aria-pressed="isCheckpoint"
          :aria-label="isCheckpoint ? t('gui.conversation.checkpoint.clear_here') : t('gui.conversation.checkpoint.set_here')"
          :title="isCheckpoint ? t('gui.conversation.checkpoint.clear_here') : t('gui.conversation.checkpoint.set_here')"
          :disabled="checkpointBusy"
          @click="emit('checkpoint', isCheckpoint ? null : turn.seq)"
        >⚑</button>
      </div>
      <!-- A person's own input stays plain text; an AI's message is Markdown (tables, code blocks with copy) rendered by
           the shared renderer in safe mode: raw HTML is shown as text, unsafe links and every image as text only. -->
      <div v-if="isUser" class="tl-text">{{ turn.body }}</div>
      <div v-else class="tl-text tl-md" data-test="tl-md" @click.capture="onBodyClick" v-html="html" />
      <p v-if="failed" class="tl-note tl-note--failed">
        {{ t('gui.conversation.message.failed') }}
        <button v-if="!readOnly" type="button" class="tl-retry" data-test="tl-retry" @click="emit('retry')">{{ t('gui.conversation.retry') }}</button>
      </p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { copyToClipboard } from '../../core/interaction/clipboard'
import { createMarkdownRenderer } from '../../core/markdown/markdownRenderer'
import { formatTimelineTime } from './timeline'
import type { ConversationTurn } from './types'

const props = defineProps<{
  turn: ConversationTurn
  pending: boolean
  failed: boolean
  readOnly?: boolean
  /** flowgate.v02test.0025: the context checkpoint button is offered. */
  canCheckpoint?: boolean
  /** This message is the chat's context checkpoint. */
  isCheckpoint?: boolean
  checkpointBusy?: boolean
}>()

const emit = defineEmits<{ retry: []; checkpoint: [seq: number | null] }>()

const { t, locale } = useI18n()

const isUser = computed(() => props.turn.speaker === 'user')
// flowgate.v02test.0023: FlowGate's own turn (the chat commit result) -- shown as Markdown like an AI message, but
// labelled FlowGate so it is never taken for the AI's reply.
const isSystem = computed(() => props.turn.speaker === 'system')
const rootEl = ref<HTMLElement | null>(null)

// flowgate.v02.0157 — the run result window's renderer (GFM tables, a copy button per code block), made once per
// message as in that window. The computed caches the parse: it runs again only when the body or the language (the
// code copy label) changes.
const renderMarkdown = createMarkdownRenderer({ copyLabel: () => t('gui.document_window.viewer.copy_code'), safe: true })
const html = computed(() => {
  if (isUser.value) return ''
  void locale.value
  return renderMarkdown(props.turn.body)
})

type CopyState = 'idle' | 'done' | 'failed'
const COPY_FLASH_MS = 1500
const CODE_COPY_ICON = '⧉'

const copyState = ref<CopyState>('idle')
const copyLabel = computed(() =>
  copyState.value === 'done'
    ? t('gui.conversation.message.copied')
    : copyState.value === 'failed'
      ? t('gui.conversation.message.copy_failed')
      : t('gui.conversation.message.copy'),
)

let copyTimer: ReturnType<typeof setTimeout> | null = null
let copyAttempt = 0
const codeTimers = new Map<Element, ReturnType<typeof setTimeout>>()
const codeAttempts = new WeakMap<Element, number>()
// Bumped when the shown text changes (another turn, a new body, a new language) and when the message leaves: a copy
// still in flight then reports nowhere, so an earlier result never lands on the new state.
let generation = 0

function resetCodeButton(button: Element): void {
  button.textContent = CODE_COPY_ICON
  button.classList.remove('code-copy-btn--copied', 'code-copy-btn--failed')
}

function resetCopyFeedback(): void {
  generation += 1
  if (copyTimer !== null) clearTimeout(copyTimer)
  copyTimer = null
  copyState.value = 'idle'
  for (const [button, timer] of codeTimers) {
    clearTimeout(timer)
    resetCodeButton(button)
  }
  codeTimers.clear()
}

watch([() => props.turn.seq, () => props.turn.body, html], resetCopyFeedback)
onBeforeUnmount(resetCopyFeedback)

/** The whole message as written (Markdown source with its tables and code fences), never the rendered HTML. */
async function copyBody(): Promise<void> {
  const gen = generation
  const attempt = ++copyAttempt
  const copied = await copyToClipboard(props.turn.body, rootEl.value)
  // Only the latest click on the same text reports.
  if (gen !== generation || attempt !== copyAttempt) return
  copyState.value = copied ? 'done' : 'failed'
  if (copyTimer !== null) clearTimeout(copyTimer)
  copyTimer = setTimeout(() => {
    copyTimer = null
    copyState.value = 'idle'
  }, COPY_FLASH_MS)
}

/** A code block's copy button (the renderer's wrapper): only the code of the block that was pressed. */
async function onBodyClick(event: MouseEvent): Promise<void> {
  const button = (event.target as Element | null)?.closest?.('.code-copy-btn')
  if (!button) return
  event.stopPropagation()
  const code = button.closest('.code-block-wrapper')?.querySelector('code')
  if (!code) return
  const gen = generation
  const attempt = (codeAttempts.get(button) ?? 0) + 1
  codeAttempts.set(button, attempt)
  const copied = await copyToClipboard(code.textContent ?? '', rootEl.value)
  if (gen !== generation || codeAttempts.get(button) !== attempt || !button.isConnected) return
  const previous = codeTimers.get(button)
  if (previous !== undefined) clearTimeout(previous)
  resetCodeButton(button)
  button.textContent = copied ? '✓' : '✕'
  button.classList.add(copied ? 'code-copy-btn--copied' : 'code-copy-btn--failed')
  codeTimers.set(
    button,
    setTimeout(() => {
      codeTimers.delete(button)
      resetCodeButton(button)
    }, COPY_FLASH_MS),
  )
}
</script>

<style scoped>
.tl-msg {
  display: flex;
  width: 100%;
}

.tl-msg--user {
  justify-content: flex-end;
}

.tl-bubble {
  max-width: 92%;
  min-width: 0;
  border: 1px solid var(--fg2-line);
  border-radius: var(--fg2-control-radius);
  background: var(--fg2-control-surface);
  padding: 7px 8px 8px;
  color: var(--fg2-control-fg);
}

.tl-msg--user .tl-bubble {
  border-color: var(--fg2-status-info-border);
  background: var(--fg2-status-info-bg);
  color: var(--fg2-text);
}

.tl-msg.is-pending .tl-bubble {
  opacity: 0.7;
}

.tl-msg.is-failed .tl-bubble {
  border-color: var(--fg2-status-danger-border);
}

/* One line that never widens the bubble: the model badge gives way first (ellipsis); the rest keep their size. */
.tl-meta {
  min-width: 0;
  min-height: 18px;
  display: flex;
  align-items: center;
  gap: 5px;
  margin-bottom: 3px;
  color: var(--fg2-muted);
  font-size: 10px;
  white-space: nowrap;
}

.tl-speaker {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 4px;
  font-weight: 700;
  color: var(--fg2-text);
}

.tl-avatar {
  width: 15px;
  height: 15px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  background: var(--fg2-control-surface-strong);
  color: var(--fg2-status-info-fg);
  font-size: 8px;
}

.tl-msg--ai .tl-avatar {
  color: var(--fg2-violet);
}

.tl-msg--system .tl-bubble {
  border-style: dashed;
  background: transparent;
}

.tl-msg--system .tl-avatar {
  color: var(--fg2-muted);
}

.tl-provider {
  min-width: 0;
  max-width: 110px;
  overflow: hidden;
  text-overflow: ellipsis;
  padding: 1px 5px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius-pill);
  color: var(--fg2-muted);
}

.tl-time {
  flex-shrink: 0;
  margin-left: auto;
  color: var(--fg2-muted-2);
}

.tl-copy {
  flex-shrink: 0;
  margin-left: auto;
  width: 18px;
  height: 18px;
  padding: 0;
  display: grid;
  place-items: center;
  border: 1px solid transparent;
  border-radius: var(--fg2-control-radius-sm);
  background: transparent;
  color: var(--fg2-muted);
  font: inherit;
  font-size: 11px;
  line-height: 1;
  cursor: pointer;
}

/* The time already takes the free space; the copy button then sits right after it. */
.tl-time + .tl-copy {
  margin-left: 0;
}

.tl-copy:hover,
.tl-copy:focus-visible {
  border-color: var(--fg2-line-strong);
  color: var(--fg2-text);
}

.tl-copy.is-done {
  color: var(--fg2-status-success-fg);
}

.tl-copy.is-failed {
  color: var(--fg2-status-danger-fg);
}

.tl-copy + .tl-checkpoint,
.tl-sr + .tl-checkpoint {
  margin-left: 0;
}

.tl-checkpoint.is-on {
  color: var(--fg2-status-info-fg);
  border-color: var(--fg2-status-info-border);
}

.tl-checkpoint:disabled {
  cursor: default;
  opacity: 0.5;
}

.tl-sr {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}

.tl-text {
  font-size: 12px;
  line-height: 1.48;
  white-space: pre-wrap;
  word-break: break-word;
}

/* flowgate.v02.0157 — the rendered AI message. HTML carries its own line breaks, so pre-wrap is not kept here (the
   user's plain text keeps it). Wide tables and long code lines scroll inside the bubble and never widen the panel. */
.tl-md {
  min-width: 0;
  white-space: normal;
  word-break: normal;
  overflow-wrap: break-word;
}

.tl-md :deep(> :first-child) {
  margin-top: 0;
}

.tl-md :deep(> :last-child) {
  margin-bottom: 0;
}

.tl-md :deep(p) {
  margin: 0 0 0.6em;
}

.tl-md :deep(h1),
.tl-md :deep(h2),
.tl-md :deep(h3),
.tl-md :deep(h4),
.tl-md :deep(h5),
.tl-md :deep(h6) {
  margin: 0.8em 0 0.4em;
  font-size: 12.5px;
  font-weight: 700;
  color: var(--fg2-text);
}

.tl-md :deep(ul),
.tl-md :deep(ol) {
  margin: 0 0 0.6em;
  padding-left: 1.4em;
}

.tl-md :deep(ul) {
  list-style: disc;
}

.tl-md :deep(ol) {
  list-style: decimal;
}

.tl-md :deep(li > ul),
.tl-md :deep(li > ol) {
  margin-bottom: 0;
}

.tl-md :deep(blockquote) {
  margin: 0 0 0.6em;
  padding-left: 8px;
  border-left: 2px solid var(--fg2-line-strong);
  color: var(--fg2-muted);
}

.tl-md :deep(a) {
  color: var(--fg2-blue);
}

.tl-md :deep(hr) {
  border: 0;
  border-top: 1px solid var(--fg2-line);
  margin: 0.8em 0;
}

.tl-md :deep(code) {
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
}

.tl-md :deep(:not(pre) > code) {
  padding: 0 3px;
  border-radius: 3px;
  background: var(--fg2-control-surface-strong);
  overflow-wrap: anywhere;
}

.tl-md :deep(table) {
  display: block;
  max-width: 100%;
  overflow-x: auto;
  border-collapse: collapse;
  margin: 0 0 0.6em;
  font-size: 11.5px;
}

.tl-md :deep(th),
.tl-md :deep(td) {
  border: 1px solid var(--fg2-line-strong);
  padding: 3px 6px;
  text-align: left;
  vertical-align: top;
}

.tl-md :deep(th) {
  background: var(--fg2-control-surface-flat);
  color: var(--fg2-text);
  font-weight: 600;
}

.tl-md :deep(.code-block-wrapper) {
  position: relative;
  margin: 0 0 0.6em;
}

.tl-md :deep(pre) {
  margin: 0;
  padding: 7px 30px 7px 9px;
  overflow-x: auto;
  white-space: pre;
  word-break: normal;
  overflow-wrap: normal;
  background: var(--fg2-bg);
  border: 1px solid var(--fg2-line);
  border-radius: var(--fg2-control-radius);
  line-height: 1.45;
}

.tl-md :deep(.code-copy-btn) {
  position: absolute;
  top: 4px;
  right: 4px;
  padding: 1px 6px;
  font-size: 11px;
  color: var(--fg2-muted);
  background: var(--fg2-panel);
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius-sm);
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.15s, color 0.15s;
}

/* Shown on hover and on keyboard focus (not hover-only), and while it reports a result. */
.tl-md :deep(.code-block-wrapper:hover .code-copy-btn),
.tl-md :deep(.code-copy-btn:focus-visible) {
  opacity: 1;
}

.tl-md :deep(.code-copy-btn--copied) {
  color: var(--fg2-status-success-fg);
  border-color: var(--fg2-status-success-border);
  opacity: 1;
}

.tl-md :deep(.code-copy-btn--failed) {
  color: var(--fg2-status-danger-fg);
  border-color: var(--fg2-status-danger-border);
  opacity: 1;
}

.tl-note {
  margin: 5px 0 0;
  font-size: 10px;
  color: var(--fg2-status-warning-fg);
}

.tl-note--failed {
  color: var(--fg2-status-danger-fg);
}

.tl-retry {
  margin-left: 6px;
  border: 0;
  background: transparent;
  color: var(--fg2-status-info-fg);
  font: inherit;
  text-decoration: underline;
  cursor: pointer;
}
</style>
