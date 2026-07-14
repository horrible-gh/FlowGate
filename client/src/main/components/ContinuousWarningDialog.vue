<template>
  <teleport to="body">
    <div v-if="visible" class="modal-bg" @click.self="close">
      <div class="modal-box modal-cwarn">
        <div class="modal-hd">
          <span class="modal-title">
            <AppIcon name="warning" style="color:var(--danger); margin-right:6px;" />
            {{ t('main.continuous_work.warn_title') }}
          </span>
          <button class="modal-close" type="button" @click="close">
            <AppIcon name="x" />
          </button>
        </div>

        <div class="modal-bd cwarn-body">
          <div class="cwarn-summary">
            <AppIcon name="fast-forward" />
            {{ summaryText }}
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

        <div class="modal-ft cwarn-footer">
          <button type="button" class="btn btn-ghost" @click="close">{{ t('common.cancel') }}</button>
          <button
            type="button"
            class="btn btn-secondary"
            :disabled="!consented"
            @click="onAction('copy-mention')"
          >
            <AppIcon name="copy" /> {{ t('main.continuous_work.warn_btn_copy') }}
          </button>
          <button
            type="button"
            class="btn btn-secondary"
            :disabled="!consented"
            @click="onAction('copy-with-message')"
          >
            <AppIcon name="copy" /> {{ t('main.continuous_work.warn_btn_copy_message') }}
          </button>
          <button
            type="button"
            class="btn btn-danger"
            :disabled="!consented || aiProviderStore.loading || !aiProviderStore.selectedProviderId"
            @click="onAction('confirm')"
          >
            <AppIcon name="lightning" /> {{ t('main.continuous_work.warn_btn_start') }}
          </button>
        </div>
      </div>
    </div>
  </teleport>
</template>

<script setup lang="ts">
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
  fromDecision?: boolean
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
.modal-cwarn {
  width: 560px;
  max-width: 96vw;
}
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
.cwarn-footer {
  flex-wrap: wrap;
}
@media (max-width: 600px) {
  .cwarn-footer .btn { flex: 1 1 calc(50% - 6px); }
}
</style>