<template>
  <section class="tr2 card" data-testid="tr2-body">
    <header class="tr2-header">
      <div><strong>TR2 · {{ tab.title }}</strong><small>{{ tab.id }}</small></div>
      <div class="tr2-actions">
        <button type="button" class="btn btn-outline btn-sm" :disabled="loading" @click="refresh">Refresh</button>
        <button type="button" class="btn btn-outline btn-sm" :disabled="loading" @click="runDiagnostic">Live diagnostic</button>
        <button type="button" class="btn btn-outline btn-sm" :disabled="!view" @click="downloadSpec">Download edit spec</button>
        <label class="btn btn-outline btn-sm" :aria-disabled="locked"><input type="file" accept="application/json,.json" :disabled="locked" @change="uploadSpec">Upload edit spec</label>
      </div>
    </header>
    <p v-if="error" class="tr2-error" role="alert">{{ error }}</p>
    <p v-if="loading && !view">Loading TR2…</p>
    <template v-if="view">
      <div class="tr2-summary">
        <div><span>Status</span><strong>{{ status }}</strong></div>
        <div><span>Readiness</span><strong>{{ readiness }}</strong></div>
        <div><span>Source history</span><strong>{{ view.history?.source_history_state ?? 'unknown' }}</strong></div>
        <div><span>Revision</span><strong>{{ view.document.revision_no }}</strong></div>
      </div>
      <p v-if="locked" class="tr2-note">Read only: document status or AI run locks editing.</p>
      <p v-if="view.derived?.live_precheck?.drift" class="tr2-error">Source fingerprint drift detected. Review the live diagnostic before approval.</p>
      <section class="tr2-section">
        <h3>Edit specification</h3>
        <p>Source T2: {{ view.body.source_t2_doc_id }} · {{ view.body.edit_spec.termination }}</p>
        <p>{{ view.body.edit_spec.edits.length }} edits · {{ view.body.edit_spec.deferred.length }} deferred · {{ view.body.edit_spec.gate.commands.length }} validation commands</p>
        <button type="button" class="btn btn-outline btn-sm" :disabled="locked" @click="openEditor">Edit specification</button>
      </section>
      <section class="tr2-section">
        <h3>Files</h3>
        <p v-if="!view.derived.files.length">No source files declared.</p>
        <ul v-else class="tr2-files">
          <li v-for="file in view.derived.files" :key="file.path">
            <button type="button" @click="selectFile(file.path)">{{ file.path }}</button>
            <span>{{ file.kind }} · {{ file.exists ? 'exists' : 'new' }} · {{ file.edit_ids.join(', ') }}</span>
          </li>
        </ul>
        <div v-if="fileDetail" class="tr2-detail">
          <h4>{{ fileDetail.path }}</h4>
          <p v-if="fileDetail.truncated">BEFORE preview truncated at 1 MiB.</p>
          <template v-for="edit in fileDetail.edits" :key="edit.id">
            <p>{{ edit.id }} · {{ edit.kind }} · {{ edit.confidence }} — {{ edit.rationale }}</p>
            <div v-if="edit.kind !== 'create_file'"><b>BEFORE</b><pre>{{ edit.anchor_old }}</pre></div>
            <div><b>AFTER</b><pre>{{ edit.kind === 'create_file' ? edit.content : edit.replacement_new }}</pre></div>
          </template>
          <details v-if="fileDetail.before_text != null"><summary>Current source preview</summary><pre>{{ fileDetail.before_text }}</pre></details>
        </div>
        <p v-if="fileError" class="tr2-error" role="alert">{{ fileError }}</p>
      </section>
      <section v-if="view.body.edit_spec.deferred.length" class="tr2-section">
        <h3>Deferred</h3>
        <ul><li v-for="item in view.body.edit_spec.deferred" :key="item.id">{{ item.id }} · {{ item.file ?? 'no file' }} · {{ item.reason }} — {{ item.rationale }}</li></ul>
      </section>
      <section class="tr2-section">
        <h3>Live diagnostic</h3>
        <p>{{ diagnostic?.code ?? (view.derived.live_precheck.drift ? 'drift' : 'not run') }} · {{ diagnostic?.ready ? 'ready' : 'review required' }}</p>
        <p>Baseline: {{ view.derived.live_precheck.baseline_fingerprint }}</p>
        <p>Live: {{ view.derived.live_precheck.live_fingerprint }}</p>
        <pre v-if="diagnostic">{{ JSON.stringify(diagnostic, null, 2) }}</pre>
      </section>
      <section class="tr2-section">
        <h3>Approval attempts</h3>
        <p v-if="!view.approval.attempts.length">No attempts.</p>
        <ol v-else><li v-for="(attempt, index) in view.approval.attempts" :key="attempt.attempt_id ?? index">{{ attempt.state }} · {{ attempt.phase }} · {{ attempt.result_code ?? attempt.error_code ?? '' }} · {{ attempt.commit_sha ?? '' }}</li></ol>
        <p>Ledger rows: {{ view.history?.ledger?.length ?? 0 }}</p>
      </section>
      <section v-if="editorOpen" class="tr2-section">
        <h3>Edit spec JSON</h3>
        <p>Only the edit specification is editable. Server fingerprints and approval history stay on the server.</p>
        <textarea v-model="editorText" rows="18" aria-label="TR2 edit specification JSON" :disabled="locked || saving" />
        <div class="tr2-actions"><button type="button" class="btn btn-primary btn-sm" :disabled="locked || saving" @click="saveSpec">Save</button><button type="button" class="btn btn-outline btn-sm" @click="editorOpen = false">Cancel</button></div>
      </section>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { getRequest, postRequest, putRequest } from '@shared/api'
import type { Tab } from '../../stores/tabs'

type Row = Record<string, any>
interface Tr2View {
  document: { revision_no: number; doc_review_status?: string; editable?: boolean }
  body: { tr2_version: number; source_t2_doc_id: string; edit_spec: { termination: string; edits: Row[]; deferred: Row[]; gate: { commands: string[] } } }
  derived: { files: Array<{ path: string; kind: string; exists: boolean; edit_ids: string[] }>; live_precheck: { drift: boolean; baseline_fingerprint: string; live_fingerprint: string } }
  approval: { latest_attempt: Row | null; attempts: Row[] }
  history: { source_history_state: string; ledger: Row[] }
}

const props = defineProps<{ tab: Tab; readOnly: boolean }>()
const view = ref<Tr2View | null>(null)
const diagnostic = ref<Row | null>(null)
const fileDetail = ref<Row | null>(null)
const fileError = ref('')
const error = ref('')
const loading = ref(false)
const saving = ref(false)
const editorOpen = ref(false)
const editorText = ref('')
let generation = 0
const base = computed(() => `/api/v1/documents/${encodeURIComponent(props.tab.id)}/tr2`)
const locked = computed(() => props.readOnly || !view.value?.document.editable || saving.value)
const status = computed(() => view.value?.document.doc_review_status ?? 'unknown')
const readiness = computed(() => {
  const attempt = view.value?.approval.latest_attempt
  if (view.value?.history.source_history_state === 'conflict') return 'time-machine conflict'
  if (view.value?.history.source_history_state === 'restore_pending') return 'time-machine restore pending'
  if (attempt?.phase === 'rollback' || attempt?.state === 'recovery_required') return 'recovery required'
  if (attempt?.state === 'in_progress') return 'applying'
  if (status.value === 'approved' || status.value === 'rejected') return status.value
  if (attempt?.state === 'failed' && attempt?.error_code) return String(attempt.error_code).replace(/^tr2_/, '').replaceAll('_', ' ')
  if (attempt?.state === 'failed') return 'approval failed'
  if (view.value?.derived.live_precheck.drift) return 'drift'
  if (diagnostic.value?.code) return String(diagnostic.value.code)
  return view.value?.body.edit_spec.termination === 'ready_to_apply' ? 'ready for diagnostic' : 'needs more work'
})
function message(exc: any): string { return exc?.response?.data?.error?.message ?? exc?.response?.data?.message ?? exc?.response?.data?.detail ?? exc?.message ?? String(exc) }
async function refresh() {
  const current = ++generation
  loading.value = true
  try {
    const response = await getRequest<Tr2View>(base.value)
    if (current !== generation) return
    view.value = response.data
    error.value = ''
    fileDetail.value = null
  } catch (exc) { if (current === generation) error.value = message(exc) }
  finally { if (current === generation) loading.value = false }
}
async function runDiagnostic() {
  try { diagnostic.value = (await postRequest<Row>(`${base.value}/precheck`, {})).data; error.value = '' }
  catch (exc) { diagnostic.value = null; error.value = message(exc) }
}
async function selectFile(path: string) {
  fileError.value = ''
  try { fileDetail.value = (await getRequest<Row>(`${base.value}/files/${path.split('/').map(encodeURIComponent).join('/')}`)).data }
  catch (exc) { fileDetail.value = null; fileError.value = message(exc) }
}
function openEditor() { if (!view.value || locked.value) return; editorText.value = JSON.stringify(view.value.body.edit_spec, null, 2); editorOpen.value = true }
async function saveSpec() {
  if (!view.value || locked.value) return
  let spec: Row
  try { spec = JSON.parse(editorText.value); if (!spec || Array.isArray(spec) || typeof spec !== 'object') throw new Error('JSON object required') }
  catch (exc) { error.value = `Invalid JSON: ${message(exc)}`; return }
  saving.value = true
  try {
    await putRequest(base.value, { expected_revision: view.value.document.revision_no, body: { tr2_version: view.value.body.tr2_version, source_t2_doc_id: view.value.body.source_t2_doc_id, edit_spec: spec } })
    editorOpen.value = false
    diagnostic.value = null
    await refresh()
    window.dispatchEvent(new CustomEvent('fg:document_content_changed', { detail: { doc_id: props.tab.id, project: props.tab.projectId } }))
  } catch (exc) { error.value = message(exc); await refresh() }
  finally { saving.value = false }
}
async function uploadSpec(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file || locked.value) return
  try {
    const parsed = JSON.parse(await file.text())
    const spec = parsed?.edit_spec ?? parsed
    if (!spec || Array.isArray(spec) || typeof spec !== 'object') throw new Error('Edit specification object required')
    editorText.value = JSON.stringify(spec, null, 2)
    editorOpen.value = true
    error.value = ''
    await saveSpec()
  } catch (exc) { error.value = `Invalid JSON: ${message(exc)}` }
}
function downloadSpec() {
  if (!view.value) return
  const body = { tr2_version: view.value.body.tr2_version, source_t2_doc_id: view.value.body.source_t2_doc_id, edit_spec: view.value.body.edit_spec }
  const url = URL.createObjectURL(new Blob([JSON.stringify(body, null, 2) + '\n'], { type: 'application/json' }))
  const link = document.createElement('a'); link.href = url; link.download = `${props.tab.id}-edit-spec.json`; link.click(); URL.revokeObjectURL(url)
}
function onChanged(event: Event) { const detail = (event as CustomEvent).detail; if (detail?.doc_id === props.tab.id && !editorOpen.value && !saving.value) void refresh() }
onMounted(() => window.addEventListener('fg:document_content_changed', onChanged))
onBeforeUnmount(() => { generation++; window.removeEventListener('fg:document_content_changed', onChanged) })
watch(() => props.tab.id, () => { diagnostic.value = null; void refresh() }, { immediate: true })
</script>

<style scoped>
.tr2 { padding: 16px; display: grid; gap: 16px; }
.tr2-header, .tr2-actions { display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap; }
.tr2-header small { display: block; color: var(--text-m); }
.tr2-actions input[type=file] { display: none; }
.tr2-summary { display: grid; grid-template-columns: repeat(auto-fit,minmax(130px,1fr)); gap: 8px; }
.tr2-summary div { border: 1px solid var(--border,#d9e1e8); border-radius: 8px; padding: 10px; }
.tr2-summary span, .tr2-summary strong { display: block; }
.tr2-summary span, .tr2-files span, .tr2-note { color: var(--text-m); font-size: .8rem; }
.tr2-section { border-top: 1px solid var(--border,#d9e1e8); padding-top: 12px; min-width: 0; }
.tr2-section h3 { margin: 0 0 8px; font-size: 1rem; }
.tr2-files { list-style: none; padding: 0; }
.tr2-files li { display: flex; gap: 10px; flex-wrap: wrap; padding: 6px 0; }
.tr2-files button { border: 0; background: none; color: var(--primary,#2563eb); cursor: pointer; text-align: left; }
.tr2-error { color: var(--danger,#b91c1c); }
.tr2 pre { white-space: pre-wrap; overflow-wrap: anywhere; background: var(--bg,#f6f8fa); padding: 10px; border-radius: 6px; max-height: 320px; overflow: auto; }
.tr2 textarea { box-sizing: border-box; width: 100%; font-family: monospace; }
</style>
