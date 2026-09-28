<template>
  <section class="tr2rp" :class="`tr2rp--${mode}`" :data-testid="mode === 'recovery' ? 'tr2-recovery' : 'tr2-revision-history'">
    <template v-if="mode === 'recovery'">
      <div class="tr2rp-state" role="alert" data-testid="tr2-recovery-state">
        <strong>{{ t('main.tr2_body.recovery.heading') }} · {{ stateTitle }}</strong>
        <span>{{ stateText }}</span>
        <small v-if="detailText" class="tr2rp-detail">{{ detailText }}</small>
        <span class="tr2rp-blocked">{{ t('main.tr2_body.recovery.blocked') }}</span>
      </div>
      <p v-if="state" class="tr2rp-note" data-testid="tr2-recovery-verdict">
        {{ state.recoverable ? t('main.tr2_body.recovery.recoverable') : t('main.tr2_body.recovery.unrecoverable') }}
      </p>
    </template>
    <p v-else class="tr2rp-note">{{ t('main.tr2_body.recovery.history_hint') }}</p>

    <p v-if="error" class="tr2rp-error" role="alert">{{ error }}</p>
    <p v-if="loading && !state">{{ t('main.tr2_body.recovery.loading') }}</p>

    <div v-if="confirming" class="tr2rp-confirm" role="alertdialog" data-testid="tr2-recovery-confirm">
      <span>{{ confirming.kind === 'restore' ? t('main.tr2_body.confirm.restore', { revision: confirming.revision }) : t('main.tr2_body.confirm.new_proposal') }}</span>
      <button type="button" class="btn btn-primary btn-sm" data-testid="tr2-recovery-confirm-yes" :disabled="busy" @click="confirmYes">{{ t('main.tr2_body.actions.confirm') }}</button>
      <button type="button" class="btn btn-outline btn-sm" @click="confirming = null">{{ t('main.tr2_body.actions.cancel') }}</button>
    </div>

    <template v-if="state">
      <p v-if="!state.revisions.length">{{ t('main.tr2_body.recovery.empty') }}</p>
      <ol v-else class="tr2rp-list">
        <li v-for="item in state.revisions" :key="item.revision_no" :class="{ 'is-unusable': !item.usable }" :data-testid="`tr2-revision-${item.revision_no}`">
          <div class="tr2rp-row">
            <strong>{{ t('main.tr2_body.recovery.revision', { revision: item.revision_no }) }}</strong>
            <span v-if="item.is_current" class="tr2rp-badge">{{ t('main.tr2_body.recovery.current') }}</span>
            <span v-if="mode === 'recovery' && item.revision_no === state.recommended_revision_no" class="tr2rp-badge tr2rp-badge--ok">{{ t('main.tr2_body.recovery.recommended') }}</span>
            <span class="tr2rp-meta">{{ formatTime(item.created_at) }} · {{ item.origin === 'ai' ? t('main.tr2_body.recovery.origin_ai') : t('main.tr2_body.recovery.origin_human') }}<template v-if="item.size != null"> · {{ t('main.tr2_body.recovery.size', { size: item.size }) }}</template></span>
            <span :class="item.usable ? 'tr2rp-ok' : 'tr2rp-bad'">{{ item.usable ? t('main.tr2_body.recovery.usable') : t('main.tr2_body.recovery.unusable', { problem: problemLabel(item.problem) }) }}</span>
          </div>
          <div class="tr2rp-actions">
            <button type="button" class="btn btn-outline btn-sm" :data-testid="`tr2-revision-view-${item.revision_no}`" @click="toggleView(item.revision_no)">{{ opened === item.revision_no ? t('main.tr2_body.recovery.hide') : t('main.tr2_body.recovery.view') }}</button>
            <button type="button" class="btn btn-outline btn-sm" :data-testid="`tr2-revision-download-${item.revision_no}`" @click="downloadRevision(item.revision_no)">{{ t('main.tr2_body.recovery.download') }}</button>
            <button type="button" class="btn btn-primary btn-sm" :data-testid="`tr2-revision-restore-${item.revision_no}`" :disabled="!canRestore(item)" @click="confirming = { kind: 'restore', revision: item.revision_no }">{{ t('main.tr2_body.recovery.restore') }}</button>
          </div>
          <pre v-if="opened === item.revision_no && openedContent != null" class="tr2rp-pre" :data-testid="`tr2-revision-content-${item.revision_no}`">{{ openedContent }}</pre>
        </li>
      </ol>

      <template v-if="mode === 'recovery'">
        <h4>{{ t('main.tr2_body.recovery.raw_heading') }}</h4>
        <p v-if="!state.raw.available" data-testid="tr2-raw-none">{{ t('main.tr2_body.recovery.raw_none') }}</p>
        <div v-else class="tr2rp-actions">
          <button type="button" class="btn btn-outline btn-sm" data-testid="tr2-raw-view" @click="toggleRaw">{{ rawContent != null ? t('main.tr2_body.recovery.hide') : t('main.tr2_body.recovery.raw_view') }}</button>
          <button type="button" class="btn btn-outline btn-sm" data-testid="tr2-raw-download" @click="downloadRaw">{{ t('main.tr2_body.recovery.raw_download') }}</button>
        </div>
        <pre v-if="rawContent != null" class="tr2rp-pre" data-testid="tr2-raw-content">{{ rawContent }}</pre>
        <div class="tr2rp-actions">
          <button type="button" class="btn btn-outline btn-sm" data-testid="tr2-new-proposal" :disabled="!mutable" @click="confirming = { kind: 'new' }">{{ t('main.tr2_body.recovery.new_proposal') }}</button>
        </div>
      </template>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { getRequest, postRequest } from '@shared/api'
import { BODY_ERROR_KEYS, describeTr2Error, type Row } from './tr2State'

interface RevisionInfo {
  revision_no: number; created_at: string | null; origin: 'ai' | 'human'; size: number | null
  sha256: string | null; usable: boolean; problem: string | null; is_current: boolean
}
interface RecoveryState {
  revision_no: number; state: string; error: { code: string; loc: string | null; reason: string | null } | null
  revisions: RevisionInfo[]; recommended_revision_no: number | null; recoverable: boolean
  raw: { available: boolean; size: number | null; filename: string | null }
  mutation: { allowed: boolean; reason: string | null }
}
type Pending = { kind: 'restore'; revision: number } | { kind: 'new' }

const props = defineProps<{ docId: string; mode: 'recovery' | 'history'; readOnly: boolean }>()
const emit = defineEmits<{ (e: 'changed', value: { revision: number; message: string }): void }>()
const { t, te } = useI18n()

const state = ref<RecoveryState | null>(null)
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const opened = ref<number | null>(null)
const openedContent = ref<string | null>(null)
const rawContent = ref<string | null>(null)
const confirming = ref<Pending | null>(null)

const base = computed(() => `/api/v1/documents/${encodeURIComponent(props.docId)}/tr2`)
const mutable = computed(() => !props.readOnly && !busy.value && Boolean(state.value?.mutation.allowed))
const stateTitle = computed(() => {
  const code = state.value?.error?.code
  return code && BODY_ERROR_KEYS[code] ? t(`main.tr2_body.errors.${BODY_ERROR_KEYS[code]}`) : ''
})
const stateText = computed(() => {
  const code = state.value?.error?.code
  const key = `main.tr2_body.recovery.state.${code ?? ''}`
  return code && te(key) ? t(key) : ''
})
const detailText = computed(() => {
  const err = state.value?.error
  // Where a damaged or invalid body breaks is worth showing; a storage mismatch says it all.
  if (!err || !['tr2_body_corrupt', 'tr2_body_schema_invalid'].includes(err.code)) return ''
  return t('main.tr2_body.recovery.detail', { loc: err.loc ?? '', reason: err.reason ?? '' })
})

function problemLabel(problem: string | null): string {
  return problem && BODY_ERROR_KEYS[problem] ? t(`main.tr2_body.errors.${BODY_ERROR_KEYS[problem]}`) : (problem ?? '')
}
function formatTime(value: string | null): string {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}
function canRestore(item: RevisionInfo): boolean {
  if (!item.usable || !mutable.value) return false
  // A healthy current revision restored onto itself changes nothing.
  return props.mode === 'recovery' || !item.is_current
}
function fail(exc: unknown) { error.value = describeTr2Error(exc, t, te).text }

async function load() {
  loading.value = true
  try {
    state.value = (await getRequest<RecoveryState>(`${base.value}/recovery`)).data
    error.value = ''
  } catch (exc) { fail(exc) } finally { loading.value = false }
}
async function readRevision(revision: number): Promise<Row | null> {
  try { return (await getRequest<Row>(`${base.value}/revisions/${revision}`)).data } catch (exc) { fail(exc); return null }
}
async function toggleView(revision: number) {
  if (opened.value === revision) { opened.value = null; openedContent.value = null; return }
  const data = await readRevision(revision)
  if (!data) return
  opened.value = revision
  openedContent.value = prettyJson(data.content)
}
function prettyJson(text: string | null | undefined): string {
  if (text == null) return ''
  try { return JSON.stringify(JSON.parse(text), null, 2) } catch { return text }
}
function download(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: 'application/json' }))
  const link = document.createElement('a')
  link.href = url
  link.download = name
  link.click()
  URL.revokeObjectURL(url)
}
async function downloadRevision(revision: number) {
  const data = await readRevision(revision)
  // The exact saved bytes, not a re-serialisation: this is the recovery evidence.
  if (data?.content != null) download(`${props.docId}.r${revision}.json`, data.content)
}
async function readRaw(): Promise<Row | null> {
  try { return (await getRequest<Row>(`${base.value}/raw`)).data } catch (exc) { fail(exc); return null }
}
async function toggleRaw() {
  if (rawContent.value != null) { rawContent.value = null; return }
  const data = await readRaw()
  if (data) rawContent.value = data.content ?? ''
}
async function downloadRaw() {
  const data = await readRaw()
  if (data) download(data.filename || `${props.docId}.raw.txt`, data.content ?? '')
}
async function confirmYes() {
  const pending = confirming.value
  if (!pending || !state.value) return
  busy.value = true
  error.value = ''
  try {
    if (pending.kind === 'restore') {
      const response = await postRequest<Row>(`${base.value}/recovery/restore`, { expected_revision: state.value.revision_no, revision_no: pending.revision })
      emit('changed', { revision: response.data.new_revision, message: t('main.tr2_body.recovery.restored', { from: pending.revision, revision: response.data.new_revision }) })
    } else {
      const response = await postRequest<Row>(`${base.value}/recovery/new`, { expected_revision: state.value.revision_no })
      emit('changed', { revision: response.data.new_revision, message: t('main.tr2_body.recovery.created', { revision: response.data.new_revision }) })
    }
    confirming.value = null
    opened.value = null
    openedContent.value = null
    await load()
  } catch (exc) {
    fail(exc)
    confirming.value = null
    await load()
  } finally {
    busy.value = false
  }
}

onMounted(load)
defineExpose({ load })
</script>

<style scoped>
.tr2rp { display: grid; gap: 10px; }
.tr2rp-state { display: grid; gap: 4px; padding: 11px 14px; border-radius: 8px; background: #fef2f2; color: #991b1b; border: 1px solid #fecaca; }
.tr2rp-detail { color: #7f1d1d; font-family: monospace; }
.tr2rp-blocked { font-size: .8rem; }
.tr2rp-note { color: var(--text-m); font-size: .8rem; margin: 0; }
.tr2rp-error { color: var(--danger, #b91c1c); }
.tr2rp-confirm { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; border: 1px solid var(--border, #d9e1e8); border-radius: 8px; padding: 8px 12px; }
.tr2rp-list { list-style: none; padding: 0; margin: 0; display: grid; gap: 6px; }
.tr2rp-list li { border: 1px solid var(--border, #d9e1e8); border-radius: 8px; padding: 8px 10px; display: grid; gap: 6px; }
.tr2rp-list li.is-unusable { background: var(--surface-h, #f6f8fa); }
.tr2rp-row, .tr2rp-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.tr2rp-meta { color: var(--text-m); font-size: .78rem; }
.tr2rp-badge { font-size: .68rem; font-weight: 700; padding: 1px 7px; border-radius: 999px; background: #e0e7ff; color: #3730a3; }
.tr2rp-badge--ok { background: #d1fae5; color: #047857; }
.tr2rp-ok { color: #15803d; font-size: .78rem; }
.tr2rp-bad { color: #b91c1c; font-size: .78rem; }
.tr2rp-pre { white-space: pre-wrap; overflow-wrap: anywhere; background: var(--bg, #f6f8fa); padding: 10px; border-radius: 6px; max-height: 320px; overflow: auto; margin: 0; }
</style>
