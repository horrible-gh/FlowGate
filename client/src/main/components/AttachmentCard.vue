<template>
  <!-- flowgate.default.0060 — port of the approved mockup `wdkcvrmk`'s four screens (main / empty / deleting / collapsed)
       into a real component. Class names, structure, and transition values are taken straight from the mockup.
       Elements present in the mockup are not omitted, and elements absent from the mockup are not added (D0010 6-2, DS0009 §5):
         · The [view empty state] control in the `main` screen is not carried over — it is a demo device labeled "데모" in the mockup.
         · No separate [download] button is added — the filename in the list row is itself the download entry point.
         · No [copy] button is added — copy is an API channel called by whichever side handles the source (D0010 6-7). -->
  <section
    class="card attach-card"
    :class="{ collapsed, 'has-files': attachments.length > 0 }"
    @dragenter="onCardDragEnter"
    @dragover="onCardDragEnter"
  >
    <div class="card-hd">
      <!-- The entire title row is the collapse/expand button (D0010 6-2). Caret rotation -90°,
           0.18s transition, aria-expanded, and reduced-motion handling all use the same values
           as DocHeader's collapse rule (D0010 6-3 / TR0006 measured values). No new collapse
           mechanism was created. -->
      <button
        class="card-hd-toggle"
        type="button"
        :aria-expanded="!collapsed"
        :aria-controls="bodyId"
        :title="collapsed ? t('main.attachment_card.expand') : t('main.attachment_card.collapse')"
        @click="collapsed = !collapsed"
      >
        <span class="card-title">
          <AppIcon name="paperclip" style="color:var(--text-m);" />
          {{ t('main.attachment_card.title') }}
        </span>
        <AppIcon name="caret-down" class="card-hd-caret" />
        <!-- Summary shown only while collapsed. Even while collapsed, it's readable how many files are attached. -->
        <span class="attach-fold-summary">{{ foldSummary }}</span>
      </button>
      <div class="card-actions">
        <span class="attach-count-pill">{{ t('main.attachment_card.count', { count: attachments.length }) }}</span>
      </div>
    </div>

    <div :id="bodyId" class="card-bd attach-card-bd">
      <!-- Dropzone: a large area when there are 0 files, a thin single-line bar once there is
           1 or more (D0010 6-4). In read-only mode (AI run in progress), upload/delete are
           removed, leaving only list/download (D0010 6-1). -->
      <div
        v-if="!readOnly"
        class="attach-dropzone"
        :class="{ 'drag-over': dragging }"
        @dragenter.prevent="dragging = true"
        @dragover.prevent="dragging = true"
        @dragleave.prevent="dragging = false"
        @dragend.prevent="dragging = false"
        @drop.prevent="onDrop"
      >
        <input ref="fileInputRef" type="file" multiple hidden @change="onPick" />
        <div class="attach-dz-empty">
          <AppIcon name="upload-simple" class="attach-dz-icon" />
          <p class="attach-dz-text">{{ t('main.attachment_card.drop_zone') }}</p>
          <button class="btn btn-outline btn-sm attach-select-btn" type="button" @click.stop="openPicker">
            <AppIcon name="folder-open" /> {{ t('main.attachment_card.select_files') }}
          </button>
          <p class="attach-dz-hint">{{ t('main.attachment_card.hint', { size: maxUploadLabel }) }}</p>
        </div>
        <div class="attach-dz-compact">
          <AppIcon name="plus-circle" class="attach-dz-plus" />
          <span>
            {{ t('main.attachment_card.drop_zone_compact') }}
            <button class="attach-inline-btn" type="button" @click.stop="openPicker">
              {{ t('main.attachment_card.select_inline') }}
            </button>
          </span>
        </div>
      </div>

      <ul class="attach-list">
        <!-- List row: kind icon · filename · size · delete button. Same four columns as the mockup. -->
        <li
          v-for="item in attachments"
          :key="item.filename"
          class="attach-item"
          :class="{ removing: removing.has(item.filename) }"
          @transitionend="onRemoveTransitionEnd(item.filename)"
        >
          <span class="attach-item-ico" :class="kindOf(item.filename)">
            <AppIcon :name="iconOf(item.filename)" />
          </span>
          <!-- The filename itself is the download entry point (D0010 6-2). -->
          <button
            class="attach-item-name"
            type="button"
            :title="item.filename"
            @click="download(item)"
          >{{ item.filename }}</button>
          <span class="attach-item-size">{{ formatSize(item.size) }}</span>
          <button
            v-if="!readOnly"
            class="attach-item-del"
            type="button"
            :title="t('main.attachment_card.delete')"
            @click="remove(item)"
          >
            <AppIcon name="trash" />
          </button>
        </li>
      </ul>
      <p v-if="attachments.length === 0" class="attach-empty-note">
        {{ t('main.attachment_card.empty_note') }}
      </p>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import AppIcon from '@shared/AppIcon.vue'
import { deleteRequest, downloadBlobRequest, getRequest, postFormRequest } from '@shared/api'
import { useToast } from './common/useToast'

/** P0011 1-3 attachment object. The path is always storage-relative, never absolute. */
export interface AttachmentItem {
  doc_id: string
  original_filename: string
  filename: string
  size: number
  content_type: string
  content_sha256: string
  path: string
  path_base: string
  uploaded_by: string | null
  uploaded_at: string
}

const props = defineProps<{
  docId: string
  readOnly?: boolean
}>()

const emit = defineEmits<{ changed: [count: number] }>()

const { t } = useI18n()
const { showToast } = useToast()

// L0012 1-1 attach_max_upload_bytes. The number in the hint text and the server limit must
// match — the screen used to say 10MB while the mockup's hint said 20MB, and L0012 fixed it
// at 20 MiB.
const ATTACH_MAX_UPLOAD_BYTES = 20971520

const attachments = ref<AttachmentItem[]>([])
const collapsed = ref(true)       // Initial value is collapsed when the document is created (0420 R0001) — replaces D0010 6-3's default-expanded
const dragging = ref(false)
const removing = ref<Set<string>>(new Set())
// A row the server already deleted, only waiting for the transition to finish. Kept separate
// from `removing` to distinguish it from a row that was rejected and reverts (only the
// transition rewinds; the row stays in the list).
const pendingDrop = ref<Set<string>>(new Set())
const fileInputRef = ref<HTMLInputElement | null>(null)
const bodyId = computed(() => `attach-body-${props.docId.replace(/[^A-Za-z0-9_-]/g, '-')}`)
const maxUploadLabel = computed(() => `${Math.round(ATTACH_MAX_UPLOAD_BYTES / (1024 * 1024))}MB`)

const foldSummary = computed(() => {
  const list = attachments.value
  if (list.length === 0) return t('main.attachment_card.fold_summary_empty')
  // Omit the "외 N개" ("and N more") suffix when N = count - 1 is 0 (L0012 §5 boundary condition).
  if (list.length === 1) return t('main.attachment_card.fold_summary_one', { name: list[0].filename })
  return t('main.attachment_card.fold_summary_many', {
    name: list[0].filename,
    rest: list.length - 1,
  })
})

function apiBase(): string {
  return `/api/v1/documents/${encodeURIComponent(props.docId)}/attachments`
}

function kindOf(name: string): string {
  const ext = (name.split('.').pop() || '').toLowerCase()
  if (['xls', 'xlsx'].includes(ext)) return 'xls'
  if (ext === 'csv') return 'csv'
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg'].includes(ext)) return 'img'
  if (ext === 'pdf') return 'pdf'
  return 'file'
}

function iconOf(name: string): string {
  const kind = kindOf(name)
  if (kind === 'xls' || kind === 'csv') return 'file-xls'
  if (kind === 'img') return 'file-image'
  if (kind === 'pdf') return 'file-pdf'
  return 'file'
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`
}

function errorCode(e: any): string {
  return e?.response?.data?.error?.code ?? ''
}

function errorMessage(e: any, fallbackKey: string): string {
  const code = errorCode(e)
  // 0060 TR0017 rev2. The rejection reason was only a console line reading
  // `500 (Internal Server Error)` plus a generic failure message. When the server fails to
  // store/register (5xx), that differs from a situation the user can simply retry, so the
  // screen also distinguishes and reports that the cause was logged on the server.
  const status = Number(e?.response?.status ?? 0)
  const serverSide =
    status >= 500 ||
    code === 'ATTACHMENT_STORE_FAILED' ||
    code === 'ATTACHMENT_METADATA_FAILED' ||
    code === 'ATTACHMENT_OPERATION_FAILED'
  if (serverSide) return t('main.attachment_card.error_server')
  if (code === 'DOCUMENT_NOT_MUTABLE') return t('main.attachment_card.error_not_mutable')
  if (code === 'ATTACHMENT_TOO_LARGE') {
    return t('main.attachment_card.error_too_large', { size: maxUploadLabel.value })
  }
  if (code === 'ATTACHMENT_NOT_FOUND') return t('main.attachment_card.error_not_found')
  return t(fallbackKey)
}

/** List fetch (P0011 §3). Zero attachments is not an error — it's 200 + an empty array. */
async function fetchList() {
  try {
    const res = await getRequest<any>(apiBase())
    const data = (res.data as any)?.data ?? res.data
    attachments.value = (data?.attachments ?? []) as AttachmentItem[]
    emit('changed', attachments.value.length)
  } catch {
    attachments.value = []
  }
}

function openPicker() {
  fileInputRef.value?.click()
}

function onCardDragEnter() {
  // If a file is dragged onto the card while collapsed, expand it automatically (D0010 6-3).
  if (collapsed.value) collapsed.value = false
}

function onPick(event: Event) {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files ?? [])
  input.value = ''
  if (files.length) void upload(files)
}

function onDrop(event: DragEvent) {
  dragging.value = false
  const files = Array.from(event.dataTransfer?.files ?? [])
  if (files.length) void upload(files)
}

/** Upload (P0011 §2). Multiple files are sent in one request by repeating the same-named `file` part. */
async function upload(files: File[]) {
  const form = new FormData()
  files.forEach((f) => form.append('file', f))
  try {
    const res = await postFormRequest<any>(apiBase(), form)
    const data = (res.data as any)?.data ?? res.data
    const added = (data?.attachments ?? []) as AttachmentItem[]
    attachments.value = [...attachments.value, ...added]
    emit('changed', attachments.value.length)
    showToast(t('main.attachment_card.uploaded', { count: added.length }), 'success')
  } catch (e: any) {
    // On failure, leave the pre-success list unchanged (P0011 §2). If one part fails the
    // whole request fails, so there is no partial application.
    showToast(errorMessage(e, 'main.attachment_card.upload_failed'), 'error')
    await fetchList()
  }
}

/** Download (P0011 §4). An auth header is required, so it's fetched as a blob and saved. */
async function download(item: AttachmentItem) {
  try {
    const res = await downloadBlobRequest(`${apiBase()}/${encodeURIComponent(item.filename)}`)
    const url = URL.createObjectURL(res.data)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = item.filename
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    URL.revokeObjectURL(url)
  } catch (e: any) {
    showToast(errorMessage(e, 'main.attachment_card.download_failed'), 'error')
    await fetchList()
  }
}

/**
 * Delete (P0011 §5, mockup screen ③).
 * Tags the pressed row with `.removing` so it fades and slides aside, then redraws the
 * list once the transition ends. If the server rejects the request (e.g. 409 during an
 * AI run), the transition reverts and the original item is shown again.
 */
async function remove(item: AttachmentItem) {
  if (removing.value.has(item.filename)) return
  removing.value = new Set([...removing.value, item.filename])
  try {
    await deleteRequest(`${apiBase()}/${encodeURIComponent(item.filename)}`)
    pendingDrop.value = new Set([...pendingDrop.value, item.filename])
    // Also clean up once more after the transition duration, so the row doesn't linger in
    // environments where the transition never runs (reduced-motion setting, test runners).
    // If transitionend fires first, it wins.
    window.setTimeout(() => onRemoveTransitionEnd(item.filename), 200)
  } catch (e: any) {
    const next = new Set(removing.value)
    next.delete(item.filename)
    removing.value = next
    showToast(errorMessage(e, 'main.attachment_card.delete_failed'), 'error')
    if (errorCode(e) === 'ATTACHMENT_NOT_FOUND') await fetchList()
  }
}

function onRemoveTransitionEnd(filename: string) {
  if (!pendingDrop.value.has(filename)) return
  const drop = new Set(pendingDrop.value)
  drop.delete(filename)
  pendingDrop.value = drop
  const left = new Set(removing.value)
  left.delete(filename)
  removing.value = left
  attachments.value = attachments.value.filter((a) => a.filename !== filename)
  emit('changed', attachments.value.length)
}

watch(() => props.docId, () => { void fetchList() }, { immediate: true })

defineExpose({ fetchList, attachments, collapsed })
</script>

<style scoped>
/* Carries over mockup wdkcvrmk's rules as-is. Different values would make this a different thing than the mockup. */
.attach-card { margin-bottom: 14px; }

/* Accordion — the entire card title is the collapse/expand button.
   Caret, rotation angle, transition time (0.18s), and reduced-motion handling follow
   the same rules as DocHeader's `.doc-hdr-collapse-btn` / `.doc-hdr-caret` (D0010 6-3). */
.card-hd-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  min-width: 0;
  margin: -14px 0 -14px -18px;
  padding: 14px 10px 14px 18px;
  border: 0;
  color: inherit;
  background: transparent;
  text-align: left;
  cursor: pointer;
}
.card-hd-toggle:hover .card-title { color: var(--primary); }
.card-hd-toggle:focus-visible { outline: 2px solid var(--info); outline-offset: -2px; }
.card-hd-caret {
  margin-left: 2px;
  color: var(--text-m);
  font-size: .7rem;
  transition: transform .18s ease;
}
.attach-card.collapsed .card-hd-caret { transform: rotate(-90deg); }
.attach-card.collapsed .card-bd { display: none; }
@media (prefers-reduced-motion: reduce) {
  .card-hd-caret { transition-duration: .1s; }
  .attach-item { transition-duration: .1s; }
}

/* Summary shown only while collapsed — even while collapsed, the number of attachments is readable. */
.attach-fold-summary {
  display: none;
  overflow: hidden;
  color: var(--text-m);
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: .72rem;
}
.attach-card.collapsed .attach-fold-summary { display: inline; }

/* MainPanel's `.card-actions` is a scoped rule in that file, so it can't reach into a child
   component. The same values are duplicated here. */
.card-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.attach-count-pill {
  padding: 1px 8px;
  border: 1px solid var(--border);
  border-radius: 999px;
  color: var(--text-s);
  background: var(--surface-h);
  font-size: .68rem;
  font-weight: 700;
}

.attach-card-bd { padding: 0; }

.attach-dropzone {
  position: relative;
  margin-bottom: 10px;
  border: 1.5px dashed var(--border-d);
  border-radius: var(--r);
  background: var(--surface-h);
  transition: border-color .15s ease, background .15s ease;
}
.attach-dropzone.drag-over {
  border-color: var(--primary);
  background: var(--primary-l);
}
.attach-dz-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 26px 16px;
  text-align: center;
}
.attach-dz-icon { color: var(--text-m); font-size: 1.6rem; }
.attach-dz-text { margin: 0; color: var(--text-s); font-size: .82rem; }
.attach-dz-hint { margin: 2px 0 0; color: var(--text-m); font-size: .68rem; }
.attach-dz-compact {
  display: none;
  align-items: center;
  gap: 7px;
  padding: 9px 12px;
  color: var(--text-s);
  font-size: .76rem;
}
.attach-dz-plus { color: var(--primary); font-size: 1rem; }
.attach-inline-btn {
  border: 0;
  color: var(--primary);
  background: transparent;
  font-weight: 600;
  text-decoration: underline;
  cursor: pointer;
}
/* 0 files → large dropzone, 1+ files → thin single-line bar (D0010 6-4). */
.attach-card.has-files .attach-dz-empty { display: none; }
.attach-card.has-files .attach-dz-compact { display: flex; }

.attach-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.attach-item {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: var(--r);
  background: var(--surface);
  transition: opacity .16s ease, transform .16s ease, margin .16s ease, padding .16s ease, height .16s ease;
  overflow: hidden;
}
/* Mockup screen ③ delete in progress — fades and slides aside. */
.attach-item.removing {
  opacity: 0;
  transform: translateX(6px);
  height: 0;
  padding-top: 0;
  padding-bottom: 0;
  margin: 0;
  border-width: 0;
}
.attach-item-ico { flex: 0 0 auto; color: var(--text-m); font-size: 1.05rem; }
.attach-item-ico.xls, .attach-item-ico.csv { color: #16a34a; }
.attach-item-ico.img { color: #7c3aed; }
.attach-item-ico.pdf { color: #dc2626; }
.attach-item-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  border: 0;
  padding: 0;
  color: var(--text);
  background: transparent;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: .78rem;
  cursor: pointer;
}
.attach-item-name:hover { color: var(--primary); text-decoration: underline; }
.attach-item-size { flex: 0 0 auto; color: var(--text-m); font-size: .68rem; }
.attach-item-del {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  width: 22px;
  height: 22px;
  border: 0;
  border-radius: 50%;
  color: var(--text-m);
  background: transparent;
  cursor: pointer;
}
.attach-item-del:hover { color: var(--danger); background: var(--danger-l); }
/* R0001 re-rejection: what used to sit flush at the bottom with only a 4px margin under the
   dropzone is now a footer band with a divider, giving top/bottom margin and centering the
   text inside it. Mockup empty.html already specifies this hint text itself (see the test
   above), so it is not removed — only its placement is fixed. */
.attach-empty-note {
  margin: 8px 0 0;
  padding: 10px 0;
  border-top: 1px solid var(--border);
  color: var(--text-m);
  font-size: .72rem;
  text-align: center;
}
</style>
