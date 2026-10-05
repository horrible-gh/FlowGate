<template>
  <!-- flowgate.default.0668 T0004 (R1) — the conflict card is the single entry point:
       [Provider ▾][AI로 해결] [직접 해결]. The resolver is no longer the step in front of
       the AI call; it is what [직접 해결] (and detail viewing) opens. While the run works the
       card says so in place, and the operator is free to leave — the run is the server's. -->
  <div class="git-conflict-card-actions" data-test="conflict-card-actions">
    <AiProviderSelect
      class="git-conflict-card-provider"
      :providers="providers || []"
      :model-value="selectedProvider"
      :loading="providerLoading"
      :errored="providerErrored"
      :disabled="busy || running"
      compact
      hide-label
      @update:model-value="(v) => emit('update:provider', v)"
    />
    <button
      class="btn btn-sm btn-primary git-conflict-card-ai"
      type="button"
      data-test="conflict-card-ai"
      :disabled="busy || starting || running"
      @click="emit('ai-resolve')"
    >
      <AppIcon name="magic-wand" />{{ t('main.git_status.ai_resolve') }}
    </button>
    <button
      v-if="!hideDirect"
      class="btn btn-sm btn-secondary git-conflict-card-direct"
      type="button"
      data-test="conflict-card-direct"
      :disabled="busy"
      @click="emit('direct-resolve')"
    >
      <AppIcon name="pencil-simple" />{{ t('main.git_status.direct_resolve') }}
    </button>
    <span
      v-if="running || starting"
      class="git-conflict-card-run"
      role="status"
      data-test="conflict-card-run"
    >
      <AppIcon name="spinner" spin />
      {{ runningNotice || t('main.git_status.ai_resolve_starting') }}
    </span>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import AppIcon from '@shared/AppIcon.vue'
import AiProviderSelect from './AiProviderSelect.vue'

const props = defineProps<{
  providers?: { id: string; name: string }[]
  selectedProvider?: string
  providerLoading?: boolean
  providerErrored?: boolean
  busy?: boolean
  /** The start request is in flight (no run entry in this browser yet). */
  starting?: boolean
  /** The group's own run line while it works; null/absent when nothing runs. */
  runningNotice?: string | null
  /** Opt-out: a host whose card has its own direct-resolve button. */
  hideDirect?: boolean
}>()

const emit = defineEmits<{
  'ai-resolve': []
  'direct-resolve': []
  'update:provider': [value: string]
}>()

const { t } = useI18n()
const running = computed(() => !!props.runningNotice)
</script>

<style scoped>
.git-conflict-card-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}
.git-conflict-card-provider {
  min-width: 0;
  max-width: 180px;
}
.git-conflict-card-run {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 0.74rem;
  color: var(--text-muted, #64748b);
}
</style>
