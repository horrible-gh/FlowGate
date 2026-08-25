<template>
  <div class="md-viewer">
    <div v-if="loading" class="md-viewer__loading">{{ t('common.loading') }}</div>
    <div v-else-if="error" class="md-viewer__error">
      <span>{{ t('main.error.file_load_failed') }}</span>
    </div>
    <template v-else-if="hasLinkedSource">
      <div class="md-viewer__toolbar">
        <div class="md-copy-group">
          <button class="md-copy-btn md-copy-btn--main" :class="{ 'md-copy-btn--copied': copyMdDone }" @click="copyMarkdown">
            <AppIcon name="copy" />
            {{ copyMdDone ? t('main.md_viewer.copied') : t('main.md_viewer.copy_md') }}
          </button>
          <button
            class="md-copy-btn md-copy-btn--header"
            :class="{ 'md-copy-btn--copied': copyHeaderDone }"
            :title="t('main.md_viewer.copy_md_with_header')"
            @click="copyMarkdownWithHeader"
          >
            <AppIcon v-if="!copyHeaderDone" name="text-h" />
            <AppIcon v-else name="check" />
          </button>
        </div>
      </div>
      <div class="md-viewer__content" v-html="renderedContent" @click.capture="handleContentClick" />
    </template>
    <div v-else class="md-viewer__empty">
      <div>{{ t('main.state.no_md_file') }}</div>
      <button
        v-if="docId && !readOnly"
        class="md-viewer__regen-btn"
        :disabled="regenerating"
        @click="regenerateFile"
      >
        <AppIcon name="arrow-clockwise" />
        {{ regenerating ? t('main.state.regenerating') : t('main.state.regenerate_file') }}
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import AppIcon from '@shared/AppIcon.vue'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Marked } from 'marked'
import { getRequest, postRequest } from '@shared/api'
import api from '@shared/api'
import { stripFrontmatter } from '@shared/utils/markdown'
import { useToast } from './common/useToast'
import { useExplorerStore } from '../stores/explorer'
import { copyToClipboard } from '../utils/clipboard'
import { openClipboardFallback } from '../composables/useClipboardFallback'

const props = defineProps<{
  path: string | null
  docId?: string | null
  contentOverride?: string | null
  projectId?: string | null
  // 0186 P0005 — read the markdown source from a group branch's Git objects
  // (checkout-free, read-only) instead of the base checkout.
  gitGroupId?: string | null
  gitCommit?: string | null
  readOnly?: boolean
}>()
const { t } = useI18n()
const explorerStore = useExplorerStore()
const { showToast } = useToast()

const content = ref('')
const loading = ref(false)
const error = ref(false)
const hasLinkedSource = ref(false)
const copyMdDone = ref(false)
const copyHeaderDone = ref(false)
const regenerating = ref(false)

// gfm: enable GitHub-Flavored Markdown (tables, etc.) — R0001 #3.
const mdRenderer = new Marked({ gfm: true })
mdRenderer.use({
  renderer: {
    code({ text, lang }: { text: string; lang?: string }): string {
      const escaped = text
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
      const langClass = lang ? ` class="language-${lang}"` : ''
      return `<div class="code-block-wrapper"><button class="code-copy-btn" type="button" aria-label="${t('main.md_viewer.copy_code')}"><svg class=\"code-copy-icon\" viewBox=\"0 0 256 256\" width=\"1em\" height=\"1em\" fill=\"currentColor\" aria-hidden=\"true\"><path d="M216,32H88a8,8,0,0,0-8,8V80H40a8,8,0,0,0-8,8V216a8,8,0,0,0,8,8H168a8,8,0,0,0,8-8V176h40a8,8,0,0,0,8-8V40A8,8,0,0,0,216,32ZM160,208H48V96H160Zm48-48H176V88a8,8,0,0,0-8-8H96V48H208Z"/></svg></button><pre><code${langClass}>${escaped}</code></pre></div>`
    },
  },
})

const renderedContent = computed(() => mdRenderer.parse(stripFrontmatter(content.value || '')) as string)

// Shared honest write (B0001 / group 0221) — the former local copy is folded into
// utils/clipboard. On failure offer the manual-copy fallback modal; toast only when it
// could not open (no text).
async function copyText(text: string): Promise<boolean> {
  const ok = await copyToClipboard(text)
  if (!ok && !openClipboardFallback(text)) {
    showToast(t('main.md_viewer.copy_failed'), 'danger')
  }
  return ok
}

async function copyMarkdown() {
  if (copyMdDone.value) return
  const copied = await copyText(stripFrontmatter(content.value))
  if (!copied) return
  copyMdDone.value = true
  setTimeout(() => { copyMdDone.value = false }, 1500)
}

async function copyMarkdownWithHeader() {
  if (copyHeaderDone.value) return
  const copied = await copyText(content.value)
  if (!copied) return
  copyHeaderDone.value = true
  setTimeout(() => { copyHeaderDone.value = false }, 1500)
}

async function handleContentClick(e: MouseEvent) {
  const btn = (e.target as Element).closest('.code-copy-btn')
  if (!btn) return
  e.stopPropagation()
  const wrapper = btn.closest('.code-block-wrapper')
  const codeEl = wrapper?.querySelector('code')
  if (!codeEl) return
  const text = codeEl.textContent ?? ''
  const copied = await copyText(text)
  if (!copied) return
  btn.textContent = t('main.md_viewer.copied')
  btn.classList.add('code-copy-btn--copied')
  setTimeout(() => {
    btn.textContent = ''
    btn.classList.remove('code-copy-btn--copied')
  }, 1500)
}

async function regenerateFile() {
  // R0001 / NR0003: recreate the missing .md file for this document. Only meaningful
  // for DB-backed documents (docId); src-file (path) views have no regenerate path.
  if (!props.docId || regenerating.value) return
  regenerating.value = true
  try {
    const res = await postRequest<{ body_lost?: boolean }>(
      `/api/v1/documents/${encodeURIComponent(props.docId)}/regenerate`,
      {},
    )
    const bodyLost = (res.data as any)?.body_lost === true
    showToast(
      bodyLost ? t('main.state.regenerated_metadata') : t('main.state.regenerated_revision'),
      bodyLost ? 'warning' : 'success',
    )
    await loadContent()
  } catch (e: any) {
    const status = e?.response?.status
    // 409 = the file already exists (e.g. another tab regenerated it) — just reload.
    if (status === 409) {
      await loadContent()
      return
    }
    showToast(t('main.state.regenerate_failed'), 'danger')
  } finally {
    regenerating.value = false
  }
}

async function loadContent(): Promise<boolean> {
  const path = props.path
  const docId = props.docId
  if (props.contentOverride != null) {
    content.value = props.contentOverride
    error.value = false
    hasLinkedSource.value = true
    loading.value = false
    return true
  }
  if (!docId && !path) {
    content.value = ''
    error.value = false
    hasLinkedSource.value = false
    loading.value = false
    return false
  }
  loading.value = true
  error.value = false
  hasLinkedSource.value = false
  try {
    if (docId) {
      const res = await getRequest<{ content: string }>(`/api/v1/documents/content?doc_id=${encodeURIComponent(docId)}`)
      content.value = (res.data as any)?.content ?? ''
      hasLinkedSource.value = true
    } else if (path) {
      if (props.projectId && props.gitGroupId) {
        // Group-branch read: checkout-free blob (read-only). Binary/oversize
        // markdown is unusual, but fall back to empty content rather than error.
        const data = await explorerStore.fetchGroupBranchBlob(props.projectId, props.gitGroupId, path)
        content.value = data.binary ? '' : (data.content ?? '')
        hasLinkedSource.value = true
      } else if (props.projectId) {
        const url = `/api/v1/projects/${encodeURIComponent(props.projectId)}/files/src-content?path=${encodeURIComponent(path)}`
        const res = await api.get<string>(url, { responseType: 'text' })
        content.value = res.data
        hasLinkedSource.value = true
      } else {
        const res = await fetch(`/api/files/content?path=${encodeURIComponent(path)}`)
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        content.value = await res.text()
        hasLinkedSource.value = true
      }
    }
    return true
  } catch (e: any) {
    const status = e?.response?.status
    if (status === 404) {
      error.value = false
      hasLinkedSource.value = false
    } else {
      error.value = true
    }
    content.value = ''
    return false
  } finally {
    loading.value = false
  }
}

function onDocumentContentChanged(e: Event) {
  if (!props.docId) return
  const detail = (e as CustomEvent).detail as {
    project?: string | null
    doc_id?: string | null
    revision_no?: number | null
    refresh_key?: string
  } | undefined
  if (detail?.doc_id !== props.docId) return
  if (detail.project && props.projectId && detail.project !== props.projectId) return
  void loadContent().then((success) => {
    window.dispatchEvent(new CustomEvent('fg:document_content_refresh_completed', {
      detail: {
        doc_id: detail.doc_id,
        revision_no: detail.revision_no ?? null,
        refresh_key: detail.refresh_key,
        success,
      },
    }))
  })
}

watch(
  () => [props.path, props.docId, props.contentOverride, props.projectId, props.gitGroupId, props.gitCommit],
  loadContent,
  { immediate: true },
)

onMounted(() => {
  window.addEventListener('fg:document_content_changed', onDocumentContentChanged)
})

onBeforeUnmount(() => {
  window.removeEventListener('fg:document_content_changed', onDocumentContentChanged)
})

defineExpose({
  content,
  loadContent,
})
</script>

<style scoped>
.md-viewer {
  height: 100%;
  overflow-y: auto;
  overflow-x: hidden;
  min-width: 0;
  padding: 18px;
  font-size: .8rem;
  line-height: 1.8;
  color: var(--text-s);
  scrollbar-gutter: stable;
  scrollbar-width: auto;
  scrollbar-color: auto;
}

.md-viewer::-webkit-scrollbar {
  width: 22px;
  height: 22px;
}

.md-viewer::-webkit-scrollbar-track {
  background: #e2e8f0;
}

.md-viewer::-webkit-scrollbar-thumb {
  background: #94a3b8;
  border: 3px solid #e2e8f0;
  border-radius: 11px;
}

.md-viewer::-webkit-scrollbar-thumb:hover {
  background: #64748b;
}

.md-viewer__loading,
.md-viewer__empty {
  padding: 32px;
  text-align: center;
  opacity: 0.6;
}

.md-viewer__empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
}

.md-viewer__regen-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
  font-size: .8rem;
  color: var(--text-m);
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: var(--r);
  cursor: pointer;
  transition: color .15s, border-color .15s;
}

.md-viewer__regen-btn:hover:not(:disabled) {
  color: var(--text);
  border-color: var(--primary);
}

.md-viewer__regen-btn:disabled {
  cursor: default;
  opacity: 0.6;
}

.md-viewer__error {
  padding: 16px;
  color: var(--danger, #dc2626);
}

/* Wrap long unbreakable tokens (e.g. file paths like a\b\c.py) so nothing overflows
   the panel width horizontally — `overflow-wrap: anywhere` also lowers the content's
   intrinsic min-width so it can shrink inside flex parents (R0001 #3 rework 2). */
.md-viewer__content {
  min-width: 0;
  overflow-wrap: anywhere;
  word-break: break-word;
}

.md-viewer__content :deep(h3) {
  font-size: 1rem;
  color: var(--text);
  margin-bottom: 8px;
  margin-top: 1.5em;
}

.md-viewer__content :deep(h4) {
  font-size: .875rem;
  color: var(--text);
  margin-bottom: 6px;
  margin-top: 1.2em;
}

.md-viewer__content :deep(h1),
.md-viewer__content :deep(h2) {
  color: var(--text);
  margin-top: 1.5em;
  margin-bottom: 0.5em;
}

.md-viewer__content :deep(pre) {
  background: var(--bg);
  border-radius: var(--r);
  padding: 10px 14px;
  font-family: 'JetBrains Mono', monospace;
  font-size: .75rem;
  margin-bottom: 10px;
  /* Wrap long code lines instead of a horizontal scrollbar (R0001 #3 rework 2). */
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: anywhere;
}

.md-viewer__content :deep(code) {
  background: var(--bg);
  border-radius: var(--r);
  padding: 1px 5px;
  font-family: 'JetBrains Mono', monospace;
  font-size: .75rem;
  /* Long inline paths must wrap, not overflow the panel (R0001 #3 rework 2). */
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
  border-top: 1px solid var(--border);
  margin: 1.5em 0;
}

.md-viewer__content :deep(a) {
  color: var(--primary);
}

/* Lists — restore markers stripped by the global `ul, ol { list-style: none }`
   reset in app.css, so ordered (1. 2.) and bulleted lists render properly (R0001 #3). */
.md-viewer__content :deep(ol),
.md-viewer__content :deep(ul) {
  margin: 0 0 .8em;
  padding-left: 1.6em;
}

.md-viewer__content :deep(ol) {
  list-style: decimal;
}

.md-viewer__content :deep(ul) {
  list-style: disc;
}

.md-viewer__content :deep(li) {
  margin: .2em 0;
}

.md-viewer__content :deep(li > ul),
.md-viewer__content :deep(li > ol) {
  margin-bottom: 0;
}

/* Tables — GFM tables had no borders at all; give them collapsed 1px borders (R0001 #3).
   table-layout:fixed + width:100% makes the table always fit the panel width (columns
   share the space and cell text wraps), so a wide table never overflows and never needs
   a horizontal scrollbar — it stays fully visible at a glance (R0001 #3 rework 2). */
.md-viewer__content :deep(table) {
  table-layout: fixed;
  width: 100%;
  border-collapse: collapse;
  margin: 0 0 1em;
  font-size: .9rem;
}

.md-viewer__content :deep(th),
.md-viewer__content :deep(td) {
  border: 1px solid var(--border);
  padding: 6px 10px;
  text-align: left;
  /* Long path-like cell content wraps within the column instead of widening it. */
  overflow-wrap: anywhere;
  word-break: break-word;
}

.md-viewer__content :deep(th) {
  background: var(--surface, var(--bg));
  font-weight: 600;
}

.md-viewer__truncate-hint {
  color: var(--text-m);
  font-style: italic;
  font-size: .75rem;
}

.md-viewer__toolbar {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 8px;
}

.md-copy-group {
  display: inline-flex;
}

.md-copy-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 10px;
  font-size: .75rem;
  color: var(--text-m);
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: var(--r);
  cursor: pointer;
  transition: color .15s, border-color .15s;
}

.md-copy-btn:hover {
  color: var(--text);
  border-color: var(--primary);
}

.md-copy-btn--copied {
  color: var(--success, #16a34a);
  border-color: var(--success, #16a34a);
}

.md-copy-group .md-copy-btn--main {
  border-top-right-radius: 0;
  border-bottom-right-radius: 0;
}

.md-copy-group .md-copy-btn--header {
  border-left: none;
  border-top-left-radius: 0;
  border-bottom-left-radius: 0;
  padding: 3px 8px;
}

.md-viewer__content :deep(.code-block-wrapper) {
  position: relative;
}

.md-viewer__content :deep(.code-copy-btn) {
  position: absolute;
  top: 6px;
  right: 8px;
  padding: 2px 8px;
  font-size: .7rem;
  color: var(--text-m);
  background: var(--surface, #313244);
  border: 1px solid var(--border);
  border-radius: var(--r);
  cursor: pointer;
  opacity: 0;
  transition: opacity .15s, color .15s;
}

.md-viewer__content :deep(.code-block-wrapper:hover .code-copy-btn) {
  opacity: 1;
}

.md-viewer__content :deep(.code-copy-btn .code-copy-icon) {
  width: 1em;
  height: 1em;
  vertical-align: -0.14em;
}

.md-viewer__content :deep(.code-copy-btn--copied) {
  color: var(--success, #16a34a);
  border-color: var(--success, #16a34a);
  opacity: 1;
}
</style>
