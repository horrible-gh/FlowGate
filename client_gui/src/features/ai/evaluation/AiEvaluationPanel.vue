<template>
  <div class="ai-evaluation-panel">
    <div class="detail-head">
      <div>
        <div class="page-title">{{ t('gui.ai.evaluation.title') }}</div>
        <div class="page-sub">{{ t('gui.ai.evaluation.subtitle') }}</div>
      </div>
      <div class="detail-actions">
        <Button
          variant="primary"
          :disabled="!selectedProviderId || busy || isRunning"
          :loading="busy"
          @click="start"
        >
          <svg viewBox="0 0 24 24" fill="currentColor" width="14" height="14">
            <path d="m8 5 11 7-11 7z" />
          </svg>
          {{ current ? t('gui.ai.evaluation.rerun') : t('gui.ai.evaluation.start') }}
        </Button>
      </div>
    </div>

    <!-- Provider Selection Bar -->
    <div class="provider-select-bar">
      <div class="bar-left">
        <label for="eval-provider-select" class="select-label">
          {{ t('gui.ai.evaluation.target_provider') }}
        </label>
        <div class="select-wrapper">
          <Select
            id="eval-provider-select"
            v-model="selectedProviderId"
            :options="providerOptions"
            :placeholder="t('gui.ai.evaluation.select_provider_hint')"
          />
        </div>
      </div>

      <div class="bar-actions">
        <Button
          v-if="isRunning"
          variant="danger"
          size="sm"
          :disabled="busy"
          @click="cancel"
        >
          {{ t('gui.ai.evaluation.cancel') }}
        </Button>
        <Button
          variant="secondary"
          size="sm"
          :disabled="!selectedProviderId || busy"
          @click="refresh"
        >
          {{ t('gui.common.refresh') }}
        </Button>
      </div>
    </div>

    <div v-if="error" class="panel-error-alert">{{ error }}</div>

    <div class="evaluation-layout">
      <!-- Main Left Panel -->
      <section class="detail-panel">
        <div class="detail-toolbar">
          <label class="detail-search">
            ⌕ <input v-model="historyFilter" :placeholder="t('gui.ai.evaluation.search_placeholder')" />
          </label>
          <span class="detail-hint">{{ t('gui.ai.evaluation.table_hint') }}</span>
        </div>

        <!-- Current / Running Highlight -->
        <div v-if="current" class="current-eval-card" :class="{ 'is-running': isRunning }">
          <div class="current-top">
            <div class="current-provider">
              <span class="avatar-round" :class="getAvatarClass(current.provider_name || current.provider_id)">
                {{ getInitials(current.provider_name || current.provider_id) }}
              </span>
              <div>
                <b>{{ current.provider_name || current.provider_id }}</b>
                <div class="current-meta">
                  <span>{{ formatDateTime(current.created_at) }}</span>
                  <Badge :tone="getLifecycleTone(current.lifecycle)" size="sm">
                    {{ current.lifecycle }}
                  </Badge>
                  <Badge v-if="current.is_stale" tone="warning" size="sm">
                    {{ t('gui.ai.evaluation.stale') }}
                  </Badge>
                </div>
              </div>
            </div>

            <div class="current-score-box">
              <Badge
                :tone="current.verdict === 'suitable' ? 'success' : (current.verdict === 'unsuitable' ? 'danger' : getLifecycleTone(current.lifecycle))"
                size="md"
              >
                {{ isRunning ? t('gui.ai.evaluation.lifecycle_running') : (current.verdict ? t(`gui.ai.evaluation.verdict_${current.verdict}`) : current.lifecycle) }}
              </Badge>
            </div>
          </div>



          <p v-if="current.diagnostic_summary" class="current-summary">
            {{ current.diagnostic_summary }}
          </p>
        </div>

        <div v-else-if="!selectedProviderId" class="empty-state-box">
          {{ t('gui.ai.evaluation.select_provider_prompt') }}
        </div>

        <div v-else class="empty-state-box">
          {{ t('gui.ai.evaluation.no_current_run') }}
        </div>

        <!-- History List Header -->
        <div class="history-section-title">
          {{ t('gui.ai.evaluation.history_title') }}
        </div>

        <!-- History Table -->
        <div class="evaluation-table">
          <div v-if="filteredHistory.length === 0" class="empty-row">
            {{ t('gui.ai.evaluation.history_empty') }}
          </div>

          <div
            v-for="item in filteredHistory"
            :key="item.evaluation_id"
            class="evaluation-detail-row clickable"
            @click="openDetail(item)"
          >
            <span class="avatar-round" :class="getAvatarClass(item.provider_name || item.provider_id)">
              {{ getInitials(item.provider_name || item.provider_id) }}
            </span>
            <div class="eval-info">
              <b>{{ item.provider_name || item.provider_id }}</b>
              <small>{{ formatDateTime(item.created_at) }}</small>
            </div>
            <div class="eval-verdict">
              <Badge
                :tone="item.verdict === 'suitable' ? 'success' : (item.verdict === 'unsuitable' ? 'danger' : getLifecycleTone(item.lifecycle))"
                size="sm"
              >
                {{ item.verdict ? t(`gui.ai.evaluation.verdict_${item.verdict}`) : item.lifecycle }}
              </Badge>
            </div>
            <p>{{ getHistorySummary(item) }}</p>
            <span class="chev">›</span>
          </div>

          <div v-if="nextCursor" class="load-more-row">
            <Button variant="secondary" size="sm" @click="loadMore">
              {{ t('gui.ai.evaluation.load_more') }}
            </Button>
          </div>
        </div>
      </section>

      <!-- Right Side Info Panel -->
      <aside class="evaluation-side">
        <div class="side-title">{{ t('gui.ai.evaluation.method_title') }}</div>
        <p>{{ t('gui.ai.evaluation.method_desc') }}</p>

        <div class="axis-chips">
          <span>{{ t('gui.ai.evaluation.axis_understanding') }}</span>
          <span>{{ t('gui.ai.evaluation.axis_decomposition') }}</span>
          <span>{{ t('gui.ai.evaluation.axis_exploration') }}</span>
          <span>{{ t('gui.ai.evaluation.axis_judgment') }}</span>
          <span>{{ t('gui.ai.evaluation.axis_reasoning') }}</span>
          <span>{{ t('gui.ai.evaluation.axis_tools') }}</span>
          <span>{{ t('gui.ai.evaluation.axis_execution') }}</span>
          <span>{{ t('gui.ai.evaluation.axis_verification') }}</span>
          <span>{{ t('gui.ai.evaluation.axis_reporting') }}</span>
          <span>{{ t('gui.ai.evaluation.axis_completeness') }}</span>
        </div>

        <div class="scratch-preview">
          <b>scratch/eval_xxxx/</b><br />
          ├ instruction.md<br />
          ├ sample-project/<br />
          ├ expected-constraints.json<br />
          └ output/
        </div>

        <div class="eval-meta">
          <span>{{ t('gui.ai.evaluation.meta_result') }}</span>
          <b>{{ t('gui.ai.evaluation.meta_result_val') }}</b>
          <span>{{ t('gui.ai.evaluation.meta_evaluator') }}</span>
          <b>FlowGate Baseline</b>
          <span>{{ t('gui.ai.evaluation.meta_history') }}</span>
          <b>{{ t('gui.ai.evaluation.meta_history_val') }}</b>
        </div>
      </aside>
    </div>

    <!-- Detail Modal -->
    <AiEvaluationDetailModal
      :open="detailOpen"
      :evaluation="selectedDetail"
      @close="detailOpen = false"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Badge from '@/components/ui/Badge.vue'
import Button from '@/components/ui/Button.vue'
import Select from '@/components/ui/Select.vue'
import {
  cancelEvaluation,
  fetchCurrentEvaluation,
  fetchEvaluationDetail,
  fetchEvaluationHistory,
  startEvaluation,
} from '../api'
import type {
  AiEvaluation,
  EvaluationLifecycle,
  ProviderItem,
} from '../types'
import AiEvaluationDetailModal from './AiEvaluationDetailModal.vue'

const props = defineProps<{
  projectId: string
  providers: ProviderItem[]
}>()

const emit = defineEmits<{
  changed: []
}>()

const { t } = useI18n()

const selectedProviderId = ref('')
const current = ref<AiEvaluation | null>(null)
const latestHistory = ref<AiEvaluation | null>(null)
const history = ref<AiEvaluation[]>([])
const nextCursor = ref<string | null>(null)
const busy = ref(false)
const error = ref('')
const historyFilter = ref('')

const detailOpen = ref(false)
const selectedDetail = ref<AiEvaluation | null>(null)

let requestGeneration = 0
let activeRunId = ''
let pollTimer: ReturnType<typeof setInterval> | null = null

const providerOptions = computed(() => {
  return props.providers
    .filter((p) => p.enabled !== false)
    .map((p) => ({
      value: p.id,
      label: `${p.name} (${p.kind} · ${p.exec_type.toUpperCase()})`,
    }))
})

const isRunning = computed(() => {
  return current.value ? ['pending', 'running'].includes(current.value.lifecycle) : false
})

const filteredHistory = computed(() => {
  const q = historyFilter.value.trim().toLowerCase()
  if (!q) return history.value
  return history.value.filter(
    (h) =>
      (h.provider_name || h.provider_id).toLowerCase().includes(q) ||
      (h.diagnostic_summary || '').toLowerCase().includes(q)
  )
})

function isCurrent(project: string, provider: string, gen: number): boolean {
  return props.projectId === project && selectedProviderId.value === provider && gen === requestGeneration
}

async function refresh(): Promise<void> {
  const project = props.projectId
  const provider = selectedProviderId.value
  const gen = ++requestGeneration
  if (!project || !provider) return

  try {
    const [currentRes, historyRes] = await Promise.allSettled([
      fetchCurrentEvaluation(project, provider),
      fetchEvaluationHistory(project, provider, 20),
    ])

    if (!isCurrent(project, provider, gen)) return
    error.value = ''

    if (currentRes.status === 'fulfilled') {
      current.value = currentRes.value.current
      latestHistory.value = currentRes.value.latest_history
      if (
        current.value &&
        current.value.evaluation_id === activeRunId &&
        !['pending', 'running'].includes(current.value.lifecycle)
      ) {
        activeRunId = ''
        emit('changed')
      }
    } else {
      error.value = currentRes.reason instanceof Error ? currentRes.reason.message : 'Failed to load current evaluation'
    }

    if (historyRes.status === 'fulfilled') {
      history.value = historyRes.value.items || []
      nextCursor.value = historyRes.value.next_cursor
    } else {
      error.value = historyRes.reason instanceof Error ? historyRes.reason.message : 'Failed to load evaluation history'
    }
  } catch (err: unknown) {
    if (isCurrent(project, provider, gen)) {
      error.value = err instanceof Error ? err.message : 'Error refreshing evaluation'
    }
  }
}

async function loadMore(): Promise<void> {
  const project = props.projectId
  const provider = selectedProviderId.value
  const cursor = nextCursor.value
  const gen = requestGeneration
  if (!cursor || !project || !provider) return

  try {
    const res = await fetchEvaluationHistory(project, provider, 20, cursor)
    if (!isCurrent(project, provider, gen)) return
    history.value = [...history.value, ...res.items]
    nextCursor.value = res.next_cursor
  } catch (err: unknown) {
    if (isCurrent(project, provider, gen)) {
      error.value = err instanceof Error ? err.message : 'Failed to load more evaluations'
    }
  }
}

async function readRun(project: string, provider: string, evaluationId: string): Promise<void> {
  try {
    const data = await fetchEvaluationDetail(project, evaluationId)
    if (
      props.projectId !== project ||
      selectedProviderId.value !== provider ||
      activeRunId !== evaluationId
    ) {
      return
    }
    current.value = data
    if (!['pending', 'running'].includes(data.lifecycle)) {
      activeRunId = ''
      await refresh()
      emit('changed')
    }
  } catch (err: unknown) {
    if (
      props.projectId === project &&
      selectedProviderId.value === provider &&
      activeRunId === evaluationId
    ) {
      error.value = err instanceof Error ? err.message : 'Failed to poll evaluation'
    }
  }
}

async function start(): Promise<void> {
  const project = props.projectId
  const provider = selectedProviderId.value
  if (!project || !provider) return
  busy.value = true
  error.value = ''

  try {
    const data = await startEvaluation(project, provider)
    if (props.projectId !== project || selectedProviderId.value !== provider) return
    activeRunId = data.evaluation_id
    current.value = data
    emit('changed')
    await refresh()
  } catch (err: any) {
    const conflict = err?.response?.data?.error
    if (
      err?.response?.status === 409 &&
      conflict?.code === 'evaluation_already_running' &&
      conflict.evaluation_id &&
      props.projectId === project &&
      selectedProviderId.value === provider
    ) {
      activeRunId = conflict.evaluation_id
      await readRun(project, provider, activeRunId)
      await refresh()
    } else if (props.projectId === project && selectedProviderId.value === provider) {
      error.value = err instanceof Error ? err.message : 'Failed to start evaluation'
    }
  } finally {
    busy.value = false
  }
}

async function cancel(): Promise<void> {
  if (!current.value) return
  const project = props.projectId
  const provider = selectedProviderId.value
  const evaluationId = current.value.evaluation_id
  busy.value = true

  try {
    await cancelEvaluation(project, evaluationId)
    if (props.projectId === project && selectedProviderId.value === provider) {
      activeRunId = ''
      await refresh()
      emit('changed')
    }
  } catch (err: unknown) {
    if (props.projectId === project && selectedProviderId.value === provider) {
      error.value = err instanceof Error ? err.message : 'Failed to cancel evaluation'
    }
  } finally {
    busy.value = false
  }
}

function openDetail(item: AiEvaluation) {
  selectedDetail.value = item
  detailOpen.value = true
}

watch(
  () => props.providers,
  (list) => {
    const selectable = (list ?? []).filter((p) => p.enabled !== false)
    if (selectable.length > 0) {
      if (!selectable.some((p) => p.id === selectedProviderId.value)) {
        selectedProviderId.value = selectable[0].id
      }
    } else {
      selectedProviderId.value = ''
    }
  },
  { immediate: true, deep: true }
)

watch(
  () => [props.projectId, selectedProviderId.value],
  () => {
    requestGeneration++
    activeRunId = ''
    current.value = null
    latestHistory.value = null
    history.value = []
    error.value = ''
    if (selectedProviderId.value) {
      void refresh()
    }
  },
  { immediate: true }
)

pollTimer = setInterval(() => {
  if (activeRunId && props.projectId && selectedProviderId.value) {
    void readRun(props.projectId, selectedProviderId.value, activeRunId)
  } else if (current.value && ['pending', 'running'].includes(current.value.lifecycle)) {
    void refresh()
  }
}, 3000)

onBeforeUnmount(() => {
  if (pollTimer) clearInterval(pollTimer)
  requestGeneration++
})

function getHistorySummary(item: AiEvaluation): string {
  if (item.diagnostic_summary && item.diagnostic_summary.trim()) {
    return item.diagnostic_summary
  }
  if (item.lifecycle === 'running') {
    return t('gui.ai.evaluation.lifecycle_running')
  }
  if (item.lifecycle === 'failed') {
    return t('gui.ai.evaluation.lifecycle_failed')
  }
  if (item.lifecycle === 'cancelled') {
    return t('gui.ai.evaluation.lifecycle_cancelled')
  }
  if (item.verdict === 'unsuitable') {
    return t('gui.ai.evaluation.verdict_unsuitable')
  }
  if (item.verdict === 'suitable') {
    return t('gui.ai.evaluation.baseline_passed')
  }
  return t('gui.ai.evaluation.no_diagnostic')
}

function getLifecycleTone(status: EvaluationLifecycle): 'neutral' | 'info' | 'success' | 'warning' | 'danger' {
  switch (status) {
    case 'completed': return 'success'
    case 'running': return 'info'
    case 'pending': return 'warning'
    case 'failed': return 'danger'
    case 'cancelled': return 'neutral'
    default: return 'neutral'
  }
}

function getInitials(name: string): string {
  if (!name) return 'AI'
  const parts = name.trim().split(/\s+/)
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase()
  return name.slice(0, 2).toUpperCase()
}

function getAvatarClass(name: string): string {
  const n = (name || '').toLowerCase()
  if (n.includes('sonnet') || n.includes('claude')) return 'av-cs'
  if (n.includes('opus')) return 'av-co'
  if (n.includes('haiku')) return 'av-ch'
  if (n.includes('gpt') || n.includes('openai')) return 'av-og'
  if (n.includes('gemini')) return 'av-gp'
  if (n.includes('llama')) return 'av-l3'
  if (n.includes('terra')) return 'av-te'
  if (n.includes('luna')) return 'av-lu'
  return 'av-cs'
}

function formatDateTime(iso?: string | null): string {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  } catch {
    return iso
  }
}
</script>

<style scoped>
.ai-evaluation-panel {
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: 100%;
}

.detail-head {
  display: flex;
  align-items: flex-start;
  gap: 20px;
  margin-bottom: 16px;
}

.page-title {
  font-size: 21px;
  font-weight: 850;
  margin: 0 0 6px;
  color: var(--fg2-text, #edf3fa);
}

.page-sub {
  color: #b9c8d8;
  font-size: 14.5px;
  margin: 0;
}

.detail-actions {
  margin-left: auto;
  display: flex;
  gap: 8px;
}

.provider-select-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 18px;
  margin-bottom: 14px;
  border: 1px solid var(--fg2-line);
  border-radius: 10px;
  background: #0d1b2b;
}

.bar-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.select-label {
  font-size: 13px;
  font-weight: 700;
  color: #c8d7e7;
  white-space: nowrap;
}

.select-wrapper {
  min-width: 320px;
}

.bar-actions {
  display: flex;
  gap: 8px;
}

.evaluation-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 310px;
  gap: 16px;
  flex: 1;
  min-height: 0;
}

.detail-panel {
  border: 1px solid var(--fg2-line);
  border-radius: 12px;
  background: linear-gradient(180deg, rgba(13, 26, 40, 0.94), rgba(10, 22, 35, 0.92));
  overflow: hidden;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.detail-toolbar {
  height: 50px;
  padding: 0 16px;
  display: flex;
  align-items: center;
  gap: 14px;
  border-bottom: 1px solid var(--fg2-line);
  flex: none;
}

.detail-search {
  height: 32px;
  min-width: 260px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 10px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: 8px;
  background: #0c1b2c;
  color: #8399b0;
  font-size: 12.5px;
}

.detail-search input {
  min-width: 0;
  flex: 1;
  border: 0;
  outline: 0;
  background: transparent;
  color: #dce7f3;
  font-size: 12px;
}

.detail-hint {
  font-size: 11.5px;
  color: var(--fg2-muted);
}

.current-eval-card {
  margin: 14px 16px;
  padding: 16px 18px;
  border-radius: 10px;
  border: 1px solid rgba(120, 177, 233, 0.25);
  background: linear-gradient(145deg, rgba(18, 38, 62, 0.6), rgba(12, 26, 42, 0.8));
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.current-eval-card.is-running {
  border-color: rgba(75, 163, 255, 0.6);
  animation: pulse-border 2s infinite ease-in-out;
}

@keyframes pulse-border {
  0%, 100% { border-color: rgba(75, 163, 255, 0.4); }
  50% { border-color: rgba(75, 163, 255, 0.8); }
}

.current-top {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
}

.current-provider {
  display: flex;
  align-items: center;
  gap: 12px;
}

.current-provider b {
  font-size: 15px;
  color: #edf3fa;
}

.current-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
  font-size: 11.5px;
  color: var(--fg2-muted);
}

.current-score-box {
  text-align: right;
}

.current-score-box small {
  font-size: 11px;
  color: var(--fg2-muted);
  display: block;
}

.score {
  font-family: var(--fg2-font-mono, monospace);
  font-weight: 800;
  font-size: 24px;
  color: #8be2ff;
  line-height: 1;
}

.score--unsuitable {
  color: #ff9d9d;
}

.current-bars {
  display: grid;
  gap: 5px;
  background: rgba(10, 21, 34, 0.5);
  padding: 10px 14px;
  border-radius: 8px;
  border: 1px solid rgba(154, 181, 210, 0.08);
}

.barline {
  display: grid;
  grid-template-columns: 28px 1fr;
  gap: 8px;
  align-items: center;
  font-size: 10px;
  color: #90a4ba;
}

.track {
  height: 6px;
  border-radius: 999px;
  background: #172a42;
  overflow: hidden;
}

.fill {
  height: 100%;
  border-radius: 999px;
  background: linear-gradient(90deg, #67b6ff, #54d1ff);
}

.fill.p2 { background: linear-gradient(90deg, #57d2c9, #5fe0b6); }
.fill.p3 { background: linear-gradient(90deg, #7ba8ff, #8c84ff); }
.fill.p4 { background: linear-gradient(90deg, #8f74ff, #cb7cff); }

.current-summary {
  margin: 0;
  font-size: 12px;
  color: #a9bed4;
  line-height: 1.5;
}

.empty-state-box {
  padding: 32px 16px;
  text-align: center;
  color: var(--fg2-muted);
  font-size: 13px;
}

.history-section-title {
  padding: 8px 16px;
  font-size: 12px;
  font-weight: 800;
  color: #8da2b6;
  background: #0a1726;
  border-top: 1px solid var(--fg2-line);
  border-bottom: 1px solid var(--fg2-line);
}

.evaluation-table {
  padding: 0 14px 12px;
  overflow-y: auto;
  flex: 1;
}

.evaluation-detail-row {
  display: grid;
  grid-template-columns: 38px 170px 58px 240px minmax(140px, 1fr) 16px;
  gap: 12px;
  align-items: center;
  min-height: 80px;
  border-bottom: 1px solid rgba(154, 181, 210, 0.08);
  padding: 8px 4px;
}

.evaluation-detail-row:last-child {
  border-bottom: 0;
}

.evaluation-detail-row b {
  font-size: 13px;
  color: #edf3fa;
  display: block;
}

.evaluation-detail-row small {
  font-size: 10.5px;
  color: var(--fg2-muted);
  display: block;
  margin-top: 3px;
}

.evaluation-detail-row > strong {
  font-family: var(--fg2-font-mono, monospace);
  font-weight: 800;
  font-size: 18px;
  color: #7fddff;
}

.evaluation-detail-row p {
  font-size: 11.5px;
  color: #9fb1c4;
  line-height: 1.5;
  margin: 0;
}

.detail-bars {
  display: grid;
  gap: 4px;
}

.detail-bars span {
  position: relative;
  height: 12px;
  padding-left: 32px;
  font-size: 9px;
  color: #8fa4ba;
}

.detail-bars span::before {
  content: "";
  position: absolute;
  left: 32px;
  right: 0;
  top: 3px;
  height: 6px;
  border-radius: 99px;
  background: #172a42;
}

.detail-bars span::after {
  content: "";
  position: absolute;
  left: 32px;
  top: 3px;
  width: calc((100% - 32px) * var(--w) / 100);
  height: 6px;
  border-radius: 99px;
  background: linear-gradient(90deg, #57cfc8, #66bfff);
}

.chev {
  color: #88a0b8;
  font-size: 22px;
  line-height: 1;
}

.clickable {
  cursor: pointer;
}

.clickable:hover {
  background: rgba(255, 255, 255, 0.02);
}

.load-more-row {
  display: flex;
  justify-content: center;
  padding: 14px 0;
}

.empty-row {
  padding: 30px 16px;
  text-align: center;
  color: var(--fg2-muted);
  font-size: 12.5px;
}

.evaluation-side {
  border: 1px solid var(--fg2-line);
  border-radius: 12px;
  background: #0d1b2b;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.side-title {
  font-size: 14.5px;
  font-weight: 850;
  color: #edf3fa;
}

.evaluation-side p {
  color: #9fb1c4;
  font-size: 12px;
  line-height: 1.5;
  margin: 0;
}

.axis-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}

.axis-chips span {
  height: 24px;
  padding: 0 7px;
  border-radius: 6px;
  background: #11263b;
  border: 1px solid rgba(93, 145, 199, 0.18);
  color: #b9d4ee;
  font-size: 10px;
  display: inline-flex;
  align-items: center;
}

.scratch-preview {
  margin-top: 4px;
  padding: 10px 12px;
  border: 1px solid var(--fg2-line);
  border-radius: 8px;
  background: #081522;
  color: #8ea4ba;
  font: 11px/1.65 var(--fg2-font-mono, monospace);
}

.scratch-preview b {
  color: #c8d8e8;
}

.eval-meta {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 6px 10px;
  margin-top: 8px;
  font-size: 11px;
}

.eval-meta span {
  color: #788fa7;
}

.eval-meta b {
  font-weight: 700;
  color: #c8d8e8;
}

.avatar-round {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  font-weight: 800;
  font-size: 11px;
  color: #fff;
  flex: none;
}

.av-cs, .av-co, .av-ch { background: linear-gradient(145deg, #d98466, #8d3f31); }
.av-og, .av-o4, .av-lu { background: linear-gradient(145deg, #67a2ff, #3667d7); }
.av-gp { background: linear-gradient(145deg, #9b79ff, #6f49d7); }
.av-l3 { background: linear-gradient(145deg, #58adff, #2e77c6); }
.av-te { background: linear-gradient(145deg, #e6b089, #b97851); }

.panel-error-alert {
  padding: 10px 14px;
  border-radius: 8px;
  background: rgba(239, 68, 68, 0.15);
  border: 1px solid rgba(239, 68, 68, 0.3);
  color: #fca5a5;
  font-size: 12px;
  margin-bottom: 12px;
}

@media (max-width: 1200px) {
  .evaluation-layout {
    grid-template-columns: 1fr;
  }
}
</style>