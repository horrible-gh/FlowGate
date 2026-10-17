<template>
  <div class="md-viewer" data-test="md-viewer" @scroll.passive="onViewerScroll">
    <div v-if="loading" class="md-viewer__loading" data-test="md-viewer-loading">{{ t(k('loading')) }}</div>
    <div v-else-if="error" class="md-viewer__error" data-test="md-viewer-error">
      <span>{{ t(k('error')) }}</span>
      <IconButton :label="t(k('retry'))" :title="t(k('retry'))" variant="secondary" size="sm" data-test="md-viewer-retry" @click="loadContent()">↻</IconButton>
    </div>
    <div v-else-if="unreadableReason" class="md-viewer__unreadable" role="alert" data-test="md-viewer-unreadable">
      <p>⚠ {{ t(`gui.document.bodyState.${unreadableReason}`) }}</p>
      <p>{{ t('gui.document.bodyState.aiUnreadable') }}</p>
      <p v-if="canReenter && recoverGuide" data-test="md-viewer-recover-guide">{{ t(recoverGuide) }}</p>
      <div v-if="canReenter" class="md-viewer__actions">
        <IconButton v-if="recoverMethod" :label="t('gui.document.recover.action')" :title="t('gui.document.recover.action')" variant="secondary" size="sm" :disabled="recovering" data-test="md-viewer-recover" @click="recover">{{ t('gui.document.recover.action') }}</IconButton>
        <IconButton :label="t('gui.document.bodyState.reenter')" :title="t('gui.document.bodyState.reenter')" variant="secondary" size="sm" data-test="md-viewer-reenter" @click="emit('reenter')">{{ t('gui.document.bodyState.reenter') }}</IconButton>
      </div>
      <p v-if="recoverNotice" data-test="md-viewer-recover-notice">{{ t(recoverNotice) }}</p>
    </div>
    <template v-else>
      <p v-if="recoverNotice" class="md-viewer__notice" role="status" data-test="md-viewer-recover-notice">{{ t(recoverNotice) }}</p>
      <Teleport :to="copyTarget ?? 'body'" :disabled="!copyTarget">
        <span v-if="hasBody" class="md-viewer__toolbar" :class="{ 'md-viewer__toolbar--titlebar': copyTarget }">
          <IconButton
            :label="t(k('copy_md'))"
            :title="copyMdState === 'failed' ? t(k('copy_failed')) : t(k('copy_md'))"
            variant="ghost"
            size="sm"
            :class="copyStateClass(copyMdState)"
            data-test="md-viewer-copy"
            @mousedown.stop
            @click="copyMarkdown"
          >{{ copyGlyph(copyMdState, '⧉') }}</IconButton>
        </span>
      </Teleport>
      <div class="md-viewer__content" data-test="md-viewer-content" v-html="renderedContent" @click.capture="handleContentClick" />
    </template>
  </div>
</template>

<script setup lang="ts">
// flowgate.v02.0053 T#3 — the v0.1 MD Viewer (client/src/main/components/MdViewer.vue) moved into
// the v0.2 document window. Kept from v0.1: the GFM renderer with its code-block copy buttons, the
// frontmatter / next-header strip before rendering, the parse diagnostics, the per-call content
// apply (no shared cause between overlapping loads), the locale re-render, the debounced reload on
// `fg:document_content_changed` and `loadContent` / `content` exposed to the parent.
// The same debounced reload is exposed as `requestContentRefresh`: in v0.2 nothing sends the v0.1
// window event, so the document window calls it on its document's project hub events (T#3 rework).
// Changed for v0.2:
// - Documents only: the body is read through `@/features/document` (documentContentRead). The v0.1
//   source-file and Git-branch blob modes stay in v0.1 (they need its explorer store).
// - Known unreadable body states show a warning and allow authorized users to re-enter the body.
//   flowgate.v02.0064 T-B1d (P0006 §6.2): the same users get [복구] with the method the body diagnosis of the
//   document's Flow shows (sent as `expected_method`); a method that cannot be recovered shows its guidance
//   instead, and the result says when the body may be (or is) lost.
// - The copy button is an icon with a tooltip (⧉ the body without its frontmatter); a failed copy marks
//   the button instead of the v0.1 fallback modal. Strings live under `gui.document_window.viewer`; colors
//   are the v0.2 dark theme. flowgate.v02.0056 F1 (0056 CH §5): the v0.1 "copy with header" (⎘, the
//   stored file with its frontmatter) is gone — the body copy is the one copy.
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Marked } from 'marked'
import { stripFrontmatter, stripLeadingNextHeader } from '@shared/utils/markdown'
import { recordMarkdownParse, snapshotRecoverySource } from '@shared/diagnostics/runtimeDiagnostics'
import type { MarkdownParseCause } from '@shared/diagnostics/runtimeDiagnostics'
import {
  bodyRecoveryDiagnose,
  bodyRecoveryFlowOf,
  bodyRecoveryRecover,
  bodyRecoveryRefusalOf,
  documentContentRead,
  type BodyRecoveryMethod,
} from '@/features/document'
import IconButton from '@/components/ui/IconButton.vue'
import { copyToClipboard } from '@/core/interaction/clipboard'

const props = defineProps<{
  docId: string | null
  /** Shown instead of reading the document (a preview of text that is not saved). */
  contentOverride?: string | null
  /** Relocate the existing copy action without changing its content or feedback. */
  copyTarget?: HTMLElement | null
  canReenter?: boolean
}>()

const emit = defineEmits<{ reenter: [] }>()
const { t, locale } = useI18n()
const k = (key: string) => `gui.document_window.viewer.${key}`

type CopyState = 'idle' | 'done' | 'failed'

const content = ref('')
const loading = ref(false)
const error = ref(false)
const unreadableReason = ref<string | null>(null)
const recoverMethod = ref<BodyRecoveryMethod | null>(null)
const recoverGuide = ref<string | null>(null)
const recoverNotice = ref<string | null>(null)
const recovering = ref(false)
const copyMdState = ref<CopyState>('idle')
// v0.1 rev4: rendered by the applyContent() call that set `content`, never by a computed over shared
// refs, so one load's diagnostics cause can not attach to another load's parse.
const renderedContent = ref('')

const hasBody = computed(() => stripLeadingNextHeader(stripFrontmatter(content.value || '')).trim() !== '')

// gfm: GitHub-Flavored Markdown (tables, etc.) — v0.1 R0001 #3.
const mdRenderer = new Marked({ gfm: true })
mdRenderer.use({
  renderer: {
    code({ text, lang }: { text: string; lang?: string }): string {
      const escaped = text
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
      const langClass = lang ? ` class="language-${lang}"` : ''
      return `<div class="code-block-wrapper"><button class="code-copy-btn" type="button" aria-label="${t(k('copy_code'))}" title="${t(k('copy_code'))}">⧉</button><pre><code${langClass}>${escaped}</code></pre></div>`
    },
  },
})

function render(source: string, cause: MarkdownParseCause | null): string {
  const stripped = stripLeadingNextHeader(stripFrontmatter(source || ''))
  const start = performance.now()
  const html = mdRenderer.parse(stripped) as string
  recordMarkdownParse(stripped.length, performance.now() - start, cause)
  return html
}

// v0.1 rev4: sets `content` and parses + records in one synchronous step with this call's own cause.
function applyContent(newContent: string, cause: MarkdownParseCause | null): void {
  content.value = newContent
  renderedContent.value = render(newContent, cause)
}

function copyGlyph(state: CopyState, idle: string): string {
  return state === 'done' ? '✓' : state === 'failed' ? '✕' : idle
}

function copyStateClass(state: CopyState): string | undefined {
  return state === 'idle' ? undefined : `md-copy--${state}`
}

function flash(target: typeof copyMdState, state: CopyState): void {
  target.value = state
  setTimeout(() => {
    target.value = 'idle'
  }, 1500)
}

async function copyMarkdown() {
  if (copyMdState.value !== 'idle') return
  flash(copyMdState, (await copyToClipboard(stripFrontmatter(content.value))) ? 'done' : 'failed')
}

async function handleContentClick(e: MouseEvent) {
  const btn = (e.target as Element).closest('.code-copy-btn')
  if (!btn) return
  e.stopPropagation()
  const codeEl = btn.closest('.code-block-wrapper')?.querySelector('code')
  if (!codeEl) return
  const copied = await copyToClipboard(codeEl.textContent ?? '')
  btn.textContent = copied ? '✓' : '✕'
  btn.classList.add(copied ? 'code-copy-btn--copied' : 'code-copy-btn--failed')
  setTimeout(() => {
    btn.textContent = '⧉'
    btn.classList.remove('code-copy-btn--copied', 'code-copy-btn--failed')
  }, 1500)
}

let loadSequence = 0
let activeLoads = 0
let contentRefreshInFlight = false
let contentRefreshPending = false
let contentRefreshTimer: ReturnType<typeof setTimeout> | null = null
let contentRefreshCause: MarkdownParseCause | null = null
let contentRefreshDetail: { doc_id: string; revision_no?: number | null; refresh_key?: string } | null = null
let firstPendingAt = 0
let lastScrollAt = 0
let disposed = false
let loadedOnce = false
const CONTENT_REFRESH_MS = 120
const SCROLL_IDLE_MS = 160
const MAX_SCROLL_DEFER_MS = 800

function clearContentRefresh() {
  if (contentRefreshTimer !== null) clearTimeout(contentRefreshTimer)
  contentRefreshTimer = null
  contentRefreshPending = false
  contentRefreshCause = null
  contentRefreshDetail = null
  firstPendingAt = 0
}

function scheduleContentRefresh(delay = CONTENT_REFRESH_MS) {
  if (contentRefreshTimer !== null || contentRefreshInFlight || !contentRefreshPending || disposed) return
  contentRefreshTimer = setTimeout(() => {
    contentRefreshTimer = null
    void runContentRefresh()
  }, delay)
}

async function runContentRefresh() {
  if (!contentRefreshPending || contentRefreshInFlight || disposed) return
  const now = Date.now()
  if (now - lastScrollAt < SCROLL_IDLE_MS && now - firstPendingAt < MAX_SCROLL_DEFER_MS) {
    scheduleContentRefresh(Math.min(SCROLL_IDLE_MS, MAX_SCROLL_DEFER_MS - (now - firstPendingAt)))
    return
  }
  // A prop-driven load owns the fetch. Keep the latest event's intent until it settles.
  if (activeLoads > 0) {
    scheduleContentRefresh(CONTENT_REFRESH_MS)
    return
  }
  contentRefreshInFlight = true
  contentRefreshPending = false
  const cause = contentRefreshCause
  const detail = contentRefreshDetail
  contentRefreshCause = null
  contentRefreshDetail = null
  firstPendingAt = 0
  const docId = props.docId
  try {
    const success = await loadContent(cause)
    if (!disposed && docId === props.docId && detail) {
      window.dispatchEvent(new CustomEvent('fg:document_content_refresh_completed', { detail: { ...detail, success } }))
    }
  } finally {
    contentRefreshInFlight = false
    if (contentRefreshPending) scheduleContentRefresh(0)
  }
}

async function loadContent(cause: MarkdownParseCause | null = null): Promise<boolean> {
  const seq = ++loadSequence
  const docId = props.docId
  const current = () => !disposed && seq === loadSequence && docId === props.docId
  if (props.contentOverride != null) {
    unreadableReason.value = null
    applyContent(props.contentOverride, cause)
    error.value = false
    loading.value = false
    loadedOnce = true
    return true
  }
  if (!docId) {
    unreadableReason.value = null
    applyContent('', cause)
    error.value = false
    loading.value = false
    return false
  }
  // The spinner only before the first answer; a reload keeps the shown body until the new one lands.
  const showSpinner = !loadedOnce
  activeLoads += 1
  if (showSpinner) loading.value = true
  error.value = false
  try {
    const read = await documentContentRead(docId)
    if (!current()) return false
    unreadableReason.value = read.state === 'unreadable' ? (read.reason ?? null) : null
    // The window reads its document again on a period (T#3 rework 2): an unchanged body is not parsed again.
    if (!(loadedOnce && read.content === content.value)) applyContent(read.content, cause)
    loadedOnce = true
    return true
  } catch {
    if (!current()) return false
    error.value = true
    unreadableReason.value = null
    applyContent('', cause)
    return false
  } finally {
    activeLoads -= 1
    if (current()) {
      loading.value = false
      if (contentRefreshPending) scheduleContentRefresh()
    }
  }
}

/** The diagnosed method of the shown document (its Flow's diagnosis); none when it cannot be read. */
async function diagnoseShown(docId: string): Promise<void> {
  recoverMethod.value = null
  recoverGuide.value = null
  const flowId = bodyRecoveryFlowOf(docId)
  if (!flowId) return
  try {
    const diagnosis = await bodyRecoveryDiagnose({ groupId: flowId })
    if (disposed || docId !== props.docId) return
    const item = diagnosis.items.find((entry) => entry.docId === docId)
    if (!item) return
    if (item.recoverable) recoverMethod.value = item.method
    else recoverGuide.value = `gui.document.recover.guide.${item.method}`
  } catch {
    // No diagnosis: re-entry stays the way to restore the body.
  }
}

async function recover(): Promise<void> {
  const docId = props.docId
  const method = recoverMethod.value
  if (!docId || !method || recovering.value) return
  recovering.value = true
  recoverNotice.value = null
  try {
    const result = await bodyRecoveryRecover(docId, method)
    if (disposed || docId !== props.docId) return
    recoverNotice.value = result.bodyLoss === 'possible' || result.bodyLoss === 'lost'
      ? `gui.document.recover.loss.${result.bodyLoss}`
      : 'gui.document.recover.done'
    await loadContent()
  } catch (error) {
    if (disposed || docId !== props.docId) return
    recoverNotice.value = `gui.document.recover.refusal.${bodyRecoveryRefusalOf(error) ?? 'internal_error'}`
    await diagnoseShown(docId)
  } finally {
    recovering.value = false
  }
}

/**
 * The server says this document's body changed: read it again (debounced, deferred while scrolling, kept
 * while a load runs). The document window calls this from the v0.2 project event hub (flowgate.v02.0053
 * T#3 rework); the v0.1 `fg:document_content_changed` window event lands here too.
 */
function requestContentRefresh(detail: { doc_id?: string | null; revision_no?: number | null; refresh_key?: string } | null | undefined) {
  if (!props.docId) return
  const docId = detail?.doc_id
  if (!docId || docId !== props.docId) return
  // Keep the latest event's diagnostics and completion identity; the fixed window bounds latency.
  if (!contentRefreshPending) firstPendingAt = Date.now()
  contentRefreshPending = true
  contentRefreshCause = { recovery: snapshotRecoverySource(), epoch: null }
  contentRefreshDetail = {
    doc_id: docId,
    revision_no: detail.revision_no,
    refresh_key: detail.refresh_key ?? `${docId}:${detail.revision_no ?? ''}`,
  }
  scheduleContentRefresh()
}

function onDocumentContentChanged(e: Event) {
  requestContentRefresh((e as CustomEvent).detail)
}

function onViewerScroll() {
  lastScrollAt = Date.now()
}

watch(
  () => [props.docId, props.contentOverride],
  // Never an event-caused reload — the watcher's own arguments are not a cause.
  () => {
    clearContentRefresh()
    recoverNotice.value = null
    loadedOnce = false
    void loadContent()
  },
  { immediate: true },
)

// The diagnosis is read only for a person who may restore the body, and only while it cannot be read.
watch(
  () => [unreadableReason.value, props.canReenter] as const,
  ([reason, allowed]) => {
    if (reason && allowed && props.docId) void diagnoseShown(props.docId)
    else {
      recoverMethod.value = null
      recoverGuide.value = null
    }
  },
)

// v0.1 rev5: re-render the loaded content in place on a locale change (the code copy button's label
// is read at parse time), with no cause of its own, so it can not race a load's cause attribution.
watch(locale, () => {
  renderedContent.value = render(content.value, null)
})

onMounted(() => {
  window.addEventListener('fg:document_content_changed', onDocumentContentChanged)
})

onBeforeUnmount(() => {
  disposed = true
  loadSequence += 1
  clearContentRefresh()
  window.removeEventListener('fg:document_content_changed', onDocumentContentChanged)
})

defineExpose({
  content,
  loadContent,
  requestContentRefresh,
})
</script>

<style scoped>
.md-viewer__unreadable {
  padding: 16px;
  color: var(--fg2-status-warning-fg);
}

.md-viewer__actions {
  display: flex;
  gap: 8px;
}

.md-viewer__notice {
  margin: 0 0 8px;
  color: var(--fg2-status-warning-fg);
}

.md-viewer {
  height: 100%;
  overflow-y: auto;
  overflow-x: hidden;
  min-width: 0;
  box-sizing: border-box;
  padding: 14px 18px;
  font-size: 13px;
  line-height: 1.75;
  color: var(--fg2-control-fg);
  scrollbar-gutter: stable;
}

.md-viewer__loading {
  padding: 32px;
  text-align: center;
  color: var(--fg2-muted-2);
}

.md-viewer__error {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 16px;
  color: var(--fg2-status-danger-fg);
}

/* Long unbreakable tokens (file paths) wrap instead of widening the window (v0.1 R0001 #3 rework 2). */
.md-viewer__content {
  min-width: 0;
  overflow-wrap: anywhere;
  word-break: break-word;
}

.md-viewer__content :deep(h1),
.md-viewer__content :deep(h2) {
  color: var(--fg2-text);
  margin-top: 1.4em;
  margin-bottom: 0.5em;
}

.md-viewer__content :deep(> :first-child) {
  margin-top: 0;
}

.md-viewer__content :deep(h3) {
  font-size: 15px;
  color: var(--fg2-text);
  margin-bottom: 8px;
  margin-top: 1.5em;
}

.md-viewer__content :deep(h4) {
  font-size: 13.5px;
  color: var(--fg2-text);
  margin-bottom: 6px;
  margin-top: 1.2em;
}

.md-viewer__content :deep(pre) {
  background: var(--fg2-bg);
  border: 1px solid var(--fg2-line);
  border-radius: var(--fg2-control-radius);
  padding: 10px 14px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  margin-bottom: 10px;
  /* Wrap long code lines instead of a horizontal scrollbar (v0.1 R0001 #3 rework 2). */
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: anywhere;
}

.md-viewer__content :deep(code) {
  background: var(--fg2-bg);
  border-radius: var(--fg2-control-radius-sm);
  padding: 1px 5px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  overflow-wrap: anywhere;
  word-break: break-word;
}

.md-viewer__content :deep(pre code) {
  background: none;
  padding: 0;
  border-radius: 0;
  font-size: inherit;
}

.md-viewer__content :deep(hr) {
  border: none;
  border-top: 1px solid var(--fg2-line);
  margin: 1.5em 0;
}

.md-viewer__content :deep(a) {
  color: var(--fg2-blue);
}

/* Lists — markers restored against a global list reset (v0.1 R0001 #3). */
.md-viewer__content :deep(ol),
.md-viewer__content :deep(ul) {
  margin: 0 0 0.8em;
  padding-left: 1.6em;
}

.md-viewer__content :deep(ol) {
  list-style: decimal;
}

.md-viewer__content :deep(ul) {
  list-style: disc;
}

.md-viewer__content :deep(li) {
  margin: 0.2em 0;
}

.md-viewer__content :deep(li > ul),
.md-viewer__content :deep(li > ol) {
  margin-bottom: 0;
}

/* Tables always fit the window width; cell text wraps (v0.1 R0001 #3 rework 2). */
.md-viewer__content :deep(table) {
  table-layout: fixed;
  width: 100%;
  border-collapse: collapse;
  margin: 0 0 1em;
  font-size: 12.5px;
}

.md-viewer__content :deep(th),
.md-viewer__content :deep(td) {
  border: 1px solid var(--fg2-line-strong);
  padding: 6px 10px;
  text-align: left;
  overflow-wrap: anywhere;
  word-break: break-word;
}

.md-viewer__content :deep(th) {
  background: var(--fg2-control-surface-flat);
  color: var(--fg2-text);
  font-weight: 600;
}

.md-viewer__toolbar {
  display: flex;
  justify-content: flex-end;
  gap: 4px;
  margin-bottom: 4px;
}

.md-viewer__toolbar--titlebar {
  margin-bottom: 0;
}

.md-copy--done {
  color: var(--fg2-status-success-fg);
}

.md-copy--failed {
  color: var(--fg2-status-danger-fg);
}

.md-viewer__content :deep(.code-block-wrapper) {
  position: relative;
  /* Code fences are independent blocks: skip offscreen layout and paint (v0.1). */
  content-visibility: auto;
  contain-intrinsic-size: auto 180px;
}

.md-viewer__content :deep(.code-copy-btn) {
  position: absolute;
  top: 6px;
  right: 8px;
  padding: 2px 8px;
  font-size: 12px;
  color: var(--fg2-muted);
  background: var(--fg2-panel);
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius-sm);
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.15s, color 0.15s;
}

.md-viewer__content :deep(.code-block-wrapper:hover .code-copy-btn) {
  opacity: 1;
}

.md-viewer__content :deep(.code-copy-btn--copied) {
  color: var(--fg2-status-success-fg);
  border-color: var(--fg2-status-success-border);
  opacity: 1;
}

.md-viewer__content :deep(.code-copy-btn--failed) {
  color: var(--fg2-status-danger-fg);
  border-color: var(--fg2-status-danger-border);
  opacity: 1;
}
</style>
