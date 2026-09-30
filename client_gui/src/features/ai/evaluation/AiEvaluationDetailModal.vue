<template>
  <ModalShell :open="open" size="lg" @close="emit('close')">
    <template #header="{ titleId }">
      <div class="dialog-header">
        <h2 :id="titleId" class="dialog-title">{{ t('gui.ai.evaluation.detail_title') }}</h2>
        <Badge v-if="evaluation" :tone="lifecycleTone(evaluation.lifecycle)" size="sm">
          {{ t(`gui.ai.evaluation.lifecycle_${evaluation.lifecycle}`) }}
        </Badge>
      </div>
    </template>

    <div class="detail-body">
      <LoadingState v-if="loading && !evaluation" :label="t('gui.common.loading')" />
      <p v-else-if="loadError" class="detail-error" role="alert">{{ loadError }}</p>
      <template v-else-if="evaluation">
        <section class="parties">
          <div>
            <span class="k">{{ t('gui.ai.evaluation.target_ai') }}</span>
            <b>{{ evaluation.target.provider_name || evaluation.target.provider_id }}</b>
          </div>
          <div>
            <span class="k">{{ t('gui.ai.evaluation.judge_ai') }}</span>
            <b>{{ evaluation.judge.provider_name || evaluation.judge.provider_id }}</b>
          </div>
          <div class="total">
            <span class="k">{{ t('gui.ai.evaluation.total_score') }}</span>
            <b v-if="evaluation.final_score !== null" :class="scoreClass(evaluation.final_score)">
              {{ formatScore(evaluation.final_score) }}<small>/10</small>
            </b>
            <span v-else class="muted">—</span>
            <small v-if="evaluation.human_review?.score != null" class="muted">
              AI {{ formatScore(evaluation.total_score) }}
            </small>
          </div>
        </section>

        <section v-if="evaluation.lifecycle === 'failed' || evaluation.lifecycle === 'cancelled'" class="failure">
          <b>{{ t(`gui.ai.evaluation.lifecycle_${evaluation.lifecycle}`) }}</b>
          <span v-if="evaluation.failure_stage">
            · {{ t('gui.ai.evaluation.failure_stage') }}: {{ t(`gui.ai.evaluation.lifecycle_${evaluation.failure_stage}`) }}
          </span>
          <span v-if="evaluation.reason_code"> · {{ reasonLabel(evaluation.reason_code) }}</span>
          <p v-if="evaluation.diagnostic_summary">{{ evaluation.diagnostic_summary }}</p>
        </section>

        <section class="block">
          <h3>
            {{ t('gui.ai.evaluation.task') }}
            <Badge v-if="evaluation.evaluation_set" tone="info" size="sm">{{ evaluation.evaluation_set.name }}</Badge>
          </h3>
          <template v-if="evaluation.task.title">
            <b class="task-title">{{ evaluation.task.title }}</b>
            <p v-if="evaluation.task.summary" class="task-summary">{{ evaluation.task.summary }}</p>
            <details v-if="evaluation.task.instruction">
              <summary>{{ t('gui.ai.evaluation.instruction') }}</summary>
              <pre>{{ evaluation.task.instruction }}</pre>
            </details>
          </template>
          <p v-else class="muted">{{ t('gui.ai.evaluation.task_pending') }}</p>
        </section>

        <section class="block">
          <h3>{{ t('gui.ai.evaluation.item_scores') }}</h3>
          <p v-if="!evaluation.scores.length" class="muted">{{ t('gui.ai.evaluation.scores_pending') }}</p>
          <div v-for="item in evaluation.scores" :key="item.key" class="score-item">
            <div class="score-line">
              <span class="score-name">{{ criterionLabel(item.key) }}</span>
              <span class="track"><span class="fill" :style="{ width: `${item.score ?? 0}%` }" /></span>
              <b class="score-val">{{ formatScore(item.score) }}</b>
            </div>
            <p v-if="item.description" class="score-desc">{{ t('gui.ai.evaluation.standard') }}: {{ item.description }}</p>
            <p v-if="item.rationale" class="score-rationale">{{ item.rationale }}</p>
          </div>
        </section>

        <section class="block">
          <h3>{{ t('gui.ai.evaluation.review') }}</h3>
          <p v-if="evaluation.review_summary" class="review">{{ evaluation.review_summary }}</p>
          <p v-else class="muted">{{ t('gui.ai.evaluation.review_pending') }}</p>
        </section>

        <!-- Human review: optional, completed evaluations only. Its score becomes the final score. -->
        <section v-if="evaluation.lifecycle === 'completed'" class="block">
          <h3>{{ t('gui.ai.evaluation.human_review') }}</h3>
          <div class="human-form">
            <label>
              <span>{{ t('gui.ai.evaluation.human_score') }}</span>
              <input v-model="humanScore" class="score-input" type="number" min="0" max="10" step="0.1" :disabled="savingHuman" />
            </label>
            <Textarea v-model="humanComment" :rows="2" :placeholder="t('gui.ai.evaluation.human_comment')" :disabled="savingHuman" />
            <div class="human-actions">
              <Button v-if="evaluation.human_review" variant="ghost" size="sm" :disabled="savingHuman" @click="saveHuman(true)">
                {{ t('gui.ai.evaluation.human_clear') }}
              </Button>
              <Button variant="secondary" size="sm" :loading="savingHuman" :disabled="!humanValid" @click="saveHuman(false)">
                {{ t('gui.common.save') }}
              </Button>
            </div>
            <p v-if="humanError" class="detail-error" role="alert">{{ humanError }}</p>
          </div>
        </section>

        <section class="block">
          <h3>{{ t('gui.ai.evaluation.work_products') }}</h3>
          <details v-if="evaluation.target_report">
            <summary>{{ t('gui.ai.evaluation.target_report') }}</summary>
            <pre>{{ evaluation.target_report }}</pre>
          </details>
          <p v-if="!(evaluation.artifacts ?? []).length" class="muted">{{ t('gui.ai.evaluation.no_artifacts') }}</p>
          <details v-for="artifact in evaluation.artifacts ?? []" :key="artifact.path" class="artifact">
            <summary>
              <code>{{ artifact.path }}</code>
              <span class="origin">{{ t(`gui.ai.evaluation.origin_${artifact.origin}`) }}</span>
              <span class="muted">{{ artifact.size }} B</span>
            </summary>
            <pre v-if="artifact.excerpt !== null">{{ artifact.excerpt }}</pre>
            <p v-else class="muted">{{ t('gui.ai.evaluation.binary_or_large') }}</p>
          </details>
          <p v-if="evaluation.scratch_ref" class="scratch-ref">
            {{ t('gui.ai.evaluation.scratch_ref') }}: <code>{{ evaluation.scratch_ref }}</code>
          </p>
        </section>

        <section class="meta">
          <span>{{ t('gui.ai.evaluation.created_at') }}: {{ formatDate(evaluation.created_at) }}</span>
          <span>{{ t('gui.ai.evaluation.started_at') }}: {{ formatDate(evaluation.started_at) }}</span>
          <span>{{ t('gui.ai.evaluation.finished_at') }}: {{ formatDate(evaluation.finished_at) }}</span>
        </section>
      </template>
    </div>

    <template #footer>
      <div class="dialog-actions">
        <Button variant="secondary" @click="emit('close')">{{ t('gui.common.close') }}</Button>
      </div>
    </template>
  </ModalShell>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Badge from '@/components/ui/Badge.vue'
import Button from '@/components/ui/Button.vue'
import LoadingState from '@/components/ui/LoadingState.vue'
import ModalShell from '@/components/ui/ModalShell.vue'
import Textarea from '@/components/ui/Textarea.vue'
import { fetchEvaluationDetail, saveHumanReview } from '../api'
import {
  ACTIVE_EVALUATION_LIFECYCLES,
  EVALUATION_CRITERIA,
  formatScore,
  scoreClass,
  type AiEvaluation,
  type EvaluationLifecycle,
} from '../types'

const props = defineProps<{
  open: boolean
  projectId: string
  evaluationId: string | null
}>()

const emit = defineEmits<{ close: []; changed: [] }>()
const { t, te } = useI18n()

const evaluation = ref<AiEvaluation | null>(null)
const humanScore = ref<string | number>('')
const humanComment = ref('')
const savingHuman = ref(false)
const humanError = ref('')
const loading = ref(false)
const loadError = ref('')
let epoch = 0
let timer: ReturnType<typeof setTimeout> | null = null

function stopTimer(): void {
  if (timer) clearTimeout(timer)
  timer = null
}

async function load(): Promise<void> {
  stopTimer()
  const current = ++epoch
  if (!props.open || !props.evaluationId) return
  loading.value = true
  try {
    const data = await fetchEvaluationDetail(props.projectId, props.evaluationId)
    if (current !== epoch) return
    if (evaluation.value?.evaluation_id !== data.evaluation_id || evaluation.value?.human_review?.reviewed_at !== data.human_review?.reviewed_at) {
      resetHuman(data)
    }
    evaluation.value = data
    loadError.value = ''
    // Follow an evaluation that is still preparing / running / being reviewed.
    if (ACTIVE_EVALUATION_LIFECYCLES.includes(data.lifecycle)) timer = setTimeout(() => void load(), 3000)
  } catch (err: unknown) {
    if (current === epoch) loadError.value = err instanceof Error ? err.message : t('gui.ai.evaluation.load_failed')
  } finally {
    if (current === epoch) loading.value = false
  }
}

watch(
  () => [props.open, props.evaluationId, props.projectId] as const,
  ([open]) => {
    evaluation.value = null
    loadError.value = ''
    if (open) void load()
    else {
      epoch++
      stopTimer()
    }
  },
  { immediate: true }
)

onBeforeUnmount(() => {
  epoch++
  stopTimer()
})

function resetHuman(data: AiEvaluation): void {
  const score = data.human_review?.score
  humanScore.value = typeof score === 'number' ? score / 10 : ''
  humanComment.value = data.human_review?.comment ?? ''
  humanError.value = ''
}

// Entered on the 10-point scale shown everywhere, stored 0-100.
const humanScoreValue = computed<number | null>(() => {
  if (humanScore.value === '' || humanScore.value === null) return null
  const value = Number(humanScore.value)
  return Number.isFinite(value) && value >= 0 && value <= 10 ? Math.round(value * 10) : NaN
})
const humanValid = computed(() =>
  !savingHuman.value && !Number.isNaN(humanScoreValue.value) && (humanScoreValue.value !== null || !!humanComment.value.trim())
)

async function saveHuman(clear: boolean): Promise<void> {
  const current = evaluation.value
  if (!current) return
  savingHuman.value = true
  humanError.value = ''
  try {
    const saved = await saveHumanReview(props.projectId, current.evaluation_id, clear
      ? { score: null, comment: null }
      : { score: humanScoreValue.value, comment: humanComment.value.trim() || null })
    evaluation.value = saved
    resetHuman(saved)
    emit('changed')
  } catch (err: unknown) {
    humanError.value = err instanceof Error ? err.message : t('gui.ai.evaluation.load_failed')
  } finally {
    savingHuman.value = false
  }
}

function criterionLabel(key: string): string {
  return (EVALUATION_CRITERIA as readonly string[]).includes(key) ? t(`gui.ai.evaluation.criteria.${key}`) : key
}

function reasonLabel(code: string): string {
  const key = `gui.ai.evaluation.reason.${code}`
  return te(key) ? t(key) : code
}

function lifecycleTone(lifecycle: EvaluationLifecycle): 'neutral' | 'info' | 'success' | 'warning' | 'danger' {
  if (lifecycle === 'completed') return 'success'
  if (lifecycle === 'failed') return 'danger'
  if (lifecycle === 'cancelled') return 'neutral'
  return 'info'
}

function formatDate(value?: string | null): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}
</script>

<style scoped>
.dialog-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 16px 20px 12px;
  border-bottom: 1px solid var(--fg2-line);
}

.dialog-title {
  margin: 0;
  font-size: 16px;
  font-weight: 800;
}

.human-form {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.human-form label {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12.5px;
  color: var(--fg2-muted);
}

.score-input {
  width: 90px;
  padding: 6px 8px;
  border-radius: 6px;
  border: 1px solid var(--fg2-line);
  background: transparent;
  color: var(--fg2-text);
}

.human-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.detail-body {
  padding: 16px 20px;
  max-height: min(70vh, 820px);
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 16px;
  color: var(--fg2-text);
  font-size: 13px;
}

.detail-error {
  color: #ff9aab;
}

.parties {
  display: grid;
  grid-template-columns: 1fr 1fr auto;
  gap: 12px;
  padding: 12px 14px;
  border: 1px solid var(--fg2-line);
  border-radius: 10px;
  background: #0d1b2b;
}

.parties > div {
  display: grid;
  gap: 4px;
}

.k {
  font-size: 11.5px;
  color: var(--fg2-muted);
  font-weight: 650;
}

.total b {
  font-size: 26px;
  line-height: 1;
}

.total small {
  font-size: 12px;
  color: var(--fg2-muted);
  margin-left: 2px;
}

.score-high { color: #72d6b6; }
.score-mid { color: #f2c46d; }
.score-low { color: #ff9aab; }

.failure {
  padding: 10px 14px;
  border-radius: 8px;
  border: 1px solid rgba(239, 68, 68, 0.3);
  background: rgba(239, 68, 68, 0.1);
  color: #fca5a5;
}

.failure p {
  margin: 6px 0 0;
  color: #f3c1c1;
}

.block h3 {
  margin: 0 0 8px;
  font-size: 13.5px;
  color: #eaf1f8;
}

.task-title {
  display: block;
  font-size: 14px;
}

.task-summary,
.review {
  margin: 6px 0;
  line-height: 1.55;
  white-space: pre-wrap;
}

details {
  margin-top: 6px;
}

summary {
  cursor: pointer;
  color: #b9c8d8;
  display: flex;
  align-items: center;
  gap: 8px;
}

pre {
  margin: 6px 0 0;
  padding: 8px 10px;
  max-height: 260px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: var(--fg2-font-mono, monospace);
  font-size: 12px;
  background: #091522;
  border: 1px solid var(--fg2-line);
  border-radius: 6px;
}

.score-item {
  padding: 8px 0;
  border-bottom: 1px solid rgba(154, 181, 210, 0.08);
}

.score-line {
  display: grid;
  grid-template-columns: 150px 1fr 40px;
  align-items: center;
  gap: 10px;
}

.score-name {
  font-weight: 650;
}

.track {
  height: 7px;
  border-radius: 4px;
  background: #102238;
  overflow: hidden;
}

.fill {
  display: block;
  height: 100%;
  background: linear-gradient(90deg, #57d2c9, #5fe0b6);
}

.score-val {
  text-align: right;
}

.score-desc,
.score-rationale {
  margin: 4px 0 0 160px;
  font-size: 12px;
  line-height: 1.5;
}

.score-desc {
  color: var(--fg2-muted);
}

.origin {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 5px;
  background: #11263c;
  color: #b4d1ff;
}

.scratch-ref {
  margin: 8px 0 0;
  font-size: 12px;
  color: var(--fg2-muted);
}

.muted {
  color: var(--fg2-muted);
}

.meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 18px;
  font-size: 11.5px;
  color: var(--fg2-muted);
}

.dialog-actions {
  display: flex;
  justify-content: flex-end;
  padding: 12px 20px;
  border-top: 1px solid var(--fg2-line);
}
</style>
