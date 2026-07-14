<template>
  <div v-if="run" class="aiv-inline-layer">
    <section
      class="aiv-inline"
      :class="`aiv-inline--${run.phase}`"
      aria-live="polite"
      :aria-label="titleText"
    >
      <div class="aiv-inline__header">
        <div class="aiv-inline__heading">
          <AppIcon
            :name="run.phase === 'running' ? 'circle-notch' : run.phase === 'finished' && run.outcome === 'complete' ? 'check-circle' : 'warning'"
            :spin="run.phase === 'running'"
          />
          <div>
            <div class="aiv-inline__title">{{ titleText }}</div>
            <div class="aiv-inline__doc">{{ run.docRef }}</div>
          </div>
        </div>
        <div class="aiv-inline__actions">
          <button
            v-if="run.phase === 'running'"
            type="button"
            class="btn btn-danger btn-sm"
            :disabled="run.cancelling || cancelling"
            @click="cancel"
          >
            <AppIcon name="prohibit" />
            {{ t('main.ai_invoke_dialog.btn_cancel_run') }}
          </button>
          <button
            v-else
            type="button"
            class="btn btn-ghost btn-sm"
            @click="store.dismiss(groupId)"
          >
            <AppIcon name="x" />
            {{ t('common.close') }}
          </button>
        </div>
      </div>

      <template v-if="run.phase === 'running'">
        <div class="aiv-inline__meta">
          <span>{{ run.provider?.name || '—' }}</span>
          <span v-if="run.attemptNo > 1">
            {{ t('main.ai_invoke_dialog.attempt_no', { n: run.attemptNo }) }}
          </span>
          <span>{{ elapsedText }}</span>
          <span v-if="run.docsTarget > 1">
            {{ t('main.ai_invoke_dialog.docs_progress', {
              reached: run.docsReachedSoFar,
              target: run.docsTarget,
            }) }}
          </span>
        </div>
        <p v-if="run.cancelling" class="aiv-inline__notice">
          {{ t('main.ai_invoke_dialog.cancelling') }}
        </p>
      </template>

      <p v-else-if="run.phase === 'lost'" class="aiv-inline__notice aiv-inline__notice--warning">
        {{ t('main.ai_invoke_dialog.error_run_lost') }}
      </p>

      <div v-else class="aiv-inline__result">
        <div class="aiv-inline__meta">
          <span>{{ run.provider?.name || '—' }}</span>
          <span>{{ elapsedText }}</span>
          <span v-if="run.docsTarget > 1">
            {{ t('main.ai_invoke_dialog.docs_progress', {
              reached: run.docsReached,
              target: run.docsTarget,
            }) }}
          </span>
        </div>

        <p v-if="endReasonText" class="aiv-inline__notice">{{ endReasonText }}</p>

        <div v-if="missingDocReasons.length > 0" class="aiv-inline__section aiv-inline__diagnostics">
          <strong>{{ t('main.ai_invoke_dialog.failure_details') }}</strong>
          <ul class="aiv-inline__history">
            <li v-for="(reason, index) in missingDocReasons" :key="`${index}-${reason}`">
              {{ reason }}
            </li>
          </ul>
        </div>

        <div v-if="run.reachedDocIds.length > 0" class="aiv-inline__section">
          <strong>{{ t('main.ai_invoke_dialog.reached_docs') }}</strong>
          <div class="aiv-inline__chips">
            <code v-for="docId in run.reachedDocIds" :key="docId">{{ docId }}</code>
          </div>
        </div>

        <div class="aiv-inline__section">
          <strong>{{ t('main.ai_invoke_dialog.last_message') }}</strong>
          <p class="aiv-inline__message">
            {{ run.lastMessageReceived && run.lastMessage
              ? run.lastMessage
              : t('main.ai_invoke_dialog.last_message_none') }}
          </p>
        </div>

        <div v-if="run.providerSwitches.length > 0" class="aiv-inline__section">
          <strong>
            {{ t('main.ai_invoke_dialog.fallback_history', { count: run.providerSwitches.length }) }}
          </strong>
          <ul class="aiv-inline__history">
            <li v-for="(item, index) in run.providerSwitches" :key="`${index}-${item.attemptNo ?? ''}`">
              <span>{{ providerSwitchLabel(item) }}</span>
              <span v-if="item.reason"> · {{ fallbackReason(item.reason) }}</span>
              <span v-if="item.detail"> — {{ item.detail }}</span>
            </li>
          </ul>
        </div>

        <div v-if="run.sourceDirty" class="aiv-inline__notice aiv-inline__notice--warning">
          <AppIcon name="warning" />
          {{ t('main.ai_invoke_dialog.source_dirty', { count: run.sourceDirtyFiles.length }) }}
          <div v-if="run.sourceDirtyFiles.length > 0" class="aiv-inline__files">
            {{ run.sourceDirtyFiles.join(', ') }}
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import AppIcon from '@shared/AppIcon.vue'
import {
  useAiInvokeRunsStore,
  type AiInvokeProviderSwitch,
} from '../stores/aiInvokeRuns'
import { useToast } from './common/useToast'

const props = defineProps<{ groupId: string }>()
const { t } = useI18n()
const { showToast } = useToast()
const store = useAiInvokeRunsStore()
const cancelling = ref(false)

const groupId = computed(() => props.groupId)
const run = computed(() => store.runsByGroup[groupId.value] ?? null)

const titleText = computed(() => {
  if (!run.value) return ''
  if (run.value.phase === 'running') {
    return run.value.cancelling
      ? t('main.ai_invoke_dialog.cancelling')
      : t('main.ai_invoke_dialog.inline_running')
  }
  if (run.value.phase === 'lost') return t('main.ai_invoke_dialog.error_run_lost')
  if (run.value.outcome === 'complete') return t('main.ai_invoke_dialog.outcome_complete')
  if (run.value.outcome === 'partial') return t('main.ai_invoke_dialog.outcome_partial')
  return t('main.ai_invoke_dialog.outcome_none')
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
  if (current.oracleMismatch) reasons.push(t('main.ai_invoke_dialog.oracle_no_documents'))
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

function fallbackReason(reason: string): string {
  if (reason === 'spawn_failed') return t('main.ai_invoke_dialog.reason_spawn_failed')
  if (reason === 'fast_fail') return t('main.ai_invoke_dialog.reason_fast_fail')
  if (reason === 'api_error') return t('main.ai_invoke_dialog.reason_api_error')
  return reason
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
  () => run.value?.phase === 'running' ? run.value.runId : null,
  runId => {
    if (!runId) return
    void nextTick(() => {
      const container = document.querySelector<HTMLElement>('.content-wrap')
      if (!container) return
      if (typeof container.scrollTo === 'function') {
        container.scrollTo({ top: 0, behavior: 'auto' })
      } else {
        container.scrollTop = 0
      }
    })
  },
  { immediate: true },
)

watch(
  () => props.groupId,
  group => {
    if (group) void store.discover(group)
  },
  { immediate: true },
)
</script>

<style scoped>
.aiv-inline-layer {
  position: absolute;
  inset: 0;
  z-index: 80;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  min-height: 180px;
  padding: 24px;
  overflow: auto;
  border-radius: var(--r-lg);
  background: color-mix(in srgb, var(--bg) 88%, transparent);
  backdrop-filter: blur(2px);
}

.aiv-inline {
  width: min(760px, 100%);
  padding: 16px 18px;
  border: 1px solid color-mix(in srgb, var(--primary) 40%, var(--border));
  border-radius: var(--r-lg);
  background: var(--surface);
  box-shadow: var(--sh-md, 0 8px 24px rgba(15, 23, 42, .14));
}

.aiv-inline--lost {
  border-color: var(--warning);
}

.aiv-inline__header,
.aiv-inline__heading,
.aiv-inline__meta,
.aiv-inline__chips {
  display: flex;
  align-items: center;
}

.aiv-inline__header {
  justify-content: space-between;
  gap: 16px;
}

.aiv-inline__heading {
  min-width: 0;
  align-items: flex-start;
  gap: 10px;
}

.aiv-inline__heading > i {
  flex: 0 0 auto;
  margin-top: 2px;
  color: var(--primary);
  font-size: 1.1rem;
}

.aiv-inline--finished .aiv-inline__heading > i {
  color: var(--success);
}

.aiv-inline--lost .aiv-inline__heading > i {
  color: var(--warning);
}

.aiv-inline__title {
  color: var(--text);
  font-size: .9rem;
  font-weight: 700;
}

.aiv-inline__doc {
  overflow: hidden;
  margin-top: 2px;
  color: var(--text-m);
  font: 500 .72rem 'JetBrains Mono', monospace;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.aiv-inline__actions {
  flex: 0 0 auto;
}

.aiv-inline__meta {
  flex-wrap: wrap;
  gap: 5px 14px;
  margin-top: 12px;
  color: var(--text-m);
  font-size: .74rem;
}

.aiv-inline__result {
  display: grid;
  gap: 12px;
}

.aiv-inline__section {
  display: grid;
  gap: 6px;
  font-size: .78rem;
}

.aiv-inline__section strong {
  color: var(--text);
  font-size: .75rem;
}

.aiv-inline__chips {
  flex-wrap: wrap;
  gap: 6px;
}

.aiv-inline__chips code {
  padding: 2px 6px;
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  background: var(--bg);
  color: var(--text-s);
  font-size: .7rem;
}

.aiv-inline__message,
.aiv-inline__notice {
  margin: 0;
  color: var(--text-s);
  font-size: .78rem;
  line-height: 1.55;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.aiv-inline__notice {
  margin-top: 12px;
  padding: 8px 10px;
  border-radius: var(--r);
  background: var(--bg);
}

.aiv-inline__notice--warning {
  color: var(--warning);
  background: var(--warning-l);
}

.aiv-inline__diagnostics {
  padding: 9px 10px;
  border: 1px solid var(--warning);
  border-radius: var(--r);
  background: var(--warning-l);
}

.aiv-inline__history {
  display: grid;
  gap: 4px;
  color: var(--text-s);
  font-size: .74rem;
}

.aiv-inline__files {
  margin-top: 4px;
  font-family: 'JetBrains Mono', monospace;
  font-size: .68rem;
  overflow-wrap: anywhere;
}

@media (max-width: 680px) {
  .aiv-inline-layer {
    padding: 10px;
  }

  .aiv-inline__header {
    align-items: flex-start;
  }

  .aiv-inline__actions .btn {
    padding-inline: 7px;
  }
}
</style>