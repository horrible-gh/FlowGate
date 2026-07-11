<template>
  <!-- Slim inline failure strip (0155 confirmed design B). Renders at the very top of
       .doc-main, above DocHeader, only when the viewed doc's latest test run failed.
       A failed run never assembles a TSR (test_run_service.execute_run), so this strip is
       the sole in-context signal that a run failed — directly answering R0001's concern
       that a plain notification gets ignored. Collapsed by default (~36px); expands to the
       failing-case list with each case's output tail. -->
  <div
    v-if="visible"
    class="fail-strip"
    :class="{
      'fail-strip--open': expanded && !optimisticRunning,
      'fail-strip--fresh': justFinished && !optimisticRunning,
      'fail-strip--running': optimisticRunning,
    }"
  >
    <button
      type="button"
      class="fail-strip-bar"
      :aria-expanded="expanded"
      :aria-label="t('main.test_fail_strip.toggle_aria')"
      @click="toggleExpanded"
    >
      <AppIcon
        class="fail-strip-ic"
        :name="optimisticRunning ? 'spinner' : 'warning'"
        :spin="optimisticRunning"
        aria-hidden="true"
      />
      <span class="fail-strip-label">
        {{ optimisticRunning ? t('main.test_fail_strip.optimistic_running') : t('main.test_fail_strip.summary', { failed: failedCount, total: totalCount }) }}
      </span>
      <span v-if="!optimisticRunning && finishedText" class="fail-strip-fresh-badge">{{ finishedText }}</span>
      <span v-if="!optimisticRunning && subText" class="fail-strip-sub">{{ subText }}</span>
      <span v-if="!optimisticRunning" class="fail-strip-actions" @click.stop>
        <button type="button" class="fail-strip-btn" @click="toggleExpanded">
          <AppIcon name="file-text" aria-hidden="true" />
          {{ t('main.test_fail_strip.log') }}
        </button>
        <button
          type="button"
          class="fail-strip-btn fail-strip-btn--rerun"
          :disabled="rerunning"
          @click="onRerun"
        >
          <AppIcon
            :name="rerunning ? 'spinner' : 'arrow-clockwise'"
            :spin="rerunning"
            aria-hidden="true"
          />
          {{ t('main.test_fail_strip.rerun') }}
        </button>
      </span>
      <AppIcon
        v-if="!optimisticRunning"
        class="fail-strip-caret"
        :name="expanded ? 'caret-up' : 'caret-down'"
        aria-hidden="true"
      />
    </button>

    <div v-if="expanded && !optimisticRunning" class="fail-strip-detail">
      <div v-for="(c, idx) in failedCases" :key="idx" class="fail-case">
        <div class="fail-case-hd">
          <span class="fail-case-name">
            {{ c.case_title || c.case_no || t('main.test_fail_strip.unnamed_case') }}
          </span>
          <span class="fail-case-meta">
            <span class="fail-case-result">{{ c.result }}</span>
            <span v-if="c.exit_code != null" class="fail-case-exit">exit {{ c.exit_code }}</span>
          </span>
        </div>
        <div v-if="c.expect" class="fail-case-msg">{{ c.expect }}</div>
        <pre v-if="c.output_tail" class="fail-case-log">{{ c.output_tail }}</pre>
      </div>
      <div v-if="failedCases.length === 0" class="fail-case-empty">
        {{ t('main.test_fail_strip.no_case_detail') }}
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import AppIcon from '@shared/AppIcon.vue'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { postRequest } from '@shared/api'
import { useToast } from './common/useToast'
import type { TestRun, TestRunCase } from '../types/testRun'

const props = defineProps<{
  testRun: TestRun | null
  docId: string
}>()

const emit = defineEmits<{
  (e: 'run-started'): void
}>()

const { t, locale } = useI18n()
const { showToast } = useToast()

const expanded = ref(false)
const rerunning = ref(false)
const optimisticRunning = ref(false)
const optimisticSourceRunId = ref<TestRun['run_id'] | null>(null)
let optimisticRunningTimer: ReturnType<typeof setTimeout> | null = null
const OPTIMISTIC_RUNNING_MS = 1500

// Only a failed run surfaces the strip. Any other status (passed/running/absent) → null render,
// so the gate is automatic on every non-failing doc (embed is null unless a run is bound).
const visible = computed(() => optimisticRunning.value || props.testRun?.status === 'failed')

const failedCases = computed<TestRunCase[]>(() =>
  (props.testRun?.cases ?? []).filter((c) => c.result === 'fail' || c.result === 'timeout'),
)

const failedCount = computed(() => props.testRun?.case_failed ?? failedCases.value.length)
const totalCount = computed(
  () => props.testRun?.case_total ?? props.testRun?.cases?.length ?? failedCount.value,
)

const finishedAt = computed(() =>
  props.testRun?.finished_at ?? props.testRun?.started_at ?? props.testRun?.created_at ?? null,
)

function stopOptimisticRunning() {
  if (optimisticRunningTimer !== null) {
    clearTimeout(optimisticRunningTimer)
    optimisticRunningTimer = null
  }
  optimisticRunning.value = false
  optimisticSourceRunId.value = null
}

function startOptimisticRunning() {
  if (optimisticRunningTimer !== null) clearTimeout(optimisticRunningTimer)
  optimisticSourceRunId.value = props.testRun?.run_id ?? null
  optimisticRunning.value = true
  optimisticRunningTimer = setTimeout(() => {
    optimisticRunning.value = false
    optimisticSourceRunId.value = null
    optimisticRunningTimer = null
  }, OPTIMISTIC_RUNNING_MS)
}

function toggleExpanded() {
  if (optimisticRunning.value) return
  expanded.value = !expanded.value
}

watch(
  () => [props.testRun?.status ?? null, props.testRun?.run_id ?? null] as const,
  ([status, runId]) => {
    if (!optimisticRunning.value) return
    if (status === 'passed') {
      stopOptimisticRunning()
      return
    }
    if (status === 'running' && runId !== optimisticSourceRunId.value) {
      // The server-side running embed has arrived; keep the short local indicator
      // visible for its minimum window, then let TestRunStrip own the live state.
      return
    }
  },
)

onBeforeUnmount(stopOptimisticRunning)
function formatRunTime(date: Date): string {
  return new Intl.DateTimeFormat(locale.value, {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

const finishedText = computed(() => {
  if (!finishedAt.value) return ''
  const date = new Date(finishedAt.value)
  if (Number.isNaN(date.getTime())) return ''
  return t('main.test_fail_strip.finished_at', { time: formatRunTime(date) })
})

const justFinished = computed(() => {
  if (!finishedAt.value) return false
  const date = new Date(finishedAt.value)
  if (Number.isNaN(date.getTime())) return false
  return Date.now() - date.getTime() < 60_000
})

// A failed run has no tsr_doc_id (TSR is assembled on pass only). The identity of the failure
// is therefore its run_id + the first failing case's exit code — shown as the sub text.
const subText = computed(() => {
  const parts: string[] = []
  const first = failedCases.value[0]
  if (first && first.exit_code != null) parts.push(`exit ${first.exit_code}`)
  if (props.testRun?.run_id) parts.push(t('main.test_fail_strip.run_id', { id: props.testRun.run_id }))
  return parts.join(' · ')
})

async function onRerun() {
  if (rerunning.value || !props.docId) return
  rerunning.value = true
  try {
    await postRequest('/api/v1/documents/test-run', { doc_id: props.docId })
    startOptimisticRunning()
    showToast(t('main.test_fail_strip.rerun_started'), 'info')
    emit('run-started')
  } catch (e: unknown) {
    const code = (e as { response?: { data?: { error?: string } } })?.response?.data?.error
    const msg =
      code === 'permission_denied'
        ? t('main.test_fail_strip.rerun_denied')
        : code === 'run_in_progress'
          ? t('main.test_fail_strip.rerun_in_progress')
          : code === 'doc_not_approved'
            ? t('main.test_fail_strip.rerun_not_approved')
            : code === 'group_disposed'
              ? t('main.test_fail_strip.rerun_disposed')
              : t('main.test_fail_strip.rerun_failed')
    showToast(msg, 'error')
  } finally {
    rerunning.value = false
  }
}
</script>

<style scoped>
.fail-strip {
  border: 1px solid var(--danger, #dc2626);
  border-radius: var(--r, 6px);
  background: var(--danger-l, #fee2e2);
  margin-bottom: 12px;
  overflow: hidden;
}
.fail-strip-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  min-height: 36px;
  padding: 6px 12px;
  background: transparent;
  border: 0;
  cursor: pointer;
  text-align: left;
  font: inherit;
  color: var(--danger, #dc2626);
}
.fail-strip-ic {
  flex-shrink: 0;
  font-size: 0.9rem;
}
.fail-strip-label {
  font-weight: 600;
  font-size: 0.82rem;
  white-space: nowrap;
}
.fail-strip-fresh-badge {
  flex-shrink: 0;
  padding: 2px 6px;
  border: 1px solid rgba(220, 38, 38, 0.35);
  border-radius: var(--r-sm, 4px);
  background: rgba(255, 255, 255, 0.7);
  color: var(--danger, #dc2626);
  font-size: 0.68rem;
  font-weight: 600;
  white-space: nowrap;
}
.fail-strip--running {
  border-color: var(--primary, #2563eb);
  background: var(--primary-l, #dbeafe);
}
.fail-strip--running .fail-strip-bar {
  color: var(--primary, #2563eb);
  cursor: default;
}
.fail-strip--fresh .fail-strip-fresh-badge {
  background: var(--surface, #fff);
  box-shadow: 0 0 0 2px rgba(220, 38, 38, 0.14);
}
.fail-strip-sub {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.72rem;
  color: var(--text-s, #475569);
  opacity: 0.85;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}
.fail-strip-actions {
  display: inline-flex;
  gap: 6px;
  margin-left: auto;
  flex-shrink: 0;
}
.fail-strip-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 9px;
  font-size: 0.74rem;
  font-weight: 500;
  border: 1px solid var(--danger, #dc2626);
  border-radius: var(--r-sm, 4px);
  background: var(--surface, #fff);
  color: var(--danger, #dc2626);
  cursor: pointer;
  transition: background var(--tr, 150ms ease);
}
.fail-strip-btn:hover:not(:disabled) {
  background: var(--danger-l, #fee2e2);
}
.fail-strip-btn:disabled {
  opacity: 0.6;
  cursor: default;
}
.fail-strip-btn--rerun {
  background: var(--danger, #dc2626);
  color: #fff;
}
.fail-strip-btn--rerun:hover:not(:disabled) {
  background: var(--danger, #dc2626);
  filter: brightness(0.92);
}
.fail-strip-caret {
  flex-shrink: 0;
  font-size: 0.72rem;
}
.fail-strip-detail {
  border-top: 1px solid var(--danger, #dc2626);
  background: var(--surface, #fff);
  padding: 8px 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 320px;
  overflow-y: auto;
}
.fail-case {
  border-left: 3px solid var(--danger, #dc2626);
  padding: 4px 0 4px 10px;
}
.fail-case-hd {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
}
.fail-case-name {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.78rem;
  font-weight: 500;
  color: var(--text, #0f172a);
  word-break: break-all;
}
.fail-case-meta {
  display: inline-flex;
  gap: 6px;
  align-items: center;
  margin-left: auto;
  flex-shrink: 0;
}
.fail-case-result {
  font-size: 0.66rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  font-weight: 600;
  color: var(--danger, #dc2626);
}
.fail-case-exit {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.7rem;
  color: var(--text-s, #475569);
}
.fail-case-msg {
  font-size: 0.76rem;
  color: var(--text-s, #475569);
  margin-top: 3px;
}
.fail-case-log {
  margin: 5px 0 0;
  padding: 7px 9px;
  background: var(--surface-h, #f8fafc);
  border: 1px solid var(--border, #e2e8f0);
  border-radius: var(--r-sm, 4px);
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.7rem;
  line-height: 1.45;
  color: var(--text-s, #475569);
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 160px;
  overflow-y: auto;
}
.fail-case-empty {
  font-size: 0.76rem;
  color: var(--text-m, #94a3b8);
}
</style>
