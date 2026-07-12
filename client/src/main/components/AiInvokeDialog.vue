<template>
  <teleport to="body">
    <div v-if="visible" class="modal-bg" @click.self="onBackdrop">
      <div class="modal-box modal-aiv">
        <!-- ── Header ── -->
        <div class="modal-hd">
          <span class="modal-title">
            <i class="fa-solid fa-robot" style="color:var(--primary); margin-right:6px;"></i>
            {{ t('main.ai_invoke_dialog.title') }}
          </span>
          <button class="modal-close" type="button" @click="close">
            <i class="fa-solid fa-xmark"></i>
          </button>
        </div>

        <!-- ── Body ── -->
        <div class="modal-bd aiv-body">
          <!-- Pre-start: mode pick. Kept minimal — the run itself is the 3-state flow. -->
          <template v-if="phase === 'setup'">
            <div class="aiv-target-row">
              <span class="aiv-target-label">{{ t('main.ai_invoke_dialog.target_doc') }}</span>
              <span class="aiv-target-id">{{ docRef }}</span>
            </div>
            <label class="aiv-mode" :class="{ active: mode === 'single' }">
              <input v-model="mode" type="radio" value="single" />
              <span class="aiv-mode-text">
                <span class="aiv-mode-title">{{ t('main.ai_invoke_dialog.mode_single') }}</span>
                <span class="aiv-mode-desc">{{ t('main.ai_invoke_dialog.mode_single_desc') }}</span>
              </span>
            </label>
            <label class="aiv-mode" :class="{ active: mode === 'continuous' }">
              <input v-model="mode" type="radio" value="continuous" />
              <span class="aiv-mode-text">
                <span class="aiv-mode-title">{{ t('main.ai_invoke_dialog.mode_continuous') }}</span>
                <span class="aiv-mode-desc">{{ t('main.ai_invoke_dialog.mode_continuous_desc') }}</span>
              </span>
            </label>
            <div v-if="mode === 'continuous'" class="aiv-seq-row">
              <label class="aiv-seq-label" for="aiv-target-seq">{{ t('main.ai_invoke_dialog.target_seq_label') }}</label>
              <input
                id="aiv-target-seq"
                v-model.number="targetSeq"
                type="number"
                class="form-ctrl aiv-seq-input"
                min="1"
              />
              <span class="aiv-seq-hint">{{ t('main.ai_invoke_dialog.target_seq_hint') }}</span>
            </div>
            <div v-if="startError" class="aiv-error">
              <i class="fa-solid fa-triangle-exclamation"></i> {{ startError }}
            </div>
          </template>

          <!-- Running -->
          <template v-else-if="phase === 'running'">
            <div class="aiv-running">
              <i class="fa-solid fa-circle-notch fa-spin aiv-spinner"></i>
              <div class="aiv-running-provider">
                {{ providerName || '—' }}
                <span v-if="attemptNo > 1" class="aiv-attempt-badge">
                  {{ t('main.ai_invoke_dialog.attempt_no', { n: attemptNo }) }}
                </span>
              </div>
              <div class="aiv-running-status">
                {{ cancelRequested ? t('main.ai_invoke_dialog.cancelling') : t('main.ai_invoke_dialog.running') }}
              </div>
              <div class="aiv-elapsed">{{ elapsedText }}</div>
              <div v-if="docsTarget > 1" class="aiv-progress">
                {{ t('main.ai_invoke_dialog.docs_progress', { reached: docsSoFar, target: docsTarget }) }}
              </div>
            </div>
          </template>

          <!-- Finished: success / failure with document-reach as the verdict -->
          <template v-else>
            <div class="aiv-result-head" :class="`aiv-outcome-${result?.outcome}`">
              <i
                class="fa-solid"
                :class="result?.outcome === 'complete'
                  ? 'fa-circle-check'
                  : result?.outcome === 'partial' ? 'fa-triangle-exclamation' : 'fa-circle-xmark'"
              ></i>
              <span class="aiv-result-title">
                {{ result?.outcome === 'complete'
                  ? t('main.ai_invoke_dialog.outcome_complete')
                  : result?.outcome === 'partial'
                    ? t('main.ai_invoke_dialog.outcome_partial')
                    : t('main.ai_invoke_dialog.outcome_none') }}
              </span>
              <span v-if="docsTarget > 0" class="aiv-result-count">
                {{ result?.docs_reached ?? 0 }}/{{ docsTarget }}
              </span>
            </div>

            <div v-if="endReasonText" class="aiv-end-reason">{{ endReasonText }}</div>

            <div v-if="(result?.reached_doc_ids?.length ?? 0) > 0" class="aiv-section">
              <div class="aiv-section-title">{{ t('main.ai_invoke_dialog.reached_docs') }}</div>
              <div class="aiv-doc-list">
                <button
                  v-for="docId in result!.reached_doc_ids"
                  :key="docId"
                  type="button"
                  class="aiv-doc-link"
                  @click="emit('open-doc', docId)"
                >
                  <i class="fa-regular fa-file-lines"></i> {{ docId }}
                </button>
              </div>
            </div>

            <div class="aiv-section">
              <div class="aiv-section-title">{{ t('main.ai_invoke_dialog.last_message') }}</div>
              <pre v-if="result?.last_message_received" class="aiv-message">{{ result?.last_message }}</pre>
              <div v-else class="aiv-message aiv-message--none">
                {{ t('main.ai_invoke_dialog.last_message_none') }}
              </div>
            </div>

            <div v-if="(result?.fallback_history?.length ?? 0) > 0" class="aiv-section">
              <button type="button" class="aiv-fallback-toggle" @click="fallbackOpen = !fallbackOpen">
                <i class="fa-solid" :class="fallbackOpen ? 'fa-chevron-down' : 'fa-chevron-right'"></i>
                {{ t('main.ai_invoke_dialog.fallback_history', { count: result!.fallback_history.length }) }}
              </button>
              <ul v-if="fallbackOpen" class="aiv-fallback-list">
                <li v-for="(f, i) in result!.fallback_history" :key="i">
                  <span class="aiv-fallback-provider">{{ f.provider_name }}</span>
                  <span class="aiv-fallback-reason">{{ fallbackReasonLabel(f.reason) }}</span>
                  <span v-if="f.detail" class="aiv-fallback-detail">{{ f.detail }}</span>
                </li>
              </ul>
            </div>

            <div v-if="result?.end_reason === 'all_providers_failed'" class="aiv-hint">
              {{ t('main.ai_invoke_dialog.check_ai_settings') }}
            </div>

            <div v-if="result?.source_dirty" class="aiv-dirty-banner">
              <i class="fa-solid fa-triangle-exclamation"></i>
              {{ t('main.ai_invoke_dialog.source_dirty', { count: result?.source_dirty_files?.length ?? 0 }) }}
              <span class="aiv-dirty-files">{{ (result?.source_dirty_files ?? []).join(', ') }}</span>
            </div>
          </template>
        </div>

        <!-- ── Footer ── -->
        <div class="modal-ft">
          <template v-if="phase === 'setup'">
            <button type="button" class="btn btn-ghost" @click="close">{{ t('common.cancel') }}</button>
            <button type="button" class="btn btn-primary" :disabled="starting || !canStart" @click="start">
              <i class="fa-solid fa-bolt"></i> {{ t('main.ai_invoke_dialog.btn_start') }}
            </button>
          </template>
          <template v-else-if="phase === 'running'">
            <button type="button" class="btn btn-danger" :disabled="cancelRequested" @click="cancelRun">
              <i class="fa-solid fa-stop"></i> {{ t('main.ai_invoke_dialog.btn_cancel_run') }}
            </button>
          </template>
          <template v-else>
            <button type="button" class="btn btn-primary" @click="close">{{ t('common.close') }}</button>
          </template>
        </div>
      </div>
    </div>
  </teleport>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getRequest, postRequest } from '@shared/api'
import { useToast } from './common/useToast'

interface FallbackEntry {
  provider_id: string | null
  provider_name: string | null
  reason: string
  detail?: string | null
}

interface FinishedPayload {
  run_id: string
  outcome: 'complete' | 'partial' | 'none'
  docs_reached: number
  docs_target: number
  reached_doc_ids: string[]
  end_reason: string
  exit_code: number | null
  last_message_received: boolean
  last_message: string | null
  provider_id: string | null
  attempt_no: number
  fallback_history: FallbackEntry[]
  source_dirty: boolean | null
  source_dirty_files?: string[]
  scratch_retained?: string
  duration_ms: number
}

const props = defineProps<{
  visible: boolean
  project: string
  module?: string | null
  group: string
  docRef: string
  actionScope: 'new' | 'edit'
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
  'open-doc': [docId: string]
}>()

const { t } = useI18n()
const { showToast } = useToast()

const phase = ref<'setup' | 'running' | 'finished'>('setup')
const mode = ref<'single' | 'continuous'>('single')
const targetSeq = ref<number | null>(null)
const starting = ref(false)
const startError = ref('')

const runId = ref<string | null>(null)
const providerName = ref('')
const attemptNo = ref(1)
const docsTarget = ref(1)
const docsSoFar = ref(0)
const cancelRequested = ref(false)
const elapsedMs = ref(0)
const result = ref<FinishedPayload | null>(null)
const fallbackOpen = ref(false)

let elapsedTimer: ReturnType<typeof setInterval> | null = null
let pollTimer: ReturnType<typeof setInterval> | null = null
let startedAtMono = 0

const canStart = computed(() =>
  mode.value === 'single' || (targetSeq.value != null && targetSeq.value > 0),
)

const elapsedText = computed(() => {
  const total = Math.floor(elapsedMs.value / 1000)
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${m}:${String(s).padStart(2, '0')}`
})

const endReasonText = computed(() => {
  switch (result.value?.end_reason) {
    case 'cancelled': return t('main.ai_invoke_dialog.end_cancelled')
    case 'timeout': return t('main.ai_invoke_dialog.end_timeout')
    case 'all_providers_failed': return t('main.ai_invoke_dialog.end_all_failed')
    default: return ''
  }
})

function fallbackReasonLabel(reason: string): string {
  switch (reason) {
    case 'spawn_failed': return t('main.ai_invoke_dialog.reason_spawn_failed')
    case 'fast_fail': return t('main.ai_invoke_dialog.reason_fast_fail')
    case 'api_error': return t('main.ai_invoke_dialog.reason_api_error')
    default: return reason
  }
}

function resetState() {
  phase.value = 'setup'
  mode.value = 'single'
  targetSeq.value = null
  starting.value = false
  startError.value = ''
  runId.value = null
  providerName.value = ''
  attemptNo.value = 1
  docsTarget.value = 1
  docsSoFar.value = 0
  cancelRequested.value = false
  elapsedMs.value = 0
  result.value = null
  fallbackOpen.value = false
}

function startTimers() {
  stopTimers()
  startedAtMono = Date.now() - elapsedMs.value
  elapsedTimer = setInterval(() => {
    elapsedMs.value = Date.now() - startedAtMono
  }, 1000)
  // Poll as an SSE-gap fallback (reconnect windows lose events; scenario 8 restore).
  pollTimer = setInterval(() => void pollStatus(), 5000)
}

function stopTimers() {
  if (elapsedTimer) { clearInterval(elapsedTimer); elapsedTimer = null }
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

async function start() {
  if (starting.value || !canStart.value) return
  starting.value = true
  startError.value = ''
  try {
    const body: Record<string, unknown> = {
      project: props.project,
      group: props.group,
      doc_ref: props.docRef,
      action_scope: props.actionScope,
      mode: mode.value,
    }
    if (props.module != null) body.module = props.module
    if (mode.value === 'continuous') {
      body.continuation_target_seq = targetSeq.value
      body.continuation_review_mode = false
    }
    const res = await postRequest<any>('/api/v1/ai-invoke/start', body)
    const data = res.data
    runId.value = data.run_id
    providerName.value = data.provider?.name ?? ''
    attemptNo.value = data.attempt_no ?? 1
    docsTarget.value = data.docs_target ?? 1
    phase.value = 'running'
    elapsedMs.value = 0
    startTimers()
  } catch (e: any) {
    const status = e?.response?.status
    const data = e?.response?.data ?? {}
    if (status === 409 && data.code === 'run_in_progress' && data.run_id) {
      // Adopt the already-running run instead of erroring (scenario 8 restore).
      runId.value = data.run_id
      phase.value = 'running'
      startTimers()
      void pollStatus()
    } else if (status === 409 && data.code === 'no_enabled_provider') {
      startError.value = t('main.ai_invoke_dialog.error_no_provider')
    } else if (status === 422) {
      const msgs = (data.errors ?? []).map((er: any) => `${er.loc}: ${er.msg}`).join(' / ')
      startError.value = msgs || t('main.ai_invoke_dialog.error_start_failed')
    } else {
      startError.value = data.message ?? t('main.ai_invoke_dialog.error_start_failed')
    }
  } finally {
    starting.value = false
  }
}

async function pollStatus() {
  if (!runId.value || phase.value !== 'running') return
  try {
    const res = await getRequest<any>(`/api/v1/ai-invoke/${encodeURIComponent(runId.value)}`)
    const data = res.data
    if (data.status === 'finished') {
      applyFinished(data)
    } else {
      providerName.value = data.provider?.name ?? providerName.value
      attemptNo.value = data.attempt_no ?? attemptNo.value
      docsTarget.value = data.docs_target ?? docsTarget.value
      docsSoFar.value = data.docs_reached_so_far ?? docsSoFar.value
      if (typeof data.elapsed_ms === 'number') {
        elapsedMs.value = data.elapsed_ms
        startedAtMono = Date.now() - elapsedMs.value
      }
    }
  } catch (e: any) {
    if (e?.response?.status === 404) {
      // Server restarted — the in-memory run is gone (P0005 scenario 8 note).
      stopTimers()
      showToast(t('main.ai_invoke_dialog.error_run_lost'), 'warning')
      close()
    }
  }
}

function applyFinished(payload: any) {
  stopTimers()
  result.value = payload as FinishedPayload
  docsTarget.value = payload.docs_target ?? docsTarget.value
  phase.value = 'finished'
}

async function cancelRun() {
  if (!runId.value || cancelRequested.value) return
  cancelRequested.value = true
  try {
    await postRequest<any>(`/api/v1/ai-invoke/${encodeURIComponent(runId.value)}/cancel`, {})
  } catch {
    cancelRequested.value = false
    showToast(t('main.ai_invoke_dialog.error_cancel_failed'), 'danger')
  }
}

function onSseEvent(e: Event) {
  const detail = (e as CustomEvent).detail as { kind: string; payload: any } | undefined
  if (!detail?.payload || detail.payload.run_id !== runId.value) return
  if (detail.kind === 'switched') {
    providerName.value = detail.payload.to_provider_name ?? providerName.value
    attemptNo.value = detail.payload.attempt_no ?? attemptNo.value
  } else if (detail.kind === 'finished') {
    applyFinished(detail.payload)
  }
}

function onBackdrop() {
  // Never silently dismiss a live run; setup/finished close freely.
  if (phase.value !== 'running') close()
}

function close() {
  stopTimers()
  emit('update:visible', false)
}

watch(
  () => props.visible,
  (val) => {
    if (val) {
      resetState()
      window.addEventListener('fg:ai_invoke', onSseEvent)
    } else {
      window.removeEventListener('fg:ai_invoke', onSseEvent)
      stopTimers()
    }
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  window.removeEventListener('fg:ai_invoke', onSseEvent)
  stopTimers()
})
</script>

<style scoped>
.modal-aiv {
  width: 520px;
  max-width: 96vw;
  display: flex;
  flex-direction: column;
  max-height: 88vh;
}
.modal-aiv .modal-bd {
  flex: 1;
  overflow-y: auto;
  min-height: 0;
}
.aiv-body {
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.aiv-target-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: .82rem;
}
.aiv-target-label { color: var(--text-m); }
.aiv-target-id { font-family: 'JetBrains Mono', monospace; color: var(--text); }
.aiv-mode {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: var(--r);
  cursor: pointer;
}
.aiv-mode.active { border-color: var(--primary); background: var(--primary-l); }
.aiv-mode input { margin-top: 3px; flex-shrink: 0; }
.aiv-mode-text { display: flex; flex-direction: column; gap: 2px; }
.aiv-mode-title { font-size: .85rem; font-weight: 600; color: var(--text); }
.aiv-mode-desc { font-size: .76rem; color: var(--text-m); line-height: 1.4; }
.aiv-seq-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-left: 4px;
}
.aiv-seq-label { font-size: .8rem; color: var(--text); }
.aiv-seq-input { width: 90px; }
.aiv-seq-hint { font-size: .72rem; color: var(--text-m); }
.aiv-error {
  font-size: .8rem;
  color: var(--danger);
  background: var(--danger-light, #fee2e2);
  border-radius: var(--r-sm);
  padding: 8px 10px;
}
.aiv-running {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 24px 0;
}
.aiv-spinner { font-size: 1.6rem; color: var(--primary); }
.aiv-running-provider { font-size: .95rem; font-weight: 600; color: var(--text); }
.aiv-attempt-badge {
  font-size: .68rem;
  font-weight: 700;
  background: var(--primary-l);
  color: var(--primary);
  border-radius: 10px;
  padding: 1px 8px;
  margin-left: 6px;
}
.aiv-running-status { font-size: .8rem; color: var(--text-m); }
.aiv-elapsed { font-size: .85rem; font-family: 'JetBrains Mono', monospace; color: var(--text); }
.aiv-progress { font-size: .8rem; color: var(--text-m); }
.aiv-result-head {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 1rem;
  font-weight: 700;
  padding: 8px 0;
}
.aiv-outcome-complete { color: var(--success, #16a34a); }
.aiv-outcome-partial { color: #b45309; }
.aiv-outcome-none { color: var(--danger, #dc2626); }
.aiv-result-count {
  font-family: 'JetBrains Mono', monospace;
  font-size: .9rem;
}
.aiv-end-reason { font-size: .8rem; color: var(--text-m); }
.aiv-section { display: flex; flex-direction: column; gap: 6px; }
.aiv-section-title {
  font-size: .72rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .08em;
  color: var(--text-m);
}
.aiv-doc-list { display: flex; flex-direction: column; gap: 4px; }
.aiv-doc-link {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  background: none;
  cursor: pointer;
  font-family: 'JetBrains Mono', monospace;
  font-size: .8rem;
  color: var(--primary);
  text-align: left;
}
.aiv-doc-link:hover { background: var(--surface-h); }
.aiv-message {
  background: var(--bg-2, #f4f4f5);
  border-radius: var(--r-sm);
  padding: 10px 12px;
  font-size: .8rem;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0;
  max-height: 200px;
  overflow-y: auto;
}
.aiv-message--none {
  color: var(--text-m);
  font-style: italic;
}
.aiv-fallback-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  background: none;
  border: none;
  cursor: pointer;
  font-size: .78rem;
  color: var(--text-m);
  padding: 0;
}
.aiv-fallback-list {
  list-style: none;
  margin: 0;
  padding: 4px 0 0 14px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: .76rem;
}
.aiv-fallback-provider { font-weight: 600; margin-right: 6px; }
.aiv-fallback-reason { color: var(--danger); margin-right: 6px; }
.aiv-fallback-detail { color: var(--text-m); }
.aiv-hint { font-size: .78rem; color: var(--text-m); }
.aiv-dirty-banner {
  font-size: .78rem;
  line-height: 1.5;
  padding: 8px 10px;
  border-radius: var(--r-sm);
  background: #fef3c7;
  color: #92600a;
  border: 1px solid #fde68a;
}
.aiv-dirty-files { font-family: 'JetBrains Mono', monospace; display: block; margin-top: 2px; }
</style>
