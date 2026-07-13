<template>
  <!-- 0166: manual test-run entry point (NR0003). Before this strip, the only caller of
       POST /documents/test-run in the client was the failure strip, which renders only
       after a run has already failed — so an approved TS had no first-run entry point at
       all. This strip is that entry point. It self-hides unless the viewed doc is a TS
       whose state would pass the backend admission gate (validate_and_create_run):
       approved, or pending_review/revised with a prior run bound (the 0163/0169 re-run relaxation).
       The failed state stays owned by TestFailStrip to avoid a duplicate re-run button. -->
  <div v-if="visible" class="run-strip" :class="{ 'run-strip--running': isRunning }">
    <AppIcon
      class="run-strip-ic"
      :name="isRunning ? 'spinner' : 'flask'"
      :spin="isRunning"
      aria-hidden="true"
    />
    <span class="run-strip-label">{{ label }}</span>
    <span v-if="subText" class="run-strip-sub">{{ subText }}</span>
    <span v-if="!isRunning" class="run-strip-actions">
      <button
        type="button"
        class="run-strip-btn"
        :disabled="busy"
        :title="t('main.test_run_strip.delegate_hint')"
        @click="onDelegate"
      >
        <AppIcon
          :name="delegating ? 'spinner' : 'robot'"
          :spin="delegating"
          aria-hidden="true"
        />
        {{ t('main.test_run_strip.delegate') }}
      </button>
      <button
        type="button"
        class="run-strip-btn run-strip-btn--run"
        :disabled="busy"
        @click="onRun"
      >
        <AppIcon
          :name="launching ? 'spinner' : 'play'"
          :spin="launching"
          aria-hidden="true"
        />
        {{ hasRunHistory ? t('main.test_run_strip.rerun') : t('main.test_run_strip.run') }}
      </button>
    </span>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { postRequest } from '@shared/api'
import { copyToClipboardDeferred, ClipboardAbort } from '../utils/clipboard'
import { openClipboardFallback } from '../composables/useClipboardFallback'
import { useToast } from './common/useToast'
import AppIcon from '@shared/AppIcon.vue'
import type { TestRun } from '../types/testRun'

const props = defineProps<{
  typeCode: string | null
  reviewStatus: string | null
  testRun: TestRun | null
  groupDisposed: boolean
  docLoaded: boolean
  docId: string
}>()

const emit = defineEmits<{
  (e: 'run-started'): void
}>()

const { t } = useI18n()
const { showToast } = useToast()

const launching = ref(false)
const delegating = ref(false)
const busy = computed(() => launching.value || delegating.value)

const isRunning = computed(() => props.testRun?.status === 'running')
const hasRunHistory = computed(() => props.testRun != null)

// Mirror of the backend admission gate (test_run_service.validate_and_create_run):
// TS + approved, or TS + pending_review/revised with a run already bound (0163/0169). A running run
// keeps the strip visible as launch feedback regardless of review status; a failed run
// hides it because TestFailStrip already renders the re-run affordance for that state.
const visible = computed(() => {
  if (!props.docLoaded || props.groupDisposed) return false
  if ((props.typeCode ?? '') !== 'TS') return false
  const status = props.testRun?.status ?? null
  if (status === 'failed') return false
  if (status === 'running') return true
  if (props.reviewStatus === 'approved') return true
  return (props.reviewStatus === 'pending_review' || props.reviewStatus === 'revised') && props.testRun != null
})

const label = computed(() => {
  if (isRunning.value) return t('main.test_run_strip.running')
  if (props.testRun?.status === 'passed') {
    return t('main.test_run_strip.last_passed', {
      passed: props.testRun?.case_passed ?? 0,
      total: props.testRun?.case_total ?? 0,
    })
  }
  return t('main.test_run_strip.ready')
})

const subText = computed(() => props.testRun?.run_id ?? '')

function runErrorMessage(e: unknown): string {
  const code = (e as { response?: { data?: { error?: string } } })?.response?.data?.error
  switch (code) {
    case 'permission_denied':
      return t('main.test_run_strip.err_denied')
    case 'run_in_progress':
      return t('main.test_run_strip.err_in_progress')
    case 'doc_not_approved':
      return t('main.test_run_strip.err_not_approved')
    case 'group_disposed':
      return t('main.test_run_strip.err_disposed')
    case 'src_root_missing':
      return t('main.test_run_strip.err_src_missing')
    case 'no_test_cases':
      return t('main.test_run_strip.err_no_cases')
    default:
      return t('main.test_run_strip.err_failed')
  }
}

async function onRun() {
  if (busy.value || !props.docId) return
  launching.value = true
  try {
    await postRequest('/api/v1/documents/test-run', { doc_id: props.docId })
    showToast(t('main.test_run_strip.run_started'), 'info')
    emit('run-started')
  } catch (e: unknown) {
    showToast(runErrorMessage(e), 'error')
  } finally {
    launching.value = false
  }
}

// Manned delegation (the second, previously dead entrance — NR0003 §설계 의도):
// POST /documents/test-run-request issues a test_run-scoped token wrapped in an
// execution mention; the user pastes it to an AI worker. The mention is produced
// inside copyToClipboardDeferred so the click's activation survives the round-trip
// (group 0133). An API failure toasts its own message and aborts the copy quietly.
async function onDelegate() {
  if (busy.value || !props.docId) return
  delegating.value = true
  let apiErrorShown = false
  try {
    const ok = await copyToClipboardDeferred(async () => {
      try {
        const res = await postRequest<{ mention?: string }>(
          '/api/v1/documents/test-run-request',
          { doc_id: props.docId },
        )
        const mention = res.data?.mention
        if (!mention) throw new ClipboardAbort()
        return mention
      } catch (e: unknown) {
        if (e instanceof ClipboardAbort) throw e
        apiErrorShown = true
        showToast(runErrorMessage(e), 'error')
        throw new ClipboardAbort()
      }
    })
    if (ok) showToast(t('main.test_run_strip.delegate_copied'), 'info')
    // B0001 / group 0221: when the mention was produced but the write failed, offer the
    // manual-copy fallback modal instead of only toasting.
    else if (!apiErrorShown && !openClipboardFallback()) {
      showToast(t('main.test_run_strip.delegate_copy_failed'), 'error')
    }
  } finally {
    delegating.value = false
  }
}
</script>

<style scoped>
.run-strip {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 36px;
  padding: 6px 12px;
  border: 1px solid var(--primary, #2563eb);
  border-radius: var(--r, 6px);
  background: var(--primary-l, #eff6ff);
  color: var(--primary, #2563eb);
  margin-bottom: 12px;
}
.run-strip-ic {
  flex-shrink: 0;
  font-size: 0.9rem;
}
.run-strip-label {
  font-weight: 600;
  font-size: 0.82rem;
  white-space: nowrap;
}
.run-strip-sub {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.72rem;
  color: var(--text-s, #475569);
  opacity: 0.85;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}
.run-strip-actions {
  display: inline-flex;
  gap: 6px;
  margin-left: auto;
  flex-shrink: 0;
}
.run-strip-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 9px;
  font-size: 0.74rem;
  font-weight: 500;
  border: 1px solid var(--primary, #2563eb);
  border-radius: var(--r-sm, 4px);
  background: var(--surface, #fff);
  color: var(--primary, #2563eb);
  cursor: pointer;
  transition: background var(--tr, 150ms ease);
}
.run-strip-btn:hover:not(:disabled) {
  background: var(--primary-l, #eff6ff);
}
.run-strip-btn:disabled {
  opacity: 0.6;
  cursor: default;
}
.run-strip-btn--run {
  background: var(--primary, #2563eb);
  color: #fff;
}
.run-strip-btn--run:hover:not(:disabled) {
  background: var(--primary, #2563eb);
  filter: brightness(0.92);
}
.run-strip--running .run-strip-ic {
  color: var(--primary, #2563eb);
}
</style>
