<template>
  <span class="ai-role-select">
    <Select :model-value="selected" :options="options" :placeholder="t('gui.aiRoles.unset')" :disabled="disabled" :aria-label="label" @update:model-value="choose" />
    <span v-if="unavailable && !plain" role="img" :aria-label="t('gui.inspector.settings.option.missing', { name: displayName || providerId })" :title="cause || state || ''">⚠</span>
  </span>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import Select from '@/components/ui/Select.vue'
import { installAiRoleMessages } from './messages'
import type { AiRoleValue, AiRoleOption } from './types'
const props = defineProps<{ modelValue: AiRoleValue | null; providers: readonly AiRoleOption[]; label: string; displayName?: string | null; state?: string; cause?: string | null; disabled?: boolean; plain?: boolean }>()
// flowgate.v02.0113 T#4: `plain` (the Inspector AI tab) draws the select only — no ⚠ beside an unavailable AI.
const emit = defineEmits<{ 'update:modelValue': [value: AiRoleValue] }>()
const composer = useI18n({ useScope: 'global' }); installAiRoleMessages(composer)
const { t } = composer
// UI tokens are never sent to the API and cannot collide with an AI identifier.
const providerId = computed(() => props.modelValue?.kind === 'assigned' ? props.modelValue.providerId : null)
const selected = computed(() => props.modelValue?.kind === 'not_used' ? 'not_used' : providerId.value === null ? '' : `assigned:${providerId.value}`)
const unavailable = computed(() => props.state === 'assigned_unavailable' || (providerId.value !== null && !props.providers.some(item => item.id === providerId.value)))
const options = computed(() => {
  const out = [{ value: 'not_used', label: t('gui.aiRoles.not_used') }, ...props.providers.map(item => ({ value: `assigned:${item.id}`, label: item.name }))]
  if (providerId.value && !props.providers.some(item => item.id === providerId.value)) out.push({ value: `assigned:${providerId.value}`, label: t('gui.inspector.settings.option.missing', { name: props.displayName || providerId.value }) })
  return out
})
function choose(value: string) {
  if (props.disabled || !value) return
  emit('update:modelValue', value === 'not_used' ? { kind: 'not_used' } : { kind: 'assigned', providerId: value.slice('assigned:'.length) })
}
</script>
<style scoped>
.ai-role-select { display: flex; align-items: center; gap: 6px; min-width: 0; width: 100%; }
.ai-role-select > select { flex: 1; }
</style>
