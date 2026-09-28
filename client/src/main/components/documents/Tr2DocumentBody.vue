<template>
  <section class="tr2 card" data-testid="tr2-body">
    <header class="tr2-header">
      <div>
        <strong>TR2 · {{ tab.title }}</strong>
        <span v-if="state" class="tr2-pill" :class="`tr2-tone-${tone}`" data-testid="tr2-state-pill">{{ t(`main.tr2_body.state.${state}`) }} · {{ state }}</span>
        <small>{{ tab.id }}</small>
      </div>
      <div class="tr2-actions">
        <button type="button" class="btn btn-outline btn-sm" :disabled="loading" @click="refresh">{{ t('main.tr2_body.actions.refresh') }}</button>
        <button type="button" class="btn btn-outline btn-sm" :disabled="loading || !view" @click="runDiagnostic">{{ t('main.tr2_body.actions.diagnostic') }}</button>
        <button type="button" class="btn btn-outline btn-sm" :disabled="!view" data-testid="tr2-download" @click="downloadSpec">{{ t('main.tr2_body.actions.download') }}</button>
        <label class="btn btn-outline btn-sm" :aria-disabled="locked"><input type="file" accept="application/json,.json" data-testid="tr2-upload" :disabled="locked" @change="uploadSpec">{{ t('main.tr2_body.actions.upload') }}</label>
        <button type="button" class="btn btn-outline btn-sm" :disabled="locked" data-testid="tr2-edit-whole" @click="openWholeEditor(false)">{{ t('main.tr2_body.actions.edit_whole') }}</button>
        <button type="button" class="btn btn-outline btn-sm" :disabled="locked" data-testid="tr2-new-whole" @click="openWholeEditor(true)">{{ t('main.tr2_body.actions.new_whole') }}</button>
        <button type="button" class="btn btn-outline btn-sm btn-danger-outline" :disabled="locked" data-testid="tr2-reset-whole" @click="ask({ kind: 'reset' })">{{ t('main.tr2_body.actions.reset_whole') }}</button>
      </div>
    </header>

    <div v-if="error" class="tr2-error" role="alert">
      <span>{{ error }}</span>
      <button v-if="stale" type="button" class="btn btn-outline btn-sm" data-testid="tr2-reload" @click="reload">{{ t('main.tr2_body.actions.reload') }}</button>
    </div>
    <p v-if="notice" class="tr2-notice" role="status">{{ notice }}</p>
    <p v-if="loading && !view">{{ t('main.tr2_body.loading') }}</p>

    <div v-if="confirming" class="tr2-confirm" role="alertdialog" data-testid="tr2-confirm">
      <span>{{ confirmText }}</span>
      <button type="button" class="btn btn-danger btn-sm" data-testid="tr2-confirm-yes" @click="confirmYes">{{ t('main.tr2_body.actions.confirm') }}</button>
      <button type="button" class="btn btn-outline btn-sm" @click="confirming = null">{{ t('main.tr2_body.actions.cancel') }}</button>
    </div>

    <template v-if="view && state">
      <div class="tr2-summary">
        <div><span>{{ t('main.tr2_body.summary.review_status') }}</span><strong>{{ reviewLabel }}</strong></div>
        <div><span>{{ t('main.tr2_body.summary.state') }}</span><strong>{{ t(`main.tr2_body.state.${state}`) }}</strong></div>
        <div><span>{{ t('main.tr2_body.summary.history') }}</span><strong data-testid="tr2-history-state">{{ historyLabel }}</strong></div>
        <div><span>{{ t('main.tr2_body.summary.revision') }}</span><strong>{{ view.document.revision_no }}</strong></div>
        <div><span>{{ t('main.tr2_body.summary.attempt') }}</span><strong>{{ latest ? attemptLabel(latest) : t('main.tr2_body.summary.no_attempt') }}</strong></div>
      </div>

      <div class="tr2-strip" :class="`tr2-tone-${tone}`" data-testid="tr2-state-strip">
        <strong>{{ t(`main.tr2_body.state.${state}`) }}</strong>
        <span>{{ t(`main.tr2_body.state_desc.${state}`, { phase: phaseLabel(latest?.phase), code: latest?.error_code ?? '' }) }}</span>
        <span v-if="latest?.error_code" class="tr2-code">{{ latest.error_code }}
          <em v-if="latest.retryable === true">{{ t('main.tr2_body.attempts.retryable') }}</em>
          <em v-else-if="latest.retryable === false">{{ t('main.tr2_body.attempts.not_retryable') }}</em>
        </span>
        <div v-if="retry?.allowed" class="tr2-retry" data-testid="tr2-retry-block">
          <span>{{ retry.mode === 'recover_stale' ? t('main.tr2_body.retry.stale') : t('main.tr2_body.retry.available') }}</span>
          <button type="button" class="btn btn-primary btn-sm" data-testid="tr2-retry" :disabled="retrying || readOnly" @click="ask({ kind: 'retry' })">{{ retrying ? t('main.tr2_body.retry.running') : t('main.tr2_body.actions.retry') }}</button>
        </div>
        <span v-else-if="retryBlockedKey" class="tr2-note" data-testid="tr2-retry-blocked">{{ t(retryBlockedKey) }}</span>
      </div>

      <p v-if="lockKey" class="tr2-note" data-testid="tr2-lock">{{ t(lockKey) }}</p>
      <p v-if="staleWhileEditing" class="tr2-error">{{ t('main.tr2_body.errors.stale_while_editing') }}</p>

      <div class="tr2-split">
        <section class="tr2-section">
          <h3>{{ t('main.tr2_body.files.heading') }} <small>{{ t('main.tr2_body.files.counts', counts) }}</small></h3>
          <p v-if="!items.length">{{ t('main.tr2_body.files.none') }}</p>
          <ul v-else class="tr2-files">
            <li v-for="entry in items" :key="`${entry.collection}:${entry.item.id}`" :class="{ 'is-selected': selected?.item.id === entry.item.id }">
              <button type="button" :data-testid="`tr2-item-${entry.item.id}`" @click="selectItem(entry)">
                <span class="tr2-op" :class="`tr2-op-${entry.op}`">{{ opLabel(entry.op) }}</span>
                <span class="tr2-path">{{ entry.item.file ?? t('main.tr2_body.files.no_file') }}</span>
                <small>{{ entry.item.id }}</small>
              </button>
            </li>
          </ul>
          <button type="button" class="btn btn-outline btn-sm" data-testid="tr2-add-item" :disabled="locked" @click="openItemEditor(null)">{{ t('main.tr2_body.actions.add_item') }}</button>
          <label class="btn btn-outline btn-sm" :aria-disabled="locked"><input type="file" accept="application/json,.json" data-testid="tr2-upload-new-item" :disabled="locked" @change="uploadItem($event, null)">{{ t('main.tr2_body.actions.item_upload') }}</label>
        </section>

        <section class="tr2-section tr2-detail" data-testid="tr2-detail">
          <Tr2ItemEditor v-if="itemEditor" :key="itemEditor.key" :initial-op="itemEditor.op" :item="itemEditor.item" :disabled="locked || saving" @save="saveItem" @cancel="itemEditor = null" />
          <template v-else-if="selected">
            <div class="tr2-actions">
              <h4>{{ opLabel(selected.op) }} · {{ selected.item.file ?? t('main.tr2_body.files.no_file') }}</h4>
              <div>
                <button type="button" class="btn btn-outline btn-sm" data-testid="tr2-item-edit" :disabled="locked" @click="openItemEditor(selected)">{{ t('main.tr2_body.actions.item_edit') }}</button>
                <button type="button" class="btn btn-outline btn-sm" data-testid="tr2-item-download" @click="downloadItem(selected)">{{ t('main.tr2_body.actions.item_download') }}</button>
                <label class="btn btn-outline btn-sm" :aria-disabled="locked"><input type="file" accept="application/json,.json" data-testid="tr2-item-upload" :disabled="locked" @change="uploadItem($event, selected)">{{ t('main.tr2_body.actions.item_upload') }}</label>
                <button type="button" class="btn btn-outline btn-sm btn-danger-outline" data-testid="tr2-item-delete" :disabled="locked" @click="ask({ kind: 'delete', id: selected.item.id })">{{ t('main.tr2_body.actions.item_delete') }}</button>
              </div>
            </div>
            <template v-if="selected.op === 'deferred'">
              <p><b>{{ t('main.tr2_body.files.deferred_heading') }}</b>: {{ deferredReasonLabel(selected.item.reason) }} ({{ selected.item.reason }})</p>
              <p>{{ selected.item.rationale }}</p>
              <p class="tr2-note">{{ t('main.tr2_body.files.deferred_no_diff') }}</p>
            </template>
            <template v-else>
              <p class="tr2-note">{{ t('main.tr2_body.files.item_meta', { id: selected.item.id, confidence: selected.item.confidence ?? '', rationale: selected.item.rationale ?? '' }) }}</p>
              <div v-if="selected.op === 'edit'" class="tr2-diff">
                <div><b>{{ t('main.tr2_body.files.before') }}</b><pre>{{ selected.item.anchor_old }}</pre></div>
                <div><b>{{ t('main.tr2_body.files.after') }}</b><pre>{{ selected.item.replacement_new }}</pre></div>
              </div>
              <div v-else class="tr2-diff tr2-diff--new"><div><b>{{ t('main.tr2_body.files.after_only') }}</b><pre>{{ selected.item.content }}</pre></div></div>
              <p v-if="fileDetail?.truncated" class="tr2-note">{{ t('main.tr2_body.files.truncated') }}</p>
              <details v-if="fileDetail?.before_text != null"><summary>{{ t('main.tr2_body.files.current_source') }}</summary><pre>{{ fileDetail.before_text }}</pre></details>
            </template>
          </template>
        </section>
      </div>

      <section class="tr2-section">
        <h3>{{ t('main.tr2_body.gate.heading') }} <small>{{ gate ? t('main.tr2_body.gate.from_attempt', { round: gate.round }) : t('main.tr2_body.gate.pending') }}</small></h3>
        <p v-if="!view.body.edit_spec.gate.commands.length">{{ t('main.tr2_body.gate.none') }}</p>
        <ul v-else class="tr2-gate">
          <li v-for="(command, index) in view.body.edit_spec.gate.commands" :key="index" :class="`is-${gateStatus(index)}`"><code>{{ command }}</code> <span>{{ t(`main.tr2_body.gate.${gateStatus(index)}`) }}</span></li>
        </ul>
      </section>

      <section class="tr2-section">
        <h3>{{ t('main.tr2_body.diag.heading') }}</h3>
        <p v-if="view.derived.live_precheck.drift" class="tr2-error">{{ t('main.tr2_body.diag.drift_banner') }}</p>
        <p data-testid="tr2-diagnostic">{{ diagnostic ? (diagnostic.ready ? t('main.tr2_body.diag.ready') : t('main.tr2_body.diag.review_required')) : t('main.tr2_body.diag.not_run') }}<span v-if="diagnostic?.code"> · {{ diagnostic.code }}</span></p>
        <p>{{ t('main.tr2_body.diag.baseline') }}: <code>{{ view.derived.live_precheck.baseline_fingerprint }}</code></p>
        <p>{{ t('main.tr2_body.diag.live') }}: <code>{{ view.derived.live_precheck.live_fingerprint }}</code></p>
      </section>

      <section class="tr2-section">
        <h3>{{ t('main.tr2_body.attempts.heading') }}</h3>
        <p v-if="!view.approval.attempts.length">{{ t('main.tr2_body.attempts.none') }}</p>
        <ol v-else class="tr2-attempts" data-testid="tr2-attempts">
          <li v-for="(attempt, index) in view.approval.attempts" :key="attempt.attempt_id ?? index" :class="`is-${attempt.state}`">
            <span>{{ attemptLabel(attempt) }}</span>
            <span>{{ attempt.finished_at ?? attempt.started_at ?? '' }}</span>
            <span v-if="attempt.error_code">{{ attempt.error_code }}</span>
            <span v-if="attempt.commit_sha">{{ t('main.tr2_body.attempts.commit', { sha: String(attempt.commit_sha).slice(0, 7) }) }}</span>
            <em v-if="attempt.retryable === true">{{ t('main.tr2_body.attempts.retryable') }}</em>
            <em v-else-if="attempt.retryable === false">{{ t('main.tr2_body.attempts.not_retryable') }}</em>
          </li>
        </ol>
        <p>{{ t('main.tr2_body.attempts.ledger', { count: view.history?.ledger?.length ?? 0 }) }}</p>
      </section>

      <section v-if="wholeEditorOpen" class="tr2-section" data-testid="tr2-whole-editor">
        <h3>{{ t('main.tr2_body.editor.whole_title') }}</h3>
        <p class="tr2-note">{{ t('main.tr2_body.editor.whole_hint') }}</p>
        <p class="tr2-note">{{ t('main.tr2_body.editor.legend_editable') }} / {{ t('main.tr2_body.editor.legend_locked') }}</p>
        <textarea v-model="editorText" rows="18" :aria-label="t('main.tr2_body.editor.whole_title')" :disabled="locked || saving" />
        <div class="tr2-actions">
          <button type="button" class="btn btn-primary btn-sm" data-testid="tr2-save-whole" :disabled="locked || saving" @click="saveWhole">{{ t('main.tr2_body.actions.save') }}</button>
          <button type="button" class="btn btn-outline btn-sm" @click="wholeEditorOpen = false">{{ t('main.tr2_body.actions.cancel') }}</button>
        </div>
      </section>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { deleteRequest, getRequest, postRequest, putRequest } from '@shared/api'
import type { Tab } from '../../stores/tabs'
import Tr2ItemEditor from './Tr2ItemEditor.vue'
import {
  gateResults, itemOp, tr2State, tr2Tone,
  type ItemCollection, type ItemOp, type Row, type Tr2View,
} from './tr2State'

interface Entry { collection: ItemCollection; op: ItemOp; item: Row }
type Pending = { kind: 'reset' } | { kind: 'retry' } | { kind: 'delete'; id: string }

const props = defineProps<{ tab: Tab; readOnly: boolean }>()
const { t, te } = useI18n()

const view = ref<Tr2View | null>(null)
const diagnostic = ref<Row | null>(null)
const fileDetail = ref<Row | null>(null)
const selectedKey = ref<string | null>(null)
const itemEditor = ref<{ key: number; op: ItemOp; item: Row | null } | null>(null)
const wholeEditorOpen = ref(false)
const editorText = ref('')
const error = ref('')
const notice = ref('')
const stale = ref(false)
const staleWhileEditing = ref(false)
const loading = ref(false)
const saving = ref(false)
const retrying = ref(false)
const confirming = ref<Pending | null>(null)
let generation = 0
let editorSeq = 0

const base = computed(() => `/api/v1/documents/${encodeURIComponent(props.tab.id)}/tr2`)
const state = computed(() => (view.value ? tr2State(view.value) : null))
const tone = computed(() => (state.value ? tr2Tone(state.value) : 'live'))
const latest = computed(() => view.value?.approval.latest_attempt ?? null)
const retry = computed(() => view.value?.approval.retry ?? null)
const mutationAllowed = computed(() => view.value?.mutation?.allowed ?? Boolean(view.value?.document.editable))
const locked = computed(() => props.readOnly || !view.value || !mutationAllowed.value || saving.value)
const gate = computed(() => (view.value ? gateResults(view.value) : null))
const editing = computed(() => wholeEditorOpen.value || itemEditor.value !== null)

const items = computed<Entry[]>(() => {
  const spec = view.value?.body.edit_spec
  if (!spec) return []
  return [
    ...spec.edits.map((item) => ({ collection: 'edits' as const, op: itemOp('edits', item), item })),
    ...spec.deferred.map((item) => ({ collection: 'deferred' as const, op: 'deferred' as const, item })),
  ]
})
const selected = computed(() => items.value.find((entry) => `${entry.collection}:${entry.item.id}` === selectedKey.value) ?? null)
const counts = computed(() => ({
  edit: items.value.filter((entry) => entry.op === 'edit').length,
  create: items.value.filter((entry) => entry.op === 'create_file').length,
  deferred: items.value.filter((entry) => entry.op === 'deferred').length,
}))

const reviewLabel = computed(() => {
  const status = view.value?.document.doc_review_status ?? ''
  const key = `main.doc_header.status_${status}`
  return status && te(key) ? t(key) : status
})
const historyLabel = computed(() => {
  const value = view.value?.history?.source_history_state ?? 'unknown'
  const key = `main.tr2_body.history_state.${value}`
  return te(key) ? t(key) : value
})
const lockKey = computed(() => {
  if (!view.value) return null
  if (props.readOnly) return 'main.tr2_body.lock.read_only'
  const reason = view.value.mutation?.reason ?? (view.value.document.editable ? null : 'not_editable')
  return reason && te(`main.tr2_body.lock.${reason}`) ? `main.tr2_body.lock.${reason}` : null
})
const retryBlockedKey = computed(() => {
  const reason = retry.value?.reason
  if (!reason || !['recovery_required', 'not_retryable', 'revision_changed'].includes(reason)) return null
  return `main.tr2_body.retry.blocked_${reason}`
})
const confirmText = computed(() => {
  const pending = confirming.value
  if (!pending) return ''
  if (pending.kind === 'delete') return t('main.tr2_body.confirm.delete_item', { id: pending.id })
  if (pending.kind === 'retry') return t('main.tr2_body.confirm.retry', { revision: view.value?.document.revision_no ?? '' })
  return t('main.tr2_body.confirm.reset')
})

function phaseLabel(phase?: string | null): string {
  const key = `main.tr2_body.phase.${phase ?? ''}`
  return phase && te(key) ? t(key) : (phase ?? '')
}
function attemptLabel(attempt: Row): string {
  const stateKey = `main.tr2_body.attempt_state.${attempt.state}`
  return t('main.tr2_body.summary.attempt_label', {
    round: attempt.approval_round ?? '', state: te(stateKey) ? t(stateKey) : attempt.state, phase: phaseLabel(attempt.phase),
  })
}
function opLabel(op: ItemOp): string {
  return t(op === 'edit' ? 'main.tr2_body.files.op_edit' : op === 'create_file' ? 'main.tr2_body.files.op_create' : 'main.tr2_body.files.op_deferred')
}
function deferredReasonLabel(reason: string): string {
  const key = `main.tr2_body.deferred_reason.${reason}`
  return te(key) ? t(key) : reason
}
function gateStatus(index: number): 'passed' | 'failed' | 'waiting' {
  const result = gate.value?.commands[index]
  if (!result) return 'waiting'
  return !result.timed_out && result.exit_code === 0 ? 'passed' : 'failed'
}

function describe(exc: any): string {
  const data = exc?.response?.data ?? {}
  const code = data.code ?? data.error?.code
  const details = data.details ?? data.error?.details ?? {}
  switch (code) {
    case 'tr2_spec_changed':
      stale.value = true
      return t('main.tr2_body.errors.stale', { revision: details.current_revision_no ?? '?' })
    case 'tr2_spec_immutable': return t('main.tr2_body.errors.immutable', { reason: details.reason ?? code })
    case 'tr2_in_progress': return t('main.tr2_body.errors.in_progress')
    case 'tr2_spec_invalid': return t('main.tr2_body.errors.invalid', { loc: details.loc ?? '', reason: details.reason ?? data.message ?? '' })
    case 'tr2_item_not_found': return t('main.tr2_body.errors.not_found')
    case 'tr2_path_unsafe': return t('main.tr2_body.errors.path_unsafe', { loc: details.loc ?? '' })
  }
  const detail = typeof data.detail === 'string' ? data.detail : null
  return data.message ?? data.error?.message ?? detail ?? exc?.message ?? String(exc)
}

async function refresh() {
  const current = ++generation
  loading.value = true
  try {
    const response = await getRequest<Tr2View>(base.value)
    if (current !== generation) return
    view.value = response.data
    error.value = ''
    stale.value = false
    staleWhileEditing.value = false
    if (selectedKey.value && !selected.value) { selectedKey.value = null; fileDetail.value = null }
  } catch (exc) {
    if (current === generation) error.value = describe(exc)
  } finally {
    if (current === generation) loading.value = false
  }
}
async function reload() {
  wholeEditorOpen.value = false
  itemEditor.value = null
  await refresh()
}
async function runDiagnostic() {
  try { diagnostic.value = (await postRequest<Row>(`${base.value}/precheck`, {})).data; error.value = '' }
  catch (exc) { diagnostic.value = null; error.value = describe(exc) }
}
async function selectItem(entry: Entry) {
  selectedKey.value = `${entry.collection}:${entry.item.id}`
  itemEditor.value = null
  fileDetail.value = null
  if (entry.op === 'deferred' || !entry.item.file) return
  try {
    fileDetail.value = (await getRequest<Row>(`${base.value}/files/${String(entry.item.file).split('/').map(encodeURIComponent).join('/')}`)).data
  } catch (exc) { error.value = describe(exc) }
}

function announce(changed: boolean) {
  if (!changed) return
  window.dispatchEvent(new CustomEvent('fg:document_content_changed', {
    detail: { doc_id: props.tab.id, project: props.tab.projectId, revision_no: view.value?.document.revision_no ?? null, source: 'tr2_body' },
  }))
}

/** Every mutation: server-side merge + validation + revision CAS; then re-read the model. */
async function mutate(call: (revision: number) => Promise<{ data: any }>): Promise<boolean> {
  if (!view.value || locked.value) return false
  saving.value = true
  error.value = ''
  notice.value = ''
  try {
    const response = await call(view.value.document.revision_no)
    diagnostic.value = null
    await refresh()
    notice.value = t('main.tr2_body.saved', { revision: response.data?.new_revision ?? view.value?.document.revision_no ?? '' })
    announce(true)
    return true
  } catch (exc) {
    error.value = describe(exc)
    return false
  } finally {
    saving.value = false
  }
}

function specPayload(spec: Row) {
  return { tr2_version: view.value!.body.tr2_version, source_t2_doc_id: view.value!.body.source_t2_doc_id, edit_spec: spec }
}
function parseObject(text: string): Row {
  const parsed = JSON.parse(text)
  if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error(t('main.tr2_body.errors.object_required'))
  return parsed
}
function jsonError(exc: any): string {
  return t('main.tr2_body.errors.invalid_json', { reason: exc?.message ?? String(exc) })
}

function openWholeEditor(fresh: boolean) {
  if (!view.value || locked.value) return
  const spec = fresh
    ? { termination: 'needs_more_work', edits: [], deferred: [], gate: { commands: [], apply: false } }
    : view.value.body.edit_spec
  editorText.value = JSON.stringify(spec, null, 2)
  itemEditor.value = null
  wholeEditorOpen.value = true
}
async function saveWhole() {
  let spec: Row
  try { spec = parseObject(editorText.value) } catch (exc) { error.value = jsonError(exc); return }
  if (await mutate((revision) => putRequest(base.value, { expected_revision: revision, body: specPayload(spec) }))) wholeEditorOpen.value = false
}
async function uploadSpec(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file || locked.value) return
  let spec: Row
  try {
    const parsed = parseObject(await file.text())
    spec = parseObject(JSON.stringify(parsed.edit_spec ?? parsed))
  } catch (exc) { error.value = jsonError(exc); return }
  // Only edit_spec travels: fingerprints, attempts and history in the file are ignored.
  await mutate((revision) => putRequest(base.value, { expected_revision: revision, body: specPayload(spec) }))
}
function downloadJson(name: string, value: unknown) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2) + '\n'], { type: 'application/json' }))
  const link = document.createElement('a')
  link.href = url
  link.download = name
  link.click()
  URL.revokeObjectURL(url)
}
function downloadSpec() {
  if (view.value) downloadJson(`${props.tab.id}-edit-spec.json`, specPayload(view.value.body.edit_spec))
}
function downloadItem(entry: Entry) {
  downloadJson(`${props.tab.id}-${entry.item.id}.json`, { collection: entry.collection, item: entry.item })
}

function openItemEditor(entry: Entry | null) {
  if (locked.value) return
  wholeEditorOpen.value = false
  itemEditor.value = { key: ++editorSeq, op: entry?.op ?? 'edit', item: entry ? { ...entry.item } : null }
}
function itemUrl(id: string) {
  return `${base.value}/items/${encodeURIComponent(id)}`
}
async function saveItem(value: { collection: ItemCollection; item: Row; originalId: string | null }) {
  const ok = await mutate((revision) => (value.originalId
    ? putRequest(itemUrl(value.originalId), { expected_revision: revision, collection: value.collection, item: value.item })
    : postRequest(`${base.value}/items`, { expected_revision: revision, collection: value.collection, item: value.item })))
  if (ok) {
    itemEditor.value = null
    selectedKey.value = `${value.collection}:${value.item.id}`
  }
}
async function uploadItem(event: Event, target: Entry | null) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file || locked.value) return
  let item: Row
  let collection: ItemCollection
  try {
    const parsed = parseObject(await file.text())
    item = parseObject(JSON.stringify(parsed.item ?? parsed))
    const declared = parsed.item ? parsed.collection : undefined
    collection = declared === 'deferred' || declared === 'edits' ? declared : (target?.collection ?? (item.reason && !item.kind ? 'deferred' : 'edits'))
  } catch (exc) { error.value = jsonError(exc); return }
  const replaceId = target?.item.id ?? (items.value.some((entry) => entry.item.id === item.id) ? String(item.id) : null)
  await saveItem({ collection, item, originalId: replaceId })
}

function ask(pending: Pending) { confirming.value = pending }
async function confirmYes() {
  const pending = confirming.value
  confirming.value = null
  if (!pending) return
  if (pending.kind === 'reset') {
    await mutate((revision) => deleteRequest(`${base.value}/spec?expected_revision=${revision}`))
  } else if (pending.kind === 'delete') {
    if (await mutate((revision) => deleteRequest(`${itemUrl(pending.id)}?expected_revision=${revision}`))) selectedKey.value = null
  } else {
    await runRetry()
  }
}
async function runRetry() {
  const current = view.value
  const plan = current?.approval.retry
  if (!current || !plan?.allowed || !plan.request_key || retrying.value) return
  retrying.value = true
  error.value = ''
  try {
    // The server named this request key after the attempt it retries: a double click or a
    // network retry replays that one attempt instead of starting another round.
    await postRequest('/api/v1/documents/review_transitions/approve', {
      doc_id: props.tab.id, comment: null, expected_revision: current.document.revision_no, request_key: plan.request_key,
    })
  } catch (exc) {
    error.value = describe(exc)
  } finally {
    retrying.value = false
    await refresh()
    window.dispatchEvent(new CustomEvent('fg:open_docs_refresh', { detail: { project: props.tab.projectId ?? null, doc_id: props.tab.id } }))
  }
}

function onServerChange(detail: any) {
  if (!detail || detail.source === 'tr2_body') return
  if (detail.doc_id && detail.doc_id !== props.tab.id) return
  if (detail.project && props.tab.projectId && detail.project !== props.tab.projectId) return
  const revision = detail.revision_no
  if (typeof revision === 'number' && view.value && revision <= view.value.document.revision_no) return
  if (editing.value || saving.value) { staleWhileEditing.value = true; return }
  void refresh()
}
const onContentChanged = (event: Event) => { const detail = (event as CustomEvent).detail; if (detail?.doc_id === props.tab.id) onServerChange(detail) }
const onReviewChanged = (event: Event) => { const detail = (event as CustomEvent).detail; if (detail?.doc_id === props.tab.id) onServerChange({ doc_id: detail.doc_id }) }
const onOpenDocsRefresh = (event: Event) => onServerChange({ ...((event as CustomEvent).detail ?? {}), revision_no: undefined })

onMounted(() => {
  window.addEventListener('fg:document_content_changed', onContentChanged)
  window.addEventListener('fg:doc_review_status_changed', onReviewChanged)
  window.addEventListener('fg:open_docs_refresh', onOpenDocsRefresh)
})
onBeforeUnmount(() => {
  generation++
  window.removeEventListener('fg:document_content_changed', onContentChanged)
  window.removeEventListener('fg:doc_review_status_changed', onReviewChanged)
  window.removeEventListener('fg:open_docs_refresh', onOpenDocsRefresh)
})
watch(() => props.tab.id, () => {
  diagnostic.value = null
  selectedKey.value = null
  itemEditor.value = null
  wholeEditorOpen.value = false
  void refresh()
}, { immediate: true })
</script>

<style scoped>
.tr2 { padding: 16px; display: grid; gap: 16px; }
.tr2-header, .tr2-actions { display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap; }
.tr2-header small { display: block; color: var(--text-m); }
.tr2-actions input[type=file], .tr2-section input[type=file] { display: none; }
.tr2-pill { margin-left: 8px; padding: 2px 8px; border-radius: 999px; font-size: .75rem; }
.tr2-summary { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 8px; }
.tr2-summary div { border: 1px solid var(--border, #d9e1e8); border-radius: 8px; padding: 10px; }
.tr2-summary span, .tr2-summary strong { display: block; }
.tr2-summary span, .tr2-note { color: var(--text-m); font-size: .8rem; }
.tr2-strip { display: grid; gap: 4px; border-radius: 8px; padding: 10px 12px; border: 1px solid transparent; }
.tr2-tone-safe { background: #ecfdf5; border-color: #a7f3d0; color: #065f46; }
.tr2-tone-live { background: #eff6ff; border-color: #bfdbfe; color: #1e40af; }
.tr2-tone-warn { background: #fffbeb; border-color: #fde68a; color: #92400e; }
.tr2-tone-danger { background: #fef2f2; border-color: #fecaca; color: #991b1b; }
.tr2-tone-blocked { background: #3f1d1d; border-color: #7f1d1d; color: #fee2e2; }
.tr2-tone-done { background: #f0fdf4; border-color: #bbf7d0; color: #166534; }
.tr2-tone-past { background: #f3f4f6; border-color: #d1d5db; color: #374151; }
.tr2-code em, .tr2-attempts em { margin-left: 6px; font-style: normal; font-size: .75rem; }
.tr2-retry, .tr2-confirm, .tr2-error { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.tr2-confirm { border: 1px solid var(--border, #d9e1e8); border-radius: 8px; padding: 8px 12px; }
.tr2-error { color: var(--danger, #b91c1c); }
.tr2-notice { color: var(--success, #15803d); }
.tr2-split { display: grid; grid-template-columns: minmax(220px, 1fr) 2fr; gap: 16px; }
.tr2-section { border-top: 1px solid var(--border, #d9e1e8); padding-top: 12px; min-width: 0; }
.tr2-section h3 { margin: 0 0 8px; font-size: 1rem; }
.tr2-section h3 small { font-weight: normal; color: var(--text-m); }
.tr2-files, .tr2-gate, .tr2-attempts { list-style: none; padding: 0; margin: 0 0 8px; }
.tr2-files li button { display: flex; gap: 8px; align-items: center; width: 100%; border: 0; background: none; padding: 6px 4px; cursor: pointer; text-align: left; }
.tr2-files li.is-selected button { background: var(--bg, #f6f8fa); }
.tr2-path { overflow-wrap: anywhere; }
.tr2-op { font-size: .7rem; font-weight: 700; padding: 1px 6px; border-radius: 4px; }
.tr2-op-edit { background: #dbeafe; color: #1e40af; }
.tr2-op-create_file { background: #dcfce7; color: #166534; }
.tr2-op-deferred { background: #f3f4f6; color: #4b5563; }
.tr2-diff { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.tr2-diff--new { grid-template-columns: 1fr; }
.tr2-gate li, .tr2-attempts li { display: flex; gap: 8px; flex-wrap: wrap; padding: 4px 0; }
.tr2-gate li.is-passed span { color: #15803d; }
.tr2-gate li.is-failed span { color: #b91c1c; }
.tr2 pre { white-space: pre-wrap; overflow-wrap: anywhere; background: var(--bg, #f6f8fa); padding: 10px; border-radius: 6px; max-height: 320px; overflow: auto; }
.tr2 textarea { box-sizing: border-box; width: 100%; font-family: monospace; }
@media (max-width: 900px) { .tr2-split, .tr2-diff { grid-template-columns: 1fr; } }
</style>
