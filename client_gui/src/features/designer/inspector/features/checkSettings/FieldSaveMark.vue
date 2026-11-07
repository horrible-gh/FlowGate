<template>
  <!-- flowgate.v02.0148 T#2: one field's own save state beside its control - "saving" while its value is queued / on its way,
       "saved" only after the server's answer carried the requested value, and a retry for a refused one (the refusal itself
       is the failure icon). Text and button only; nothing here locks another control. -->
  <span v-if="state === 'saving' || state === 'saved'" class="inspector-field-state" :data-field-state="state" :data-mark-field="field" role="status">{{ state === 'saving' ? saving : saved }}</span>
  <button v-else-if="state === 'failed'" type="button" class="inspector-field-retry" data-test="check-settings-retry" :data-mark-field="field" :disabled="disabled" @click="emit('retry')">{{ retry }}</button>
</template>

<script setup lang="ts">
import type { FieldSaveState } from './checkSettingsModel'

defineProps<{
  field: string
  state?: FieldSaveState
  saving: string
  saved: string
  retry: string
  disabled?: boolean
}>()
const emit = defineEmits<{ (e: 'retry'): void }>()
</script>

<style scoped>
.inspector-field-state { flex: none; color: #94a8bd; font-size: 11.5px; white-space: nowrap; }
.inspector-field-state[data-field-state='saved'] { color: #6fcf97; }
.inspector-field-retry { flex: none; padding: 0 6px; height: 20px; border: 1px solid rgba(116, 157, 192, 0.35); border-radius: 4px; background: transparent; color: #a9c2db; font-size: 11.5px; cursor: pointer; }
.inspector-field-retry:disabled { opacity: .5; cursor: default; }
.inspector-field-retry:focus-visible { outline: 2px solid #7cb7ff; outline-offset: 2px; }
</style>
