<template>
  <section
    v-if="run && run.docRef !== suppressDocRef"
    class="ai-invoke-status-card"
    :class="`ai-invoke-status-card--${run.phase}`"
    aria-live="polite"
    :aria-label="titleText"
  >
    <div
      v-if="run.phase === 'running'"
      class="ai-invoke-status-spinner"
      aria-hidden="true"
    ></div>
    <AppIcon
      v-else
      class="ai-invoke-status-icon"
      :name="run.phase === 'finished' && run.outcome === 'complete' ? 'check-circle' : 'warning'"
    />
    <span
      class="ai-invoke-status-text"
      :data-test="run.phase === 'paused' || run.phase === 'pause_requested' ? 'ai-inline-pause-state' : undefined"
    >
      {{ titleText }}
    </span>
    <div v-if="run.documentReviewLoop" class="ai-review-loop-card" data-test="review-loop-card">
      <strong>{{ t('main.ai_invoke_dialog.review_loop_round', { n: run.documentReviewLoop.roundNo }) }} · {{ reviewLoopStageLabel(run.documentReviewLoop.currentStage) }}</strong>
      <ol><li v-for="(item,index) in run.documentReviewLoop.history" :key="index">{{ t('main.ai_invoke_dialog.review_loop_round', { n: item.round_no }) }} · {{ reviewLoopHistoryStageLabel(item.stage) }} · {{ reviewLoopHistoryResultLabel(item.result) }}</li></ol>
      <p v-if="run.documentReviewLoop.currentStage === 'stopped'">{{ reviewLoopStopLabel(run.documentReviewLoop.stopReason) }}<span v-if="run.documentReviewLoop.stopDetail"> — {{ run.documentReviewLoop.stopDetail }}</span></p>
    </div>
    <div class="ai-invoke-status-actions">
      <button
        v-if="run.phase === 'running'"
        type="button"
        class="btn btn-outline btn-sm"
        :disabled="run.cancelling || cancelling"
        @click="cancel"
      >
        <AppIcon name="stop-circle" />
        {{ t('common.cancel') }}
      </button>
      <button
        v-else
        type="button"
        class="btn btn-outline btn-sm"
        data-test="ai-inline-close"
        @click="closeSurface"
      >
        <AppIcon name="x" />
        {{ t('common.close') }}
      </button>
    </div>

    <div v-if="run.phase === 'running'" class="ai-invoke-status-meta" data-test="ai-inline-running-meta">
      <span>{{ run.provider?.name || '—' }}</span>
      <span>{{ elapsedText }}</span>
    </div>

    <div v-if="run.phase === 'finished'" class="ai-invoke-status-details">
      <div class="ai-invoke-status-meta">
        <span>{{ run.provider?.name || '—' }}</span>
        <span>{{ elapsedText }}</span>
        <span v-if="run.docsTarget > 1">
          {{ t('main.ai_invoke_dialog.docs_progress', {
            reached: run.docsReached,
            target: run.docsTarget,
          }) }}
        </span>
      </div>
      <p v-if="endReasonText" class="ai-invoke-status-notice">{{ endReasonText }}</p>
      <div v-if="missingDocReasons.length > 0" class="ai-invoke-status-section ai-invoke-status-diagnostics">
        <strong>{{ t(scoped ? 'main.ai_invoke_dialog.failure_details_scoped' : 'main.ai_invoke_dialog.failure_details') }}</strong>
        <ul class="ai-invoke-status-history">
          <li v-for="(reason, index) in missingDocReasons" :key="`${index}-${reason}`">{{ reason }}</li>
        </ul>
      </div>
      <div v-if="run.reachedDocIds.length > 0" class="ai-invoke-status-section">
        <strong>{{ t('main.ai_invoke_dialog.reached_docs') }}</strong>
        <div class="ai-invoke-status-chips">
          <code v-for="docId in run.reachedDocIds" :key="docId">{{ docId }}</code>
        </div>
      </div>
      <div class="ai-invoke-status-section">
        <strong>{{ t('main.ai_invoke_dialog.last_message') }}</strong>
        <p class="ai-invoke-status-message">
          {{ run.lastMessageReceived && run.lastMessage
            ? run.lastMessage
            : t('main.ai_invoke_dialog.last_message_none') }}
        </p>
      </div>
      <div v-if="run.providerSwitches.length > 0" class="ai-invoke-status-section">
        <strong>{{ t('main.ai_invoke_dialog.fallback_history', { count: run.providerSwitches.length }) }}</strong>
        <ul class="ai-invoke-status-history">
          <li v-for="(item, index) in run.providerSwitches" :key="`${index}-${item.attemptNo ?? ''}`">
            <span>{{ providerSwitchLabel(item) }}</span>
            <span v-if="item.reason"> · {{ fallbackReason(item.reason) }}</span>
            <span v-if="item.detail"> — {{ item.detail }}</span>
          </li>
        </ul>
      </div>
      <div v-if="run.sourceDirty" class="ai-invoke-status-notice ai-invoke-status-notice--warning">
        <AppIcon name="warning" />
        {{ t('main.ai_invoke_dialog.source_dirty', { count: run.sourceDirtyFiles.length }) }}
        <div v-if="run.sourceDirtyFiles.length > 0" class="ai-invoke-status-files">
          {{ run.sourceDirtyFiles.join(', ') }}
        </div>
      </div>
    </div>
  </section>
</template>
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import AppIcon from '@shared/AppIcon.vue'
import {
  isFinishedCard,
  useAiInvokeRunsStore,
  type AiInvokeProviderSwitch,
} from '../stores/aiInvokeRuns'
import { useToast } from './common/useToast'

const props = defineProps<{ groupId: string; suppressDocRef?: string }>()
const { t } = useI18n()
const { showToast } = useToast()
const store = useAiInvokeRunsStore()
const cancelling = ref(false)

const groupId = computed(() => props.groupId)
// The header monitor keeps finished cards for 30 minutes (0290 NR0003 §5.1), but this
// banner sits on top of the document itself — a result panel parked there for half an
// hour is in the way. The registry stays the single source of truth; only this surface's
// view of a finished run expires early (NR0003 §5.3). Removing it here is not a dismiss:
// the card is still in the header monitor until read or swept.
const storeRun = computed(() => store.runsByGroup[groupId.value] ?? null)
const hiddenEntryKey = ref('')
function entryKey(entry: NonNullable<typeof storeRun.value>): string {
  return entry.runId || `paused:${entry.pausedAt ?? entry.docRef}`
}
const run = computed(() => {
  const entry = storeRun.value
  if (!entry || hiddenEntryKey.value === entryKey(entry)) return null
  if (!isFinishedCard(entry)) return entry
  // 0452 L0003 §2-6: min(60s, the retention in force). "Never expires" still closes this
  // banner after a minute — the user asked the header list to keep results, not the
  // document to stay covered — and at 0 there is no card to show in the first place.
  return store.now - (entry.finishedAtMs as number) < store.inlineResultWindowMs ? entry : null
})

// docsTarget 0 = the server judged this run by its scope (an edit's revision, a review's
// row), not by documents — it never had a document to register, so the document-flavoured
// failure wording would be a lie. 0259 B0001.
const scoped = computed(() => run.value?.docsTarget === 0)

const titleText = computed(() => {
  if (!run.value) return ''
  if (run.value.phase === 'running') {
    return run.value.cancelling
      ? t('main.ai_invoke_dialog.cancelling')
      : t('main.ai_invoke_dialog.inline_running')
  }
  if (run.value.phase === 'pause_requested') return t('main.ai_miniplayer.pause_scheduled')
  if (run.value.phase === 'paused') return t('main.ai_miniplayer.state_paused')
  if (run.value.phase === 'lost') return t('main.ai_invoke_dialog.error_run_lost')
  if (run.value.phase === 'finished') {
    if (run.value.outcome === 'complete') return t('main.ai_invoke_dialog.outcome_complete')
    if (run.value.outcome === 'partial') return t('main.ai_invoke_dialog.outcome_partial')
    // outcome_none* is terminal-only: null/non-terminal phases never reach this branch.
    return t(scoped.value ? 'main.ai_invoke_dialog.outcome_none_scoped' : 'main.ai_invoke_dialog.outcome_none')
  }
  return ''
})

const elapsedText = computed(() => {
  const total = Math.floor(store.elapsedMsFor(groupId.value) / 1000)
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`
})

const missingDocReasons = computed(() => {
  const current = run.value
  if (!current || current.phase !== 'finished' || current.docsReached > 0) return []
  const reasons = current.registerErrors.map(error => t('main.ai_invoke_dialog.register_error', {
    status: error.status,
    reason: error.reason,
  }))
  if (current.toolCallMisses > 0) {
    reasons.push(t('main.ai_invoke_dialog.tool_not_called', { count: current.toolCallMisses }))
  }
  if (current.turnLimitExhausted) reasons.push(t('main.ai_invoke_dialog.turn_limit_exhausted'))
  if (current.oracleMismatch) {
    reasons.push(t(scoped.value
      ? 'main.ai_invoke_dialog.oracle_no_result'
      : 'main.ai_invoke_dialog.oracle_no_documents'))
  }
  return reasons
})

const endReasonText = computed(() => {
  const reason = run.value?.endReason
  if (reason === 'cancelled') return t('main.ai_invoke_dialog.end_cancelled')
  if (reason === 'timeout') return t('main.ai_invoke_dialog.end_timeout')
  if (reason === 'all_providers_failed' || reason === 'all_failed') {
    return t('main.ai_invoke_dialog.end_all_failed')
  }
  return reason ?? ''
})

function providerSwitchLabel(item: AiInvokeProviderSwitch): string {
  const from = item.fromProviderName ?? item.providerName ?? item.fromProviderId ?? item.providerId ?? '—'
  const to = item.toProviderName ?? item.toProviderId
  return to ? `${from} → ${to}` : from
}

function reviewLoopStageLabel(stage: string): string {
  if (stage === 'review') return t('main.ai_invoke_dialog.review_loop_stage_review')
  if (stage === 'rework') return t('main.ai_invoke_dialog.review_loop_stage_rework')
  return t('main.ai_invoke_dialog.review_loop_stage_stopped')
}

function reviewLoopHistoryStageLabel(stage: unknown): string {
  if (stage === 'review') return t('main.ai_invoke_dialog.review_loop_history_stage_review')
  if (stage === 'rework') return t('main.ai_invoke_dialog.review_loop_history_stage_rework')
  return t('main.ai_invoke_dialog.review_loop_history_stage_unknown')
}

function reviewLoopHistoryResultLabel(result: unknown): string {
  if (result === 'issues') return t('main.ai_invoke_dialog.review_loop_history_result_issues')
  if (result === 'passed') return t('main.ai_invoke_dialog.review_loop_history_result_passed')
  if (result === 'complete') return t('main.ai_invoke_dialog.review_loop_history_result_complete')
  return t('main.ai_invoke_dialog.review_loop_history_result_unknown')
}

function reviewLoopStopLabel(reason: string | null): string {
  if (reason === 'review_passed') return t('main.ai_invoke_dialog.review_loop_stop_review_passed')
  if (reason === 'review_count_exhausted') return t('main.ai_invoke_dialog.review_loop_stop_review_count_exhausted')
  if (reason === 'retry_exhausted') return t('main.ai_invoke_dialog.review_loop_stop_retry_exhausted')
  return t('main.ai_invoke_dialog.review_loop_stop_total_timeout')
}

function fallbackReason(reason: string): string {
  if (reason === 'spawn_failed') return t('main.ai_invoke_dialog.reason_spawn_failed')
  if (reason === 'fast_fail') return t('main.ai_invoke_dialog.reason_fast_fail')
  if (reason === 'api_error') return t('main.ai_invoke_dialog.reason_api_error')
  // 0359: the switch that happens AFTER a provider ran cleanly and produced nothing.
  if (reason === 'no_output') return t('main.ai_invoke_dialog.reason_no_output')
  return reason
}

function closeSurface(): void {
  const current = run.value
  if (!current) return
  // Surface visibility is local. Pausing remains durable in the registry/server and can
  // still be resumed from the miniplayer after this document overlay is closed.
  hiddenEntryKey.value = entryKey(current)
  if (current.phase === 'finished' || current.phase === 'lost') {
    store.dismiss(groupId.value)
  }
}

async function cancel(): Promise<void> {
  cancelling.value = true
  try {
    await store.cancel(groupId.value)
  } catch {
    showToast(t('main.ai_invoke_dialog.error_cancel_failed'), 'danger')
  } finally {
    cancelling.value = false
  }
}


watch(
  () => props.groupId,
  group => {
    if (group) void store.discover(group)
  },
  { immediate: true },
)
</script>

<style scoped>
.ai-invoke-status-card {
  position: sticky;
  top: 0;
  z-index: 30;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 14px;
  padding: 10px 14px;
  border: 1px solid #93c5fd;
  border-radius: var(--r);
  background: #eff6ff;
  box-shadow: var(--sh-sm);
}

.ai-invoke-status-spinner {
  width: 16px;
  height: 16px;
  flex: 0 0 auto;
  border: 2px solid #bfdbfe;
  border-top-color: var(--primary);
  border-radius: 50%;
  animation: ai-invoke-spin .9s linear infinite;
}

@keyframes ai-invoke-spin {
  to { transform: rotate(360deg); }
}

.ai-invoke-status-icon {
  flex: 0 0 auto;
  color: var(--primary);
  font-size: 1rem;
}

.ai-invoke-status-card--finished .ai-invoke-status-icon {
  color: var(--success);
}

.ai-invoke-status-card--lost .ai-invoke-status-icon {
  color: var(--warning);
}

.ai-invoke-status-text {
  flex: 1;
  min-width: 0;
  color: #1e40af;
  font-size: .8rem;
  font-weight: 600;
}

.ai-invoke-status-actions {
  flex: 0 0 auto;
}

.ai-invoke-status-details {
  display: grid;
  flex: 0 0 100%;
  gap: 10px;
  padding-top: 4px;
  border-top: 1px solid #bfdbfe;
}

.ai-invoke-status-meta,
.ai-invoke-status-chips {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 5px 14px;
  color: var(--text-m);
  font-size: .74rem;
}

.ai-invoke-status-card > .ai-invoke-status-meta {
  flex: 0 0 100%;
  padding-left: 26px;
}

.ai-invoke-status-section {
  display: grid;
  gap: 6px;
  font-size: .78rem;
}

.ai-invoke-status-section strong {
  color: var(--text);
  font-size: .75rem;
}

.ai-invoke-status-chips { gap: 6px; }

.ai-invoke-status-chips code {
  padding: 2px 6px;
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  background: var(--surface);
  color: var(--text-s);
  font-size: .7rem;
}

.ai-invoke-status-message,
.ai-invoke-status-notice {
  margin: 0;
  color: var(--text-s);
  font-size: .78rem;
  line-height: 1.55;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.ai-invoke-status-notice {
  padding: 8px 10px;
  border-radius: var(--r);
  background: var(--surface);
}

.ai-invoke-status-notice--warning,
.ai-invoke-status-diagnostics {
  color: var(--warning);
  background: var(--warning-l);
}

.ai-invoke-status-diagnostics {
  padding: 9px 10px;
  border: 1px solid var(--warning);
  border-radius: var(--r);
}

.ai-invoke-status-history {
  display: grid;
  gap: 4px;
  color: var(--text-s);
  font-size: .74rem;
}

.ai-invoke-status-files {
  margin-top: 4px;
  font-family: 'JetBrains Mono', monospace;
  font-size: .68rem;
  overflow-wrap: anywhere;
}

@media (max-width: 680px) {
  .ai-invoke-status-card { padding: 10px; }
  .ai-invoke-status-actions .btn { padding-inline: 7px; }
}
.ai-review-loop-card{flex:0 0 100%;padding:10px;border:1px solid #bfdbfe;border-radius:var(--r);background:var(--surface)}.ai-review-loop-card ol{margin:6px 0;padding-left:20px}.ai-review-loop-card p{margin:6px 0 0}
</style>