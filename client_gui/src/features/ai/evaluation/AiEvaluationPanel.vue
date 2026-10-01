<template>
  <div class="ai-evaluation-panel">
    <div class="detail-head">
      <div>
        <div class="page-title">
          {{ t('gui.ai.evaluation.title') }}
          <IconButton
            class="title-help"
            :label="t('gui.ai.evaluation.method_title')"
            :title="t('gui.ai.evaluation.method_title')"
            size="sm"
            @click="methodOpen = !methodOpen"
          >
            <AiIcon name="help" />
          </IconButton>
        </div>
      </div>
      <!-- System AI is evaluated by an administrator; everyone reads the results (CH0006 #48). -->
      <div v-if="isAdmin" class="detail-actions">
        <Button variant="secondary" @click="setsOpen = true">{{ t('gui.ai.evaluation.sets_manage') }}</Button>
        <Button variant="primary" @click="openStart">＋ {{ t('gui.ai.evaluation.new_evaluation') }}</Button>
      </div>
    </div>

    <div v-if="error" class="panel-error-alert">{{ error }}</div>

    <!-- Evaluation results / history -->
    <section class="detail-panel">
        <div class="detail-toolbar">
          <label class="detail-search">
            ⌕ <input v-model="searchQuery" :placeholder="t('gui.ai.evaluation.search_placeholder')" />
          </label>
          <div class="toolbar-filter">
            <Select v-model="filterTargetId" :options="filterOptions" />
          </div>
          <IconButton
            :class="{ 'is-spinning': loading }"
            :label="t('gui.common.refresh')"
            :title="t('gui.common.refresh')"
            size="sm"
            :disabled="loading"
            @click="reload()"
          >
            <AiIcon name="refresh" />
          </IconButton>
        </div>

        <div class="evaluation-table">
          <div v-if="loading && !items.length" class="empty-row">{{ t('gui.common.loading') }}</div>
          <div v-else-if="!items.length" class="empty-row">{{ t('gui.ai.evaluation.history_empty') }}</div>
          <div v-else-if="!visibleItems.length" class="empty-row">{{ t('gui.ai.evaluation.no_match') }}</div>
          <!-- One line per evaluation: what was evaluated, on which task, result/state, when.
               Judge, per-criterion scores and the review stay in the detail view. -->
          <div
            v-for="item in visibleItems"
            :key="item.evaluation_id"
            class="evaluation-detail-row"
            @click="openDetail(item)"
          >
            <span class="avatar-round" :class="getAvatarClass(partyName(item.target))">{{ getInitials(partyName(item.target)) }}</span>
            <div class="party-cell">
              <b>{{ partyName(item.target) }}</b>
              <small class="task-line">{{ taskLabel(item) }}</small>
              <small v-if="item.lifecycle === 'failed' && item.reason_code" class="task-line failure-line">
                <template v-if="item.failure_stage">{{ t(`gui.ai.evaluation.lifecycle_${item.failure_stage}`) }} · </template>{{ reasonLabel(item.reason_code) }}
              </small>
            </div>
            <strong v-if="item.final_score !== null" class="score" :class="scoreClass(item.final_score)">
              {{ formatScore(item.final_score) }}
            </strong>
            <span v-else class="score-state">
              <Badge :tone="lifecycleTone(item.lifecycle)" size="sm">{{ t(`gui.ai.evaluation.lifecycle_${item.lifecycle}`) }}</Badge>
            </span>
            <span class="date-cell">{{ formatDate(item.created_at) }}</span>
            <span class="row-actions" @click.stop>
              <button v-if="isAdmin && isActive(item)" type="button" class="action-btn action-btn--danger" @click="cancel(item)">
                {{ t('gui.ai.evaluation.cancel') }}
              </button>
              <button
                type="button"
                class="chev"
                :aria-label="t('gui.ai.evaluation.detail')"
                :title="t('gui.ai.evaluation.detail')"
                @click="openDetail(item)"
              >›</button>
            </span>
          </div>
        </div>
        <div v-if="nextCursor" class="load-more">
          <Button variant="ghost" size="sm" :loading="loadingMore" @click="loadMore">{{ t('gui.ai.evaluation.load_more') }}</Button>
        </div>
    </section>

    <!-- How the evaluation works: on demand, modeless so the list stays usable. -->
    <ModelessShell
      :open="methodOpen"
      :title="t('gui.ai.evaluation.method_title')"
      :initial-x="methodX"
      :initial-y="120"
      :initial-width="340"
      :initial-height="400"
      @close="methodOpen = false"
    >
      <div class="method-body">
        <p>{{ t('gui.ai.evaluation.method_desc') }}</p>
        <div class="axis-chips">
          <span v-for="key in EVALUATION_CRITERIA" :key="key">{{ t(`gui.ai.evaluation.criteria.${key}`) }}</span>
        </div>
        <div class="scratch-preview">
          <b>scratch/eval_xxxx/</b>
          <div v-for="entry in scratchLayout" :key="entry.name" class="scratch-line">
            <span>{{ entry.branch }} {{ entry.name }}</span>
          </div>
        </div>
        <div class="eval-meta">
          <span>{{ t('gui.ai.evaluation.meta_result') }}</span><b>{{ t('gui.ai.evaluation.meta_result_value') }}</b>
          <span>{{ t('gui.ai.evaluation.meta_judge') }}</span><b>{{ t('gui.ai.evaluation.meta_judge_value') }}</b>
          <span>{{ t('gui.ai.evaluation.meta_history') }}</span><b>{{ t('gui.ai.evaluation.meta_history_value') }}</b>
        </div>
      </div>
    </ModelessShell>

    <!-- New evaluation: target AI + judge AI -->
    <ModalShell :open="startOpen" size="sm" @close="startOpen = false">
      <template #header="{ titleId }">
        <div class="dialog-header">
          <h2 :id="titleId" class="dialog-title">{{ t('gui.ai.evaluation.new_evaluation') }}</h2>
        </div>
      </template>
      <div class="start-body">
        <label>
          <span>{{ t('gui.ai.evaluation.target_ai') }}</span>
          <Select v-model="targetId" :options="targetOptions" :placeholder="t('gui.ai.evaluation.choose_ai')" />
        </label>
        <label>
          <span>{{ t('gui.ai.evaluation.judge_ai') }}</span>
          <Select v-model="judgeId" :options="judgeOptions" :placeholder="t('gui.ai.evaluation.choose_ai')" />
        </label>
        <label>
          <span>{{ t('gui.ai.evaluation.task_source') }}</span>
          <Select v-model="setId" :options="setOptions" />
        </label>
        <p v-if="providers.length < 2" class="start-note">{{ t('gui.ai.evaluation.need_two') }}</p>
        <p v-else-if="sameSelected" class="start-error" role="alert">{{ t('gui.ai.evaluation.same_ai') }}</p>
        <p v-if="startError" class="start-error" role="alert">{{ startError }}</p>
      </div>
      <template #footer>
        <div class="dialog-actions">
          <Button variant="secondary" @click="startOpen = false">{{ t('gui.common.cancel') }}</Button>
          <Button variant="primary" :loading="starting" :disabled="!canStart" @click="start">
            {{ t('gui.ai.evaluation.start') }}
          </Button>
        </div>
      </template>
    </ModalShell>

    <AiEvaluationDetailModal
      :open="!!detailId"
      :evaluation-id="detailId"
      :can-review="isAdmin"
      @close="detailId = null"
      @changed="onDetailChanged"
    />
    <AiEvaluationSetsDialog v-if="isAdmin" :open="setsOpen" @close="setsOpen = false" @changed="(list) => (sets = list)" />
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Badge from '@/components/ui/Badge.vue'
import Button from '@/components/ui/Button.vue'
import IconButton from '@/components/ui/IconButton.vue'
import ModalShell from '@/components/ui/ModalShell.vue'
import ModelessShell from '@/components/ui/ModelessShell.vue'
import Select from '@/components/ui/Select.vue'
import { useCurrentUser } from '@/core/auth/currentUser'
import AiIcon from '../AiIcon.vue'
import { cancelEvaluation, fetchEvaluationHistory, listEvaluationSets, startEvaluation } from '../api'
import {
  ACTIVE_EVALUATION_LIFECYCLES,
  EVALUATION_CRITERIA,
  formatScore,
  scoreClass,
  type AiEvaluation,
  type AiEvaluationParty,
  type AiEvaluationSet,
  type EvaluationLifecycle,
  type ProviderItem,
} from '../types'
import AiEvaluationDetailModal from './AiEvaluationDetailModal.vue'
import AiEvaluationSetsDialog from './AiEvaluationSetsDialog.vue'

const props = defineProps<{
  providers: ProviderItem[]
}>()

const emit = defineEmits<{ changed: [] }>()
const { t, te, locale } = useI18n()
const { user } = useCurrentUser()
const isAdmin = computed(() => !!user.value?.isAdmin)

const targetId = ref('')
const judgeId = ref('')
const setId = ref('')
const sets = ref<AiEvaluationSet[]>([])
const setsOpen = ref(false)
const startOpen = ref(false)
const starting = ref(false)
const startError = ref('')
const error = ref('')
const loading = ref(false)
const loadingMore = ref(false)
const items = ref<AiEvaluation[]>([])
const nextCursor = ref<string | null>(null)
const filterTargetId = ref('')
const searchQuery = ref('')
const detailId = ref<string | null>(null)
const methodOpen = ref(false)
const methodX = Math.max(24, window.innerWidth - 380)
let epoch = 0
let pollTimer: ReturnType<typeof setTimeout> | null = null

const providerOptions = computed(() => props.providers.map((p) => ({ value: p.id, label: p.name })))
const targetOptions = computed(() => providerOptions.value)
const judgeOptions = computed(() =>
  providerOptions.value.map((option) => ({ ...option, disabled: option.value === targetId.value }))
)
const setOptions = computed(() => [
  { value: '', label: t('gui.ai.evaluation.task_by_judge') },
  ...sets.value.map((item) => ({ value: item.set_id, label: item.name })),
])
const filterOptions = computed(() => [
  { value: '', label: t('gui.ai.evaluation.all_targets') },
  ...providerOptions.value,
])
const sameSelected = computed(() => !!targetId.value && targetId.value === judgeId.value)
const canStart = computed(() => {
  const ids = new Set(props.providers.map((p) => p.id))
  return ids.has(targetId.value) && ids.has(judgeId.value) && !sameSelected.value && !starting.value
})
// Mirrors the server layout in ai_evaluation_service (INSTRUCTION_FILE / SAMPLE_DIR / CONSTRAINTS_FILE / OUTPUT_DIR).
const scratchLayout = [
  { branch: '├', name: 'instruction.md' },
  { branch: '├', name: 'sample-project/' },
  { branch: '├', name: 'expected-constraints.json' },
  { branch: '└', name: 'output/' },
]

const visibleItems = computed(() => {
  const query = searchQuery.value.trim().toLowerCase()
  if (!query) return items.value
  return items.value.filter((item) =>
    [partyName(item.target), item.task.title, item.evaluation_set?.name]
      .some((value) => (value || '').toLowerCase().includes(query))
  )
})

watch(targetId, (value) => {
  if (value && judgeId.value === value) judgeId.value = ''
})

watch(
  () => props.providers,
  (list) => {
    const ids = new Set(list.map((p) => p.id))
    if (!ids.has(targetId.value)) targetId.value = list[0]?.id ?? ''
    if (!ids.has(judgeId.value) || judgeId.value === targetId.value) {
      judgeId.value = list.find((p) => p.id !== targetId.value)?.id ?? ''
    }
  },
  { immediate: true }
)

function isActive(item: AiEvaluation): boolean {
  return ACTIVE_EVALUATION_LIFECYCLES.includes(item.lifecycle)
}

function partyName(party: AiEvaluationParty): string {
  return party.provider_name || party.provider_id
}

// Evaluation set name, else the judge's task title, else "not prepared yet".
function taskLabel(item: AiEvaluation): string {
  return item.evaluation_set?.name || item.task.title || t('gui.ai.evaluation.task_pending')
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

function getInitials(name: string): string {
  if (!name) return 'AI'
  const parts = name.trim().split(/\s+/)
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase()
  return name.slice(0, 2).toUpperCase()
}

function getAvatarClass(name: string): string {
  const n = name.toLowerCase()
  if (n.includes('opus')) return 'av-co'
  if (n.includes('haiku')) return 'av-ch'
  if (n.includes('sonnet') || n.includes('claude')) return 'av-cs'
  if (n.includes('gpt') || n.includes('openai')) return 'av-og'
  if (n.includes('gemini')) return 'av-gp'
  if (n.includes('llama')) return 'av-l3'
  if (n.includes('terra')) return 'av-te'
  if (n.includes('luna')) return 'av-lu'
  return 'av-cs'
}

function schedulePoll(): void {
  if (pollTimer) clearTimeout(pollTimer)
  pollTimer = null
  if (items.value.some(isActive)) pollTimer = setTimeout(() => void reload(true), 4000)
}

async function reload(silent = false): Promise<void> {
  const current = ++epoch
  if (!silent) loading.value = true
  try {
    const page = await fetchEvaluationHistory(filterTargetId.value || undefined, 20)
    if (current !== epoch) return
    const hadActive = items.value.some(isActive)
    items.value = page.items
    nextCursor.value = page.next_cursor
    error.value = ''
    if (hadActive && !page.items.some(isActive)) emit('changed')
  } catch (err: unknown) {
    if (current === epoch) error.value = err instanceof Error ? err.message : t('gui.ai.evaluation.load_failed')
  } finally {
    if (current === epoch) {
      loading.value = false
      schedulePoll()
    }
  }
}

async function loadMore(): Promise<void> {
  if (!nextCursor.value) return
  const current = epoch
  loadingMore.value = true
  try {
    const page = await fetchEvaluationHistory(filterTargetId.value || undefined, 20, nextCursor.value)
    if (current !== epoch) return
    items.value = [...items.value, ...page.items]
    nextCursor.value = page.next_cursor
  } catch (err: unknown) {
    error.value = err instanceof Error ? err.message : t('gui.ai.evaluation.load_failed')
  } finally {
    loadingMore.value = false
  }
}

async function loadSets(): Promise<void> {
  try {
    sets.value = await listEvaluationSets()
  } catch {
    sets.value = []
  }
  if (!sets.value.some((item) => item.set_id === setId.value)) setId.value = ''
}

function openStart(): void {
  if (!isAdmin.value) return
  startError.value = ''
  startOpen.value = true
  void loadSets()
}

function onDetailChanged(): void {
  void reload(true)
  emit('changed')
}

async function start(): Promise<void> {
  if (!canStart.value) return
  starting.value = true
  startError.value = ''
  try {
    const created = await startEvaluation({
      targetProviderId: targetId.value,
      judgeProviderId: judgeId.value,
      evaluationSetId: setId.value || null,
      locale: String(locale.value || ''),
    })
    startOpen.value = false
    detailId.value = created.evaluation_id
    await reload(true)
    emit('changed')
  } catch (err: unknown) {
    startError.value = err instanceof Error ? err.message : t('gui.ai.evaluation.start_failed')
  } finally {
    starting.value = false
  }
}

async function cancel(item: AiEvaluation): Promise<void> {
  try {
    await cancelEvaluation(item.evaluation_id)
    await reload(true)
    emit('changed')
  } catch (err: unknown) {
    error.value = err instanceof Error ? err.message : t('gui.ai.evaluation.cancel_failed')
  }
}

function openDetail(item: AiEvaluation): void {
  detailId.value = item.evaluation_id
}

watch(filterTargetId, () => void reload(), { immediate: true })

onBeforeUnmount(() => {
  epoch++
  if (pollTimer) clearTimeout(pollTimer)
})
</script>

<style scoped>
.failure-line {
  color: #fca5a5;
}

.ai-evaluation-panel {
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.detail-head {
  display: flex;
  align-items: flex-start;
  gap: 20px;
  margin-bottom: 16px;
}

.detail-actions {
  margin-left: auto;
  display: flex;
  gap: 8px;
}

.page-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 21px;
  font-weight: 850;
  margin: 0 0 6px;
  color: var(--fg2-text, #edf3fa);
}

.page-sub {
  color: #b9c8d8;
  font-size: 14.5px;
}

.detail-panel {
  border: 1px solid var(--fg2-line);
  border-radius: 12px;
  background: linear-gradient(180deg, rgba(13, 26, 40, 0.94), rgba(10, 22, 35, 0.92));
  overflow: hidden;
}

.detail-toolbar {
  height: 52px;
  padding: 0 16px;
  display: flex;
  align-items: center;
  gap: 14px;
  border-bottom: 1px solid var(--fg2-line);
}

.detail-search {
  height: 34px;
  min-width: 240px;
  flex: 1;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 11px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: 8px;
  background: #0c1b2c;
  color: #8399b0;
  font-size: 13px;
}

.detail-search input {
  min-width: 0;
  flex: 1;
  border: 0;
  outline: 0;
  background: transparent;
  color: #dce7f3;
  font-size: 12.5px;
}

.toolbar-filter {
  width: 200px;
}

.evaluation-detail-row {
  display: grid;
  grid-template-columns: 38px minmax(0, 1fr) 84px 160px auto;
  gap: 12px;
  align-items: center;
  min-height: 56px;
  padding: 10px 16px;
  border-bottom: 1px solid rgba(154, 181, 210, 0.08);
  font-size: 12.5px;
  color: #dce7f3;
  cursor: pointer;
}

.evaluation-detail-row:hover {
  background: rgba(28, 101, 197, 0.08);
}

.avatar-round {
  width: 38px;
  height: 38px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  color: #fff;
  font-size: 12px;
  font-weight: 800;
}

.av-cs, .av-co, .av-ch { background: linear-gradient(145deg, #d98466, #8d3f31); }
.av-og, .av-o4, .av-lu { background: linear-gradient(145deg, #67a2ff, #3667d7); }
.av-gp { background: linear-gradient(145deg, #9b79ff, #6f49d7); }
.av-l3 { background: linear-gradient(145deg, #58adff, #2e77c6); }
.av-te { background: linear-gradient(145deg, #e6b089, #b97851); }

.party-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.party-cell b {
  font-size: 13.5px;
  color: #eaf1f8;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.party-cell small {
  font-size: 11.5px;
  color: var(--fg2-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.score {
  font-size: 22px;
  font-weight: 850;
  text-align: center;
}

.score-state {
  display: flex;
  justify-content: center;
}

.score-high { color: #72d6b6; }
.score-mid { color: #f2c46d; }
.score-low { color: #ff9aab; }

.date-cell {
  font-size: 12px;
  color: var(--fg2-muted);
  white-space: nowrap;
}

.row-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
}

.chev {
  padding: 0 4px;
  border: 0;
  background: transparent;
  color: #6f86a0;
  font-size: 18px;
  cursor: pointer;
}

.action-btn {
  height: 28px;
  padding: 0 9px;
  border: 1px solid var(--fg2-line);
  border-radius: 6px;
  background: #102238;
  color: #b8cadc;
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
}

.action-btn:hover {
  background: #16304d;
}

.action-btn--danger {
  color: #fca5a5;
  border-color: rgba(239, 68, 68, 0.25);
}

.empty-row {
  padding: 36px 16px;
  text-align: center;
  color: var(--fg2-muted);
  font-size: 13px;
}

.load-more {
  display: flex;
  justify-content: center;
  padding: 10px;
}

.title-help {
  color: var(--fg2-muted);
}

.is-spinning :deep(svg) {
  animation: ai-spin 0.9s linear infinite;
}

@keyframes ai-spin {
  to {
    transform: rotate(360deg);
  }
}

.method-body {
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.method-body p {
  margin: 0;
  color: #b9c8d8;
  font-size: 12.5px;
  line-height: 1.55;
}

.axis-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.axis-chips span {
  height: 22px;
  padding: 0 8px;
  border-radius: 6px;
  border: 1px solid rgba(112, 151, 193, 0.2);
  background: #11263c;
  color: #dce9f8;
  font-size: 11.5px;
  display: inline-flex;
  align-items: center;
}

.scratch-preview {
  padding: 10px 12px;
  border: 1px solid var(--fg2-line);
  border-radius: 8px;
  background: #091522;
  font-family: var(--fg2-font-mono, monospace);
  font-size: 11.5px;
  color: #c8d7e7;
}

.scratch-line {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  margin-top: 3px;
}

.eval-meta {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 6px 12px;
  padding-top: 10px;
  border-top: 1px solid var(--fg2-line);
  font-size: 12px;
}

.eval-meta span {
  color: var(--fg2-muted);
}

.eval-meta b {
  color: #eaf1f8;
  text-align: right;
}

.dialog-header {
  padding: 16px 20px 12px;
  border-bottom: 1px solid var(--fg2-line);
}

.dialog-title {
  margin: 0;
  font-size: 16px;
  font-weight: 800;
}

.start-body {
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.start-body label {
  display: grid;
  gap: 6px;
  font-size: 12.5px;
  color: var(--fg2-control-fg, #c8d7e7);
  font-weight: 650;
}

.start-note {
  margin: 0;
  font-size: 12.5px;
  color: var(--fg2-muted);
}

.start-error {
  margin: 0;
  font-size: 12.5px;
  color: #ff9aab;
}

.dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 12px 20px;
  border-top: 1px solid var(--fg2-line);
}

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
  .evaluation-layout { grid-template-columns: 1fr; }
  .evaluation-detail-row { grid-template-columns: 38px minmax(0, 1fr) 72px 132px auto; }
}
</style>
