<template>
  <ModelessShell
    :open="open && result !== null"
    :title="t('gui.runResult.detail_title')"
    :initial-x="initialX"
    :initial-y="initialY"
    :initial-width="initialWidth"
    :initial-height="initialHeight"
    :min-width="360"
    :min-height="320"
    @close="emit('close')"
  >
    <section v-if="result" class="run-detail" data-test="run-result-detail-window" :data-state="result.state">
      <div class="run-detail-summary" data-test="run-result-summary">{{ summaryLine(tr, has, result) }}</div>
      <div class="run-detail-meta">
        <div v-for="role in ROLES" :key="role" class="run-detail-meta-item" :data-role="role">
          <span class="run-detail-meta-k">{{ t(`gui.runResult.ai.${role}`) }}</span>
          <span class="run-detail-meta-v" :data-test="`run-result-ai-${role}`">{{ roleUseLabel(tr, has, result.roles[role]) }}</span>
        </div>
      </div>
      <div class="run-detail-toolbar">
        <span data-test="run-result-message-count">{{ t('gui.runResult.messages', { n: result.messages.length }) }}</span>
        <span class="run-detail-copy">
          <span v-if="copyState !== 'idle'" class="run-detail-copy-state" :class="`is-${copyState}`" role="status">
            {{ t(copyState === 'done' ? 'gui.runResult.copied' : 'gui.runResult.copy_failed') }}
          </span>
          <Button size="sm" variant="secondary" data-test="run-result-copy" @click="copyAll">{{ t('gui.runResult.copy_all') }}</Button>
        </span>
      </div>
      <div class="run-detail-messages" data-test="run-result-messages" @click.capture="onContentClick">
        <p v-if="result.messages.length === 0" class="run-detail-empty">{{ t('gui.runResult.empty') }}</p>
        <article
          v-for="message in result.messages"
          :key="message.key"
          class="run-detail-message"
          :class="{ 'is-error': message.error, 'is-event': message.kind === 'event' }"
          :data-kind="message.kind"
          data-test="run-result-message"
        >
          <header class="run-detail-message-head">
            <span class="run-detail-role">{{ roleLabel(tr, has, message) }}</span>
            <span v-if="message.title" class="run-detail-card">{{ message.title }}</span>
            <span v-if="message.kind === 'invocation'" class="run-detail-model" data-test="run-result-model">{{ modelLabel(tr, message) }}</span>
            <span class="run-detail-time">{{ timeLabel(message.at, String(locale)) }}</span>
          </header>
          <!-- A body that is not this launch's own output says so (never another launch's output shown as its). -->
          <p v-if="bodyNote(tr, message)" class="run-detail-note" :data-source="message.bodySource" data-test="run-result-body-note">{{ bodyNote(tr, message) }}</p>
          <!-- The AI's message rendered as Markdown with the document viewer's renderer (raw HTML shown as text). -->
          <div v-if="message.kind === 'invocation' && message.body" class="run-detail-body md-body" v-html="render(message.body)" />
          <p v-else-if="message.kind === 'event'" class="run-detail-event">{{ eventLine(tr, has, message) }}</p>
          <div v-if="message.errorText" class="run-detail-error" data-test="run-result-error">
            <span class="run-detail-error-head">{{ t('gui.runResult.error_head') }}</span>
            <pre>{{ message.errorText }}</pre>
          </div>
        </article>
      </div>
    </section>
  </ModelessShell>
</template>

<script setup lang="ts">
// flowgate.v02.0113 T#5 (0030-M §5-3, D 개정 §3.14-3 / §6.5) — the 2nd run result window, opened from [상세]: the summary
// line (state · steps · scope), the instruction / execution / review AIs the command used, "메시지 N개" with [전체 복사],
// and one card per message — its role, card, model (requested → confirmed) and time over its body, which is Markdown
// rendered by the document viewer's renderer (headings, lists, code blocks, line breaks and paragraphs kept; raw HTML in an
// AI's message is shown as text). The full error of a failed launch or step is kept here — each launch's own; a launch
// whose own output was not kept says so, and the AI run's final output shown on its last launch is labelled as such. Everything scrolls inside the
// window, thousands of lines and many messages alike (each card lays out only when near the view). ModelessShell, as the
// 1st window; the wire is the 0029-TR mock's detailWindow.
import { ref, shallowRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '@/components/ui/Button.vue'
import ModelessShell from '@/components/ui/ModelessShell.vue'
import { copyToClipboard } from '@/core/interaction/clipboard'
import { createMarkdownRenderer } from '@/core/markdown/markdownRenderer'
import type { FlowCommandResult } from '@/features/project'
import { installRunResultMessages } from './messages'
import { bodyNote, copyText, eventLine, modelLabel, roleLabel, roleUseLabel, summaryLine, timeLabel, type HasKey, type Translate } from './runResultView'

const props = defineProps<{ result: FlowCommandResult | null; open: boolean }>()
const emit = defineEmits<{ close: [] }>()

const ROLES = ['instruction', 'execution', 'review'] as const

const composer = useI18n({ useScope: 'global' })
installRunResultMessages(composer)
const { t, te, locale } = composer
const tr: Translate = (key, values) => (values ? t(key, values) : t(key))
const has: HasKey = (key) => te(key)

const viewportWidth = typeof window === 'undefined' ? 1280 : window.innerWidth
const viewportHeight = typeof window === 'undefined' ? 800 : window.innerHeight
const initialWidth = Math.max(360, Math.min(780, viewportWidth - 30))
const initialHeight = Math.max(320, Math.min(690, viewportHeight - 40))
const initialX = Math.max(8, Math.round(viewportWidth / 2 - initialWidth / 2 + 40))
const initialY = Math.max(8, Math.round(viewportHeight * 0.08))

const renderer = createMarkdownRenderer({ copyLabel: () => t('gui.document_window.viewer.copy_code'), safe: true })
// One parse per body and language: a long body is never parsed again on a re-render.
const rendered = shallowRef(new Map<string, string>())
watch([() => props.result, locale], () => { rendered.value = new Map() })
function render(body: string): string {
  let html = rendered.value.get(body)
  if (html === undefined) {
    html = renderer(body)
    rendered.value.set(body, html)
  }
  return html
}

type CopyState = 'idle' | 'done' | 'failed'
const copyState = ref<CopyState>('idle')
let copyTimer: ReturnType<typeof setTimeout> | null = null

async function copyAll() {
  if (!props.result) return
  const copied = await copyToClipboard(copyText(tr, has, props.result, String(locale.value)))
  copyState.value = copied ? 'done' : 'failed'
  if (copyTimer !== null) clearTimeout(copyTimer)
  copyTimer = setTimeout(() => { copyState.value = 'idle' }, 1500)
}

/** A code block's copy button (the renderer's wrapper), as in the document viewer. */
async function onContentClick(event: MouseEvent) {
  const button = (event.target as Element | null)?.closest?.('.code-copy-btn')
  if (!button) return
  event.stopPropagation()
  const code = button.closest('.code-block-wrapper')?.querySelector('code')
  const copied = await copyToClipboard(code?.textContent ?? '')
  button.textContent = copied ? '✓' : '✕'
  setTimeout(() => { button.textContent = '⧉' }, 1500)
}
</script>

<style scoped>
.run-detail { display: flex; flex-direction: column; min-height: 100%; padding: 14px 16px; box-sizing: border-box; color: var(--fg2-text); font-size: 13px; }
.run-detail-summary { margin-bottom: 12px; line-height: 1.6; }
.run-detail-meta { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin-bottom: 14px; }
.run-detail-meta-item { min-width: 0; padding: 8px 10px; border: 1px solid var(--fg2-line); border-radius: var(--fg2-control-radius); background: var(--fg2-control-surface-flat); }
.run-detail-meta-k { display: block; margin-bottom: 5px; color: var(--fg2-muted); font-size: 10px; }
.run-detail-meta-v { display: block; font-size: 12px; overflow-wrap: anywhere; }
.run-detail-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 9px 2px; margin-bottom: 12px;
  border-top: 1px solid var(--fg2-line); border-bottom: 1px solid var(--fg2-line); font-weight: 700; font-size: 12px; }
.run-detail-copy { display: inline-flex; align-items: center; gap: 8px; }
.run-detail-copy-state { font-weight: 400; color: var(--fg2-muted); }
.run-detail-copy-state.is-done { color: var(--fg2-status-success-fg); }
.run-detail-copy-state.is-failed { color: var(--fg2-status-danger-fg); }
.run-detail-empty { color: var(--fg2-muted); text-align: center; padding: 20px; }
.run-detail-message { margin: 0 0 12px; border: 1px solid var(--fg2-line); border-radius: 8px; overflow: hidden; background: var(--fg2-panel);
  content-visibility: auto; contain-intrinsic-size: auto 160px; }
.run-detail-message.is-event { background: transparent; }
.run-detail-message.is-error { border-color: var(--fg2-status-danger-border, #7a3434); }
.run-detail-message-head { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 8px 12px; border-bottom: 1px solid var(--fg2-line);
  background: var(--fg2-control-surface-flat); }
.run-detail-role { font-weight: 800; font-size: 12px; }
.run-detail-card { font-size: 12px; color: var(--fg2-text); }
.run-detail-model { font-size: 11px; color: var(--fg2-muted); border: 1px solid var(--fg2-line-strong); border-radius: 4px; padding: 2px 6px; overflow-wrap: anywhere; }
.run-detail-time { margin-left: auto; font-size: 10px; color: var(--fg2-muted); }
.run-detail-body, .run-detail-event { margin: 0; padding: 12px 14px; line-height: 1.75; overflow-wrap: anywhere; word-break: break-word; user-select: text; }
.run-detail-event { font-size: 12px; color: var(--fg2-muted); }
.run-detail-note { margin: 0; padding: 8px 14px 0; font-size: 11px; color: var(--fg2-muted); font-style: italic; }
.run-detail-error { padding: 0 14px 12px; }
.run-detail-error-head { display: block; margin: 8px 0 4px; font-size: 11px; color: var(--fg2-status-danger-fg, #f0a2a2); }
.run-detail-error pre { margin: 0; padding: 8px 10px; white-space: pre-wrap; word-break: break-word; font-family: 'JetBrains Mono', monospace; font-size: 12px;
  border: 1px solid var(--fg2-line); border-radius: var(--fg2-control-radius); background: var(--fg2-bg); }
.md-body :deep(h1), .md-body :deep(h2), .md-body :deep(h3), .md-body :deep(h4) { color: var(--fg2-text); margin: 1em 0 0.5em; }
.md-body :deep(> :first-child) { margin-top: 0; }
.md-body :deep(p) { margin: 0 0 0.8em; }
.md-body :deep(ul), .md-body :deep(ol) { margin: 0 0 0.8em; padding-left: 1.6em; }
.md-body :deep(ul) { list-style: disc; }
.md-body :deep(ol) { list-style: decimal; }
.md-body :deep(pre) { background: var(--fg2-bg); border: 1px solid var(--fg2-line); border-radius: var(--fg2-control-radius); padding: 10px 14px;
  font-family: 'JetBrains Mono', monospace; font-size: 12px; white-space: pre-wrap; word-break: break-word; }
.md-body :deep(code) { font-family: 'JetBrains Mono', monospace; font-size: 12px; }
.md-body :deep(table) { table-layout: fixed; width: 100%; border-collapse: collapse; margin: 0 0 1em; }
.md-body :deep(th), .md-body :deep(td) { border: 1px solid var(--fg2-line-strong); padding: 4px 8px; overflow-wrap: anywhere; }
.md-body :deep(.code-block-wrapper) { position: relative; }
.md-body :deep(.code-copy-btn) { position: absolute; top: 6px; right: 8px; padding: 2px 8px; font-size: 12px; color: var(--fg2-muted); background: var(--fg2-panel);
  border: 1px solid var(--fg2-line-strong); border-radius: var(--fg2-control-radius-sm); cursor: pointer; }
</style>
