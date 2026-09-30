<template>
  <ModalShell :open="open" size="lg" @close="onClose">
    <template #header="{ titleId }">
      <div class="start-head">
        <div>
          <h2 :id="titleId">{{ t('gui.ai.start.title') }}</h2>
          <p>{{ t('gui.ai.start.subtitle') }}</p>
        </div>
        <Button variant="ghost" :disabled="submitting" @click="onClose">{{ t('gui.common.close') }}</Button>
      </div>
    </template>

    <div class="start-body">
      <LoadingState v-if="loading" :label="t('gui.common.loading')" />
      <ErrorState v-else-if="loadError" :title="t('gui.ai.load_failed_title')" :message="loadError">
        <template #action><Button @click="load">{{ t('gui.common.retry') }}</Button></template>
      </ErrorState>
      <template v-else>
        <div class="start-grid">
          <label><span>{{ t('gui.ai.start.module') }}</span><Select v-model="moduleName" :options="moduleOptions" /></label>
          <label><span>{{ t('gui.ai.start.group') }}</span><Input v-model="workGroup" placeholder="0015" /></label>
          <label class="wide"><span>{{ t('gui.ai.start.doc') }}</span><Input v-model="docRef" placeholder="project.module.group.0001-R" /></label>
          <label><span>{{ t('gui.ai.start.scope') }}</span><Select v-model="actionScope" :options="scopeOptions" /></label>
          <label><span>{{ t('gui.ai.start.target') }}</span><Input v-model="targetSeqText" type="number" /></label>
          <label><span>{{ t('gui.ai.start.instruction') }}</span><Select v-model="instructionMode" :options="instructionOptions" /></label>
          <label><span>{{ t('gui.ai.start.preset') }}</span><Select v-model="selectedGroupId" :options="groupOptions" @update:model-value="applyGroup" /></label>
        </div>

        <p class="start-note">{{ t('gui.ai.start.preset_hint') }}</p>
        <div class="start-grid provider-grid">
          <label><span>{{ t('gui.ai.start.executor') }}</span><Select v-model="providerId" :options="providerOptions" /></label>
          <label><span>{{ t('gui.ai.start.reworker') }}</span><Select v-model="reworkerId" :options="optionalProviderOptions" /></label>
          <label><span>{{ t('gui.ai.start.reviewer') }}</span><Select v-model="reviewerId" :options="optionalProviderOptions" /></label>
        </div>
        <p v-if="groupError" class="start-error" role="alert">{{ groupError }}</p>
        <p v-if="submitError" class="start-error" role="alert">{{ submitError }}</p>
        <p v-if="startedRunId" class="start-success" role="status">{{ t('gui.ai.start.started') }} {{ startedRunId }}</p>
      </template>
    </div>

    <template #footer>
      <div class="start-footer">
        <Button variant="ghost" :disabled="submitting" @click="onClose">{{ t('gui.common.cancel') }}</Button>
        <Button variant="primary" :loading="submitting" :disabled="loading || !!loadError || !!startedRunId || !canSubmit" @click="submit">
          {{ t('gui.ai.start.submit') }}
        </Button>
      </div>
    </template>
  </ModalShell>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { postRequest, extractApiErrorMessage } from '@shared/api'
import Button from '@/components/ui/Button.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import Input from '@/components/ui/Input.vue'
import LoadingState from '@/components/ui/LoadingState.vue'
import ModalShell from '@/components/ui/ModalShell.vue'
import Select from '@/components/ui/Select.vue'
import { fetchProjectAiSettings, listAiGroups, listSequenceSteps } from './api'
import { buildContinuousStartBody, effectiveProviders, groupHasRuntimeBinding, groupIsAvailable, reviewerStepSeqs, selectionFromGroup } from './startPreset'
import type { AiGroup, ProviderItem } from './types'
import type { Project } from '@/features/project/types'

const props = defineProps<{ open: boolean; project: Project }>()
const emit = defineEmits<{ close: [] }>()
const { t } = useI18n()
const loading = ref(false)
const submitting = ref(false)
const loadError = ref('')
const groupError = ref('')
const submitError = ref('')
const startedRunId = ref('')
const groups = ref<AiGroup[]>([])
const providers = ref<ProviderItem[]>([])
const moduleName = ref('')
const workGroup = ref('')
const docRef = ref('')
const actionScope = ref('new')
const targetSeqText = ref('1')
const instructionMode = ref('ai_direct')
const selectedGroupId = ref('')
const providerId = ref('')
const reviewerId = ref('')
const reworkerId = ref('')
let epoch = 0

const moduleOptions = computed(() => [
  { value: '', label: t('gui.ai.start.no_module') },
  ...props.project.modules.map(module => ({ value: module.name, label: module.title || module.name })),
])
const scopeOptions = computed(() => [
  { value: 'new', label: t('gui.ai.start.scope_new') },
  { value: 'edit', label: t('gui.ai.start.scope_edit') },
  { value: 'workflow_decide', label: t('gui.ai.start.scope_decide') },
])
const instructionOptions = computed(() => [
  { value: 'ai_direct', label: t('gui.ai.start.ai_direct') },
  { value: 'auto_approved', label: t('gui.ai.start.auto_approved') },
])
const groupOptions = computed(() => [
  { value: '', label: t('gui.ai.start.no_preset') },
  ...groups.value.map(group => ({
    value: group.ai_group_id,
    label: group.name +
      (!groupIsAvailable(group) ? ` — ${t('gui.ai.start.unavailable')}`
        : groupHasRuntimeBinding(group) ? '' : ` — ${t('gui.ai.start.no_binding')}`),
    disabled: !groupIsAvailable(group),
  })),
])
const providerOptions = computed(() => [
  { value: '', label: t('gui.ai.start.choose_provider') },
  ...providers.value.map(provider => ({ value: provider.id, label: provider.name })),
])
const optionalProviderOptions = computed(() => [
  { value: '', label: t('gui.ai.start.none') },
  ...providers.value.map(provider => ({ value: provider.id, label: provider.name })),
])
const targetSeq = computed(() => Number(targetSeqText.value))
const canSubmit = computed(() => {
  const ids = new Set(providers.value.map(provider => provider.id))
  return !!workGroup.value.trim() && !!docRef.value.trim() && ids.has(providerId.value) &&
    (!reviewerId.value || ids.has(reviewerId.value)) && (!reworkerId.value || ids.has(reworkerId.value)) &&
    Number.isInteger(targetSeq.value) && (actionScope.value === 'workflow_decide' ? targetSeq.value === -1 : targetSeq.value >= 0)
})

function onClose(): void {
  if (!submitting.value) emit('close')
}

function applyGroup(groupId: string): void {
  groupError.value = ''
  if (!groupId) return
  const group = groups.value.find(item => item.ai_group_id === groupId)
  if (!group || !groupIsAvailable(group)) {
    selectedGroupId.value = ''
    groupError.value = t('gui.ai.start.unavailable')
    return
  }
  const selection = selectionFromGroup(group, {
    providerId: providerId.value,
    reviewerId: reviewerId.value,
    reworkerId: reworkerId.value,
  })
  providerId.value = selection.providerId
  reviewerId.value = selection.reviewerId
  reworkerId.value = selection.reworkerId
}

async function load(): Promise<void> {
  const current = ++epoch
  loading.value = true
  loadError.value = ''
  try {
    const [settings, listed] = await Promise.all([
      fetchProjectAiSettings(props.project.id),
      listAiGroups(props.project.id),
    ])
    if (current !== epoch || !props.open) return
    providers.value = effectiveProviders(settings.effective.providers)
    // An OFF team is not offered; the default team is pre-selected.
    groups.value = listed.filter(group => group.enabled !== false)
    providerId.value = settings.effective.default_provider_id ?? providers.value[0]?.id ?? ''
    const preset = groups.value.find(group => group.is_default)
    if (preset && groupIsAvailable(preset)) {
      selectedGroupId.value = preset.ai_group_id
      applyGroup(preset.ai_group_id)
    }
  } catch (error) {
    if (current === epoch && props.open) loadError.value = error instanceof Error ? error.message : t('gui.ai.load_failed_title')
  } finally {
    if (current === epoch) loading.value = false
  }
}

watch(() => [props.open, props.project.id] as const, ([open]) => {
  ++epoch
  submitting.value = false
  if (!open) return
  groups.value = []
  providers.value = []
  selectedGroupId.value = ''
  providerId.value = ''
  reviewerId.value = ''
  reworkerId.value = ''
  groupError.value = ''
  submitError.value = ''
  startedRunId.value = ''
  moduleName.value = props.project.modules[0]?.name ?? ''
  workGroup.value = ''
  docRef.value = ''
  actionScope.value = 'new'
  targetSeqText.value = '1'
  instructionMode.value = 'ai_direct'
  void load()
}, { immediate: true })

watch(actionScope, scope => { if (scope === 'workflow_decide') targetSeqText.value = '-1' })

async function submit(): Promise<void> {
  if (!canSubmit.value || submitting.value) return
  const current = epoch
  submitError.value = ''
  submitting.value = true
  try {
    const scope = actionScope.value as 'new' | 'edit' | 'workflow_decide'
    const mode = instructionMode.value as 'ai_direct' | 'auto_approved'
    let stepSeqs: number[] = []
    if (reviewerId.value && scope !== 'workflow_decide') {
      stepSeqs = reviewerStepSeqs(await listSequenceSteps(docRef.value.trim()), targetSeq.value, mode)
      if (current !== epoch || !props.open) return
      if (!stepSeqs.length) {
        submitError.value = t('gui.ai.start.reviewer_steps_missing')
        return
      }
    }
    const body = buildContinuousStartBody({
      project: props.project.id,
      module: moduleName.value,
      group: workGroup.value.trim(),
      docRef: docRef.value.trim(),
      actionScope: scope,
      targetSeq: targetSeq.value,
      instructionMode: mode,
      requestedAiGroupId: selectedGroupId.value,
      providerId: providerId.value,
      reviewerId: reviewerId.value,
      reworkerId: reworkerId.value,
      reviewerStepSeqs: stepSeqs,
    })
    const response = await postRequest<{ run_id?: string }>('/api/v1/ai-invoke/start', body)
    if (current === epoch && props.open) startedRunId.value = response.data.run_id ?? t('gui.ai.start.started')
  } catch (error) {
    if (current === epoch && props.open) submitError.value = extractApiErrorMessage(error, t('gui.ai.start.start_failed'))
  } finally {
    if (current === epoch) submitting.value = false
  }
}
</script>

<style scoped>
.start-head, .start-footer { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 20px 24px; }
.start-head h2 { margin: 0; font-size: 21px; font-weight: 650; color: var(--fg2-text); }
.start-head p, .start-note { margin: 6px 0 0; color: var(--fg2-muted); font-size: 14px; line-height: 1.5; }
.start-body { padding: 12px 24px 20px; max-height: min(70vh, 800px); overflow: auto; }
.start-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px 20px; }
.start-grid label { display: grid; gap: 7px; color: var(--fg2-control-fg); font-size: 14px; }
.start-grid .wide { grid-column: 1 / -1; }
.provider-grid { border-top: 1px solid var(--fg2-line-strong); margin-top: 18px; padding-top: 18px; }
.start-error { color: #ff9aab; } .start-success { color: #72d6b6; }
@media (max-width: 640px) { .start-grid { grid-template-columns: 1fr; } }
</style>
