<template>
  <section class="card mb-4" data-test="ai-evaluation-panel">
    <div class="card-hd"><span class="card-title">{{ t('settings.project.ai_v02.evaluation') }}</span></div>
    <div class="card-bd pad">
      <label class="form-label" for="evaluation-provider">{{ t('settings.project.ai_v02.provider') }}</label>
      <select id="evaluation-provider" v-model="providerId" class="form-ctrl ai-evaluation-select">
        <option v-for="provider in providers" :key="provider.id" :value="provider.id">{{ provider.name }}</option>
      </select>
      <p v-if="!providers.length" class="form-hint">{{ t('settings.project.ai_v02.no_providers') }}</p>
      <p v-if="error" class="alert alert-danger">{{ error }}</p>
      <template v-if="providerId">
        <div class="ai-evaluation-state">
          <span>{{ t('settings.project.ai_v02.current') }}: {{ stateLabel(current) }}</span>
          <span v-if="!current && latestHistory?.is_stale" class="badge badge-yellow">{{ t('settings.project.ai_v02.stale') }}</span>
          <span v-if="latestHistory">{{ t('settings.project.ai_v02.latest') }}: {{ stateLabel(latestHistory) }}</span>
        </div>
        <p v-if="current?.diagnostic_summary" class="form-hint">{{ current.diagnostic_summary }}</p>
        <div class="ai-evaluation-actions">
          <button class="btn btn-secondary btn-sm" :disabled="busy" @click="start">{{ t(current ? 'settings.project.ai_v02.rerun' : 'settings.project.ai_v02.start') }}</button>
          <button v-if="current && ['pending', 'running'].includes(current.lifecycle)" class="btn btn-secondary btn-sm" :disabled="busy" @click="cancel">{{ t('settings.project.ai_v02.cancel') }}</button>
          <button class="btn btn-secondary btn-sm" @click="refresh">{{ t('settings.project.ai_v02.refresh') }}</button>
        </div>
        <h3>{{ t('settings.project.ai_v02.history') }}</h3>
        <ul class="ai-evaluation-history">
          <li v-for="item in history" :key="item.evaluation_id">
            <span>{{ item.created_at }}</span> · {{ stateLabel(item) }}
            <span v-if="item.is_stale" class="badge badge-yellow">{{ t('settings.project.ai_v02.stale') }}</span>
          </li>
        </ul>
        <button v-if="nextCursor" class="btn btn-secondary btn-sm" @click="loadMore">{{ t('settings.project.ai_v02.more') }}</button>
      </template>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getRequest, postRequest } from '@shared/api'

type Evaluation = { project_id: string; provider_id: string; evaluation_id: string; lifecycle: string; verdict: string | null; is_stale: boolean; created_at: string; diagnostic_summary?: string | null }
const props = defineProps<{ projectId: string; providers: { id: string; name: string }[] }>()
const emit = defineEmits<{ changed: [] }>()
const { t } = useI18n()
const providerId = ref('')
const current = ref<Evaluation | null>(null)
const latestHistory = ref<Evaluation | null>(null)
const history = ref<Evaluation[]>([])
const nextCursor = ref<string | null>(null)
const error = ref('')
const busy = ref(false)
let requestGeneration = 0
let activeRunId = ''
let timer: ReturnType<typeof setInterval> | undefined

function stateLabel(item: Evaluation | null): string {
  if (!item) return t('settings.project.ai_v02.none')
  const status = item.lifecycle === 'completed' ? (item.verdict || 'completed') : item.lifecycle
  return t(`settings.project.ai_v02.status_${status}`)
}
function apiError(e: any): string {
  return e?.response?.data?.error?.code || e?.response?.data?.code || t('settings.project.ai_v02.load_failed')
}
function isCurrent(project: string, provider: string, generation: number): boolean {
  return props.projectId === project && providerId.value === provider && generation === requestGeneration
}
async function refresh(): Promise<void> {
  const project = props.projectId
  const provider = providerId.value
  const generation = ++requestGeneration
  if (!project || !provider) return
  const base = `/api/v1/ai-evaluations`
  const query = `provider_id=${encodeURIComponent(provider)}`
  const [currentResult, historyResult] = await Promise.allSettled([
    getRequest<any>(`${base}/current?${query}`),
    getRequest<any>(`${base}?${query}&limit=20`),
  ])
  if (!isCurrent(project, provider, generation)) return
  error.value = ""
  if (currentResult.status === 'fulfilled') {
    current.value = currentResult.value.data.current
    latestHistory.value = currentResult.value.data.latest_history
    if (current.value && current.value.evaluation_id === activeRunId && !['pending', 'running'].includes(current.value.lifecycle)) {
      activeRunId = ''
      emit('changed')
    }
  } else error.value = apiError(currentResult.reason)
  if (historyResult.status === 'fulfilled') {
    history.value = historyResult.value.data.items || []
    nextCursor.value = historyResult.value.data.next_cursor
  } else error.value = apiError(historyResult.reason)
}
async function loadMore(): Promise<void> {
  const project = props.projectId, provider = providerId.value, cursor = nextCursor.value
  const generation = requestGeneration
  if (!cursor) return
  try {
    const { data } = await getRequest<any>(`/api/v1/ai-evaluations?provider_id=${encodeURIComponent(provider)}&limit=20&cursor=${encodeURIComponent(cursor)}`)
    if (!isCurrent(project, provider, generation)) return
    history.value = [...history.value, ...(data.items || [])]
    nextCursor.value = data.next_cursor
  } catch (e) { if (isCurrent(project, provider, generation)) error.value = apiError(e) }
}
async function readRun(project: string, provider: string, evaluationId: string): Promise<void> {
  try {
    const { data } = await getRequest<Evaluation>(`/api/v1/ai-evaluations/${encodeURIComponent(evaluationId)}`)
    if (props.projectId !== project || providerId.value !== provider || activeRunId !== evaluationId || data.evaluation_id !== evaluationId || data.provider_id !== provider || data.project_id !== project) return
    current.value = data
    if (!['pending', 'running'].includes(data.lifecycle)) { activeRunId = ''; await refresh(); emit('changed') }
  } catch (e) { if (props.projectId === project && providerId.value === provider && activeRunId === evaluationId) error.value = apiError(e) }
}
async function start(): Promise<void> {
  const project = props.projectId, provider = providerId.value
  busy.value = true; error.value = ''
  try {
    const { data } = await postRequest<any>(`/api/v1/ai-evaluations`, { provider_id: provider })
    if (props.projectId !== project || providerId.value !== provider) return
    activeRunId = data.evaluation_id
    current.value = data
    emit('changed')
    await refresh()
  } catch (e: any) {
    const conflict = e?.response?.data?.error
    if (e?.response?.status === 409 && conflict?.code === 'evaluation_already_running' && conflict.evaluation_id && props.projectId === project && providerId.value === provider) {
      activeRunId = conflict.evaluation_id
      await readRun(project, provider, activeRunId)
      await refresh()
    } else if (props.projectId === project && providerId.value === provider) error.value = apiError(e)
  } finally { busy.value = false }
}
async function cancel(): Promise<void> {
  if (!current.value) return
  const project = props.projectId, provider = providerId.value, evaluationId = current.value.evaluation_id
  busy.value = true
  try {
    await postRequest(`/api/v1/ai-evaluations/${encodeURIComponent(evaluationId)}/cancel`, {})
    if (props.projectId === project && providerId.value === provider) { activeRunId = ''; await refresh(); emit('changed') }
  } catch (e) { if (props.projectId === project && providerId.value === provider) error.value = apiError(e) }
  finally { busy.value = false }
}
watch(() => [props.projectId, props.providers], () => {
  if (!props.providers.some(p => p.id === providerId.value)) providerId.value = props.providers[0]?.id || ''
}, { immediate: true, deep: true })
watch(() => [props.projectId, providerId.value], () => {
  requestGeneration++
  activeRunId = ''
  current.value = null; latestHistory.value = null; history.value = []; error.value = ''
  void refresh()
}, { immediate: true })
timer = setInterval(() => {
  if (activeRunId && props.projectId && providerId.value) void readRun(props.projectId, providerId.value, activeRunId)
  else if (current.value && ['pending', 'running'].includes(current.value.lifecycle)) void refresh()
}, 3000)
onBeforeUnmount(() => { if (timer) clearInterval(timer); requestGeneration++ })
</script>

<style scoped>
.ai-evaluation-select { max-width: 360px; margin-bottom: 12px; }
.ai-evaluation-state, .ai-evaluation-actions { display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: center; margin: 10px 0; }
.ai-evaluation-history { max-height: 240px; overflow: auto; padding-left: 20px; }
</style>
