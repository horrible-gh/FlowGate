<template>
  <section class="tr2-source" data-testid="tr2-source-import" :aria-busy="busy">
    <h3>{{ t('main.tr2_body.source.heading') }}</h3>
    <details class="source-info"><summary>{{ t('main.tr2_body.diag.technical') }}</summary><p>{{ t('main.tr2_body.source.hint') }}</p></details>
    <div class="source-dropzone" :class="{ 'is-dragging': dragging }" data-testid="tr2-source-dropzone"
      @dragover.prevent="dragging = true" @dragleave.prevent="dragging = false" @drop.prevent="dropFiles">
      <strong>{{ t('main.tr2_body.import.drop') }}</strong>
      <p>{{ t('main.tr2_body.import.source_hint') }}</p>
      <input ref="fileInput" type="file" multiple hidden data-testid="tr2-source-files" :disabled="disabled || busy" @change="loadFiles" />
      <input ref="folderInput" type="file" multiple hidden webkitdirectory data-testid="tr2-source-folder" :disabled="disabled || busy" @change="loadFiles" />
      <div class="actions">
        <button type="button" class="btn btn-primary btn-sm" :disabled="disabled || busy" @click="fileInput?.click()">{{ t('main.tr2_body.source.files') }}</button>
        <button type="button" class="btn btn-outline btn-sm" :disabled="disabled || busy" @click="folderInput?.click()">{{ t('main.tr2_body.source.folder') }}</button>
      </div>
    </div>
    <template v-if="rows.length || busy">
      <p v-if="busy">{{ t('main.tr2_body.batch.loading') }}</p>
      <p role="status">{{ t('main.tr2_body.batch.count', { total: rows.length, valid: rows.filter(r => r.valid).length, selected: selected.length }) }}</p>
      <p v-if="stale || (previewRevision !== null && previewRevision !== revision)" role="alert">{{ t('main.tr2_body.batch.stale') }}</p>
      <ul>
        <li v-for="(row, index) in rows" :key="index" :data-testid="`tr2-source-row-${index}`">
          <label><input v-model="row.selected" type="checkbox" :disabled="disabled || busy || stale || !row.valid" />{{ row.name }}</label>
          <input v-model="row.file" :aria-label="t('main.tr2_body.source.path')" :placeholder="t('main.tr2_body.source.path')" :disabled="disabled || busy" @input="invalidate" />
          <strong>{{ t(`main.tr2_body.source.status.${row.status}`) }}</strong>
          <p v-if="row.reason && row.status === 'review_required'" class="source-error">{{ sourceReason(row.reason) }}</p>
          <Tr2CodeDiff v-if="row.item" :before="row.status === 'EDIT' ? String(row.item.anchor_old ?? '') : undefined" :after="String(row.item.replacement_new ?? row.item.content ?? '')" />
          <details v-if="row.reason || row.item"><summary>{{ t('main.tr2_body.diag.technical') }}</summary><p v-if="row.reason">{{ row.reason }}</p><pre v-if="row.item">{{ JSON.stringify(row.item, null, 2) }}</pre></details>
        </li>
      </ul>
      <div class="actions">
        <button type="button" data-testid="tr2-source-preview" class="btn btn-outline btn-sm" :disabled="disabled || busy || stale || !candidates.length" @click="compare">{{ t('main.tr2_body.source.compare') }}</button>
        <button type="button" data-testid="tr2-source-save" class="btn btn-primary btn-sm" :disabled="disabled || busy || stale || !selected.length || previewRevision !== revision || !fingerprint" @click="save">{{ t('main.tr2_body.source.save', { count: selected.length }) }}</button>
        <button type="button" data-testid="tr2-source-cancel" class="btn btn-outline btn-sm" :disabled="saving" @click="cancel">{{ t('main.tr2_body.actions.cancel') }}</button>
      </div>
    </template>
    <p v-if="problem" role="alert">{{ problem }}</p>
  </section>
</template>
<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { postRequest } from '@shared/api'
import { describeTr2Error } from './tr2State'
import Tr2CodeDiff from './Tr2CodeDiff.vue'

type SourceRow = { name: string; file: string; content_base64: string | null; status: string; valid: boolean; selected: boolean; reason: string; item: Record<string, unknown> | null }
type Result = { revision_no: number; preview_fingerprint: string; files: Array<{ index: number; status: string; valid: boolean; reason: string | null; item: Record<string, unknown> | null }> }
const props = defineProps<{ docId: string; revision: number; disabled: boolean; stale: boolean }>()
const emit = defineEmits<{ changed: [value: { revision: number; message: string }]; editing: [value: boolean]; busy: [value: boolean]; cancelled: []; saving: [value: boolean] }>()
const { t, te } = useI18n()
const rows = ref<SourceRow[]>([])
const busy = ref(false)
const saving = ref(false)
const fingerprint = ref('')
const previewRevision = ref<number | null>(null)
const problem = ref('')
const dragging = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)
const folderInput = ref<HTMLInputElement | null>(null)
let generation = 0
let previewInputs: Array<{ file: string; content_base64: string }> = []
let previewRows: SourceRow[] = []
const candidates = computed(() => rows.value.filter(row => row.content_base64 !== null))
const selected = computed(() => rows.value.filter(row => row.valid && row.selected))
const base = computed(() => `/api/v1/documents/${encodeURIComponent(props.docId)}/tr2/sources`)
watch(saving, value => emit('saving', value), { flush: 'sync' })
watch(busy, value => emit('busy', value), { flush: 'sync' })
watch(() => rows.value.length, value => emit('editing', value > 0), { flush: 'sync' })
function invalidate() {
  fingerprint.value = ''
  previewRevision.value = null
  previewRows = []
  for (const row of rows.value) { row.valid = false; row.selected = false; row.item = null; if (row.content_base64 !== null) { row.status = 'pending'; row.reason = '' } }
}
function reset() {
  generation++
  rows.value = []
  emit('editing', false)
  busy.value = false
  saving.value = false
  fingerprint.value = ''
  previewRevision.value = null
  previewInputs = []
  previewRows = []
  problem.value = ''
  dragging.value = false
}
function cancel() { if (!saving.value) { reset(); emit('cancelled') } }
async function loadFiles(event: Event) {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files ?? [])
  input.value = ''
  await readFiles(files)
}
function dropFiles(event: DragEvent) {
  dragging.value = false
  if (props.disabled || busy.value) return
  // A directory drop needs the browser's folder picker to preserve every path.
  const items = Array.from(event.dataTransfer?.items ?? [])
  if (items.some(item => item.webkitGetAsEntry?.()?.isDirectory)) { problem.value = t('main.tr2_body.import.folder_drop'); return }
  if (event.dataTransfer) void readFiles(Array.from(event.dataTransfer.files))
}
async function readFiles(files: File[]) {
  if (props.disabled || busy.value || !files.length) return
  reset()
  if (files.length > 200 || files.reduce((n, f) => n + f.size, 0) > 10 * 1024 * 1024) { problem.value = t('main.tr2_body.source.limit'); return }
  const current = generation
  busy.value = true
  emit('editing', true)
  const loaded = await Promise.all(files.map(async (file): Promise<SourceRow> => {
    const row: SourceRow = { name: file.name, file: file.webkitRelativePath || '', content_base64: null, status: 'pending', valid: false, selected: false, reason: '', item: null }
    try {
      if (file.size > 1024 * 1024) throw new Error(t('main.tr2_body.source.limit'))
      const bytes = new Uint8Array(await file.arrayBuffer())
      let binary = ''
      for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192))
      row.content_base64 = btoa(binary)
    } catch { row.status = 'review_required'; row.reason = t('main.tr2_body.source.read_error') }
    return row
  }))
  if (current !== generation) return
  rows.value = loaded
  busy.value = false
}
async function compare() {
  if (props.disabled || busy.value || props.stale || !candidates.value.length) return
  invalidate()
  const current = generation
  const revision = props.revision
  const url = `${base.value}/preview`
  const targets = candidates.value
  busy.value = true
  problem.value = ''
  try {
    const inputs = targets.map(row => ({ file: row.file, content_base64: row.content_base64! }))
    const response = await postRequest<Result>(url, { expected_revision: revision, files: inputs })
    if (current !== generation) return
    for (const result of response.data.files) {
      const row = targets[result.index]
      if (row) Object.assign(row, { status: result.status, valid: result.valid, selected: result.valid, reason: result.reason ?? '', item: result.item })
    }
    previewInputs = inputs
    previewRows = targets
    fingerprint.value = response.data.preview_fingerprint
    previewRevision.value = revision
  } catch (exc) {
    if (current === generation) problem.value = describeTr2Error(exc, t, te).text
  } finally { if (current === generation) busy.value = false }
}
async function save() {
  if (props.disabled || busy.value || props.stale || previewRevision.value !== props.revision || !fingerprint.value || !selected.value.length) return
  const current = generation
  const url = base.value
  const indices = previewRows.map((row, i) => row.valid && row.selected ? i : -1).filter(i => i >= 0)
  busy.value = true
  saving.value = true
  problem.value = ''
  try {
    const response = await postRequest<{ new_revision: number }>(url, { expected_revision: previewRevision.value, files: previewInputs, selected: indices, preview_fingerprint: fingerprint.value })
    if (current !== generation) return
    const revision = response.data.new_revision
    reset()
    emit('changed', { revision, message: t('main.tr2_body.saved', { revision }) })
  } catch (exc) {
    if (current === generation) { problem.value = describeTr2Error(exc, t, te).text; invalidate() }
  } finally { if (current === generation) { busy.value = false; saving.value = false } }
}
function sourceReason(reason: string): string {
  const key = reason.includes('duplicate') ? 'duplicate'
    : reason.includes('path_unsafe') ? 'path'
    : reason.includes('parent directory') ? 'parent'
    : reason.includes('UTF-8') || reason.includes('binary/control') ? 'encoding'
    : reason.includes('MiB') || reason.includes('limit') ? 'size'
    : reason.includes('already has pending') ? 'occupied'
    : reason.includes('hunks') || reason.includes('anchor') ? 'anchor' : 'review'
  return t(`main.tr2_body.import.reason.${key}`)
}
defineExpose({ reset })
watch(() => props.docId, reset)
onBeforeUnmount(reset)
</script>
<style scoped>
.tr2-source { min-width: 0; font-size: 12px; }
h3 { font-size: 14px; margin: 0 0 8px; }
.source-info { margin-bottom: 12px; }
p { color: var(--text-m, #718096); line-height: 1.6; }
.actions { display: flex; flex-wrap: wrap; gap: 8px; }
input[type=file] { display: none; }
.btn { display: inline-flex; align-items: center; justify-content: center; border: 1px solid var(--border, #e3e9f1); border-radius: 8px; background: var(--surface, #fff); color: var(--text, #48586c); padding: 7px 11px; font: inherit; font-weight: 650; cursor: pointer; }
.btn-primary { background: var(--primary, #356ae6); color: #fff; border-color: var(--primary, #356ae6); }
.btn:disabled { opacity: .45; cursor: not-allowed; }
.btn:focus-visible, input:focus-visible, summary:focus-visible { outline: 2px solid var(--primary, #356ae6); outline-offset: 2px; }
.source-dropzone { padding: 24px 18px; border: 1.5px dashed var(--border, #b8cbe8); border-radius: 11px; background: var(--bg, #f8fbff); text-align: center; }
.source-dropzone .actions { justify-content: center; }
.source-dropzone.is-dragging { border-color: var(--primary, #356ae6); background: var(--primary-l, #eef4ff); }
ul { display: grid; gap: 8px; max-height: 360px; overflow: auto; padding: 0; list-style: none; }
li { padding: 12px; border: 1px solid var(--border, #e3e9f1); border-radius: 9px; overflow-wrap: anywhere; min-width: 0; }
li > input { display: block; box-sizing: border-box; width: 100%; margin: 8px 0; padding: 8px 9px; border: 1px solid var(--border, #d5deea); border-radius: 7px; background: var(--surface, #fff); color: inherit; font: inherit; }
.source-error, p[role=alert] { color: var(--danger, #b83b45); }
pre { white-space: pre; overflow-wrap: normal; word-break: normal; overflow: auto; max-height: 200px; background: var(--bg, #f6f8fc); padding: 10px; font: 12px/1.6 ui-monospace, Consolas, monospace; }
summary { cursor: pointer; color: var(--text-m, #718096); margin-top: 8px; }
</style>
