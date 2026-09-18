<template>
  <DialogShell :open="visible" variant="alert" surface-class="dialog-continuous-warning-dialog"  @request-close="close">
    <template #header>
      <DialogHeader title="" :closeable="true" @close="close">
        <template #title>
            <AppIcon name="warning" style="color:var(--danger); margin-right:6px;" />
            {{ t('main.continuous_work.warn_title') }}
          </template>
      </DialogHeader>
    </template>

        

        <div class="dialog-feature-body cwarn-body">
          <div class="cwarn-summary">
            <AppIcon name="fast-forward" />
            {{ summaryText }}
          </div>
          <div class="cwarn-summary cwarn-summary--mode">
            <AppIcon name="gear" />
            {{ instructionModeText }}
          </div>
          <div v-if="autoApproveCountText" class="cwarn-summary cwarn-summary--auto-approve">
            <AppIcon name="seal-check" />
            {{ autoApproveCountText }}
          </div>

          <ul class="cwarn-list">
            <li>{{ t('main.continuous_work.warn_unmanned') }}</li>
            <li>{{ t('main.continuous_work.warn_responsibility') }}</li>
            <li>{{ t('main.continuous_work.warn_quality') }}</li>
          </ul>

          <label class="cwarn-provider">
            <span>{{ t('main.continuous_work.provider_label') }}</span>
            <select
              class="form-ctrl"
              :value="aiProviderStore.selectedProviderId"
              :disabled="aiProviderStore.loading"
              @change="onProviderChange"
            >
              <option v-if="aiProviderStore.loading" value="">
                {{ t('main.continuous_work.provider_loading') }}
              </option>
              <option v-for="provider in aiProviderStore.providers" :key="provider.id" :value="provider.id">
                {{ provider.name }}
              </option>
            </select>
            <small v-if="!aiProviderStore.loading && aiProviderStore.providers.length === 0">
              {{ t('main.continuous_work.provider_unavailable') }}
            </small>
          </label>

          <label class="cwarn-consent">
            <input v-model="consented" type="checkbox" />
            <span>{{ t('main.continuous_work.warn_consent') }}</span>
          </label>
        </div>

        
      

    <template #footer>
      <DialogFooter :actions="[
        { id: 'close-0', role: 'cancel', label: t('common.cancel'), onSelect: () => close() },
        { id: 'onAction-1', role: 'aux', label: t('main.continuous_work.warn_btn_copy'), onSelect: () => { onAction('copy-mention') }, disabled: !consented },
        { id: 'onAction-2', role: 'aux', label: t('main.continuous_work.warn_btn_copy_message'), onSelect: () => { onAction('copy-with-message') }, disabled: !consented },
        { id: 'onAction-3', role: 'primary', label: t('main.continuous_work.warn_btn_start'), onSelect: () => { onAction('confirm') }, disabled: !consented || aiProviderStore.loading || !aiProviderStore.selectedProviderId, tone: 'danger' }
      ]">
        <template #action-close-0>{{ t('common.cancel') }}</template>
        <template #action-onAction-1>
            <AppIcon name="copy" /> {{ t('main.continuous_work.warn_btn_copy') }}
          </template>
        <template #action-onAction-2>
            <AppIcon name="copy" /> {{ t('main.continuous_work.warn_btn_copy_message') }}
          </template>
        <template #action-onAction-3>
            <AppIcon name="lightning" /> {{ t('main.continuous_work.warn_btn_start') }}
          </template>
      </DialogFooter>
    </template>
  </DialogShell>
</template>

<script setup lang="ts">
import DialogShell from './dialogs/DialogShell.vue'
import DialogHeader from './dialogs/DialogHeader.vue'
import DialogFooter from './dialogs/DialogFooter.vue'
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import AppIcon from '@shared/AppIcon.vue'
import { useAiProviderStore } from '../stores/aiProvider'

const props = defineProps<{
  visible: boolean
  project: string
  stepCount: number
  targetLabel: string
  reviewMode: boolean
  instructionMode?: 'auto_approved' | 'ai_direct'
  // R0001 "워크플로 결정부터": the run starts from the workflow decision; there is no step
  // count to quote, so the summary uses a dedicated decision-first line.
  fromDecision?: boolean
  // 0352 T0004 §3.7: how many N/T steps the ai_direct chain picked for server auto-handling
  // — shown so the consent screen states the full picture, not just the mode name.
  autoApproveCount?: number
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
  'confirm': []
  'copy-mention': []
  'copy-with-message': []
}>()

const { t } = useI18n()
const aiProviderStore = useAiProviderStore()
const consented = ref(false)

const summaryText = computed(() => {
  if (props.fromDecision) {
    return props.reviewMode
      ? t('main.continuous_work.warn_summary_from_decision_review')
      : t('main.continuous_work.warn_summary_from_decision')
  }
  return props.reviewMode
    ? t('main.continuous_work.warn_summary_review', { count: props.stepCount, target: props.targetLabel })
    : t('main.continuous_work.warn_summary', { count: props.stepCount, target: props.targetLabel })
})
const instructionModeText = computed(() =>
  props.instructionMode === 'ai_direct'
    ? t('main.continuous_work.warn_instruction_mode_ai')
    : t('main.continuous_work.warn_instruction_mode_auto'),
)
// 0352 T0004 §3.7: state the auto-handled count on the consent screen too — only meaningful
// under ai_direct with a non-empty selection (auto_approved already says "every N/T" via
// instructionModeText above).
const autoApproveCountText = computed(() => {
  const count = props.autoApproveCount ?? 0
  if (props.instructionMode !== 'ai_direct' || count <= 0) return ''
  return t('main.continuous_work.warn_auto_approve_count', { count })
})

function onProviderChange(event: Event) {
  aiProviderStore.selectProvider((event.target as HTMLSelectElement).value)
}

function onAction(action: 'confirm' | 'copy-mention' | 'copy-with-message') {
  if (!consented.value) return
  if (action === 'confirm') emit('confirm')
  else if (action === 'copy-mention') emit('copy-mention')
  else emit('copy-with-message')
  emit('update:visible', false)
}

function close() {
  emit('update:visible', false)
}

watch(
  () => props.visible,
  (val) => {
    if (!val) return
    consented.value = false
    if (props.project) void aiProviderStore.ensureLoaded(props.project)
  },
  { immediate: true },
)
</script>

<style scoped>

.cwarn-body {
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.cwarn-summary {
  font-size: .85rem;
  font-weight: 600;
  color: var(--text);
  background: var(--surface-h);
  border-radius: var(--r-sm);
  padding: 8px 10px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.cwarn-summary--mode {
  background: var(--surface);
  border: 1px solid var(--border);
}
.cwarn-list {
  margin: 0;
  padding-left: 18px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.cwarn-list li {
  font-size: .82rem;
  color: var(--text-s);
  line-height: 1.5;
}
.cwarn-provider {
  display: grid;
  grid-template-columns: minmax(110px, auto) minmax(0, 1fr);
  align-items: center;
  gap: 7px 12px;
  color: var(--text-s);
  font-size: .82rem;
  font-weight: 600;
}
.cwarn-provider small {
  grid-column: 2;
  color: var(--danger);
  font-size: .72rem;
  font-weight: 400;
}
.cwarn-consent {
  display: flex;
  align-items: flex-start;
  gap: 9px;
  padding: 10px 12px;
  border: 1px solid var(--danger);
  border-radius: var(--r);
  background: #fef2f2;
  cursor: pointer;
}
.cwarn-consent input { margin-top: 2px; flex-shrink: 0; }
.cwarn-consent span {
  font-size: .82rem;
  font-weight: 600;
  color: #991b1b;
  line-height: 1.45;
}



:global(.fg-dialog-surface.dialog-continuous-warning-dialog) { width: 560px; max-width: 96vw; }
</style>