<template>
  <!-- 0234 B0001: invocation-time provider confirm/change. Presentational only —
       the parent owns the aiProvider store and wires providers/modelValue + the
       update event, so this control stays usable in Pinia-free component tests. -->
  <label class="aip-select" :class="{ 'aip-select--errored': errored, 'aip-select--compact': compact }" :title="titleText">
    <AppIcon v-if="!hideIcon" name="robot" class="aip-select-icon" />
    <span v-if="!hideLabel" class="aip-select-label">{{ t('main.ai_provider.run_label') }}</span>
    <span class="aip-select-input-wrap">
      <select
        class="aip-select-input"
        :value="modelValue"
        :aria-label="t('main.ai_provider.run_label')"
        :disabled="loading || providers.length === 0"
        @change="onChange"
      >
        <option v-if="loading" value="">{{ t('main.ai_provider.loading') }}</option>
        <option v-else-if="providers.length === 0" value="">{{ t('main.ai_provider.none') }}</option>
        <option v-for="provider in providers" :key="provider.id" :value="provider.id">
          {{ provider.name }}
        </option>
      </select>
      <AppIcon name="caret-down" class="aip-select-caret" />
    </span>
  </label>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import AppIcon from '@shared/AppIcon.vue'

interface ProviderOption {
  id: string
  name: string
}

const props = withDefaults(
  defineProps<{
    providers?: ProviderOption[]
    modelValue?: string
    loading?: boolean
    errored?: boolean
    hideLabel?: boolean
    hideIcon?: boolean
    compact?: boolean
  }>(),
  {
    providers: () => [],
    modelValue: '',
    loading: false,
    errored: false,
    hideLabel: false,
    hideIcon: false,
    compact: false,
  },
)

const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
const { t } = useI18n()

const titleText = computed(() =>
  props.errored ? t('main.ai_provider.load_failed') : t('main.ai_provider.run_label'),
)

function onChange(event: Event) {
  emit('update:modelValue', (event.target as HTMLSelectElement).value)
}
</script>

<style scoped>
.aip-select {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: .82rem;
  min-width: 0;
}
.aip-select-icon {
  color: var(--primary);
  flex-shrink: 0;
}
.aip-select-label {
  color: var(--text-m);
  white-space: nowrap;
  flex-shrink: 0;
}
.aip-select-input-wrap {
  position: relative;
  display: flex;
  flex: 1;
  min-width: 120px;
}
.aip-select-input {
  flex: 1;
  min-width: 0;
  width: 100%;
  padding: 5px 26px 5px 8px;
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  background: var(--surface);
  color: var(--text);
  font-size: .82rem;
  appearance: none;
  -webkit-appearance: none;
  -moz-appearance: none;
}
.aip-select-caret {
  position: absolute;
  right: 8px;
  top: 50%;
  transform: translateY(-50%);
  color: var(--text-m);
  font-size: .7rem;
  pointer-events: none;
}
.aip-select--errored .aip-select-input {
  border-color: var(--danger);
}
.aip-select--compact {
  gap: 0;
  font-size: .74rem;
}
.aip-select--compact .aip-select-input {
  height: 20px;
  padding: 2px 8px;
  font-size: .74rem;
  appearance: auto;
  -webkit-appearance: auto;
  -moz-appearance: auto;
}
.aip-select--compact .aip-select-caret {
  display: none;
}
</style>
