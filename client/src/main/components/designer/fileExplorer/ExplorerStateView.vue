<template>
  <div class="dfx-state" :class="`dfx-state--${kind}`" :data-test="`dfx-state-${kind}`" :role="kind === 'error' ? 'alert' : 'status'">
    <AppIcon v-if="kind === 'loading'" name="spinner" spin />
    <AppIcon v-else-if="kind === 'error'" name="warning-circle" />
    <span class="dfx-state-msg">{{ message }}</span>
    <span v-if="detail" class="dfx-state-detail">{{ detail }}</span>
    <button v-if="retryLabel" type="button" class="dfx-state-retry" data-test="dfx-state-retry" @click="$emit('retry')">
      {{ retryLabel }}
    </button>
  </div>
</template>

<script setup lang="ts">
// flowgate.v02.0024 T0003 — loading / error / empty presentation (0024-D §1 상태 UI).
// One small view with a `kind`, since the three differ only in icon, tone and retry.
import AppIcon from '@shared/AppIcon.vue'

defineProps<{ kind: 'loading' | 'error' | 'empty'; message: string; detail?: string | null; retryLabel?: string | null }>()
defineEmits<{ retry: [] }>()
</script>

<style scoped>
.dfx-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 18px 12px;
  font-size: 0.76rem;
  text-align: center;
  color: var(--text-m, rgba(255, 255, 255, 0.6));
}
.dfx-state--error { color: #fca5a5; }
.dfx-state-detail { font-size: 0.7rem; opacity: 0.85; overflow-wrap: anywhere; }
.dfx-state-retry {
  padding: 2px 10px;
  font-size: 0.72rem;
  color: inherit;
  background: transparent;
  border: 1px solid currentColor;
  border-radius: 4px;
  cursor: pointer;
}
</style>
