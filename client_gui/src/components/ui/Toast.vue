<template>
  <div class="ui-toast" :class="`ui-toast--${tone}`" :role="toastRole" :aria-live="ariaLive">
    <span v-if="$slots.icon" class="ui-toast-icon" aria-hidden="true"><slot name="icon" /></span>
    <span class="ui-toast-content"><slot /></span>
    <IconButton class="ui-toast-close" variant="ghost" size="sm" :label="dismissLabel" @click="emit('dismiss')">×</IconButton>
  </div>
</template>

<script setup lang="ts">
// Domain-free transient feedback (T0008 §5). Owns render/tone/lifecycle only -- never the
// wording (the message is caller content via the default slot) and never *when* to show
// one (that is entirely the calling Feature's decision, T0008 §5.1). Never a Modal: no
// focus trap, no backdrop, no background interaction block, and it never steals focus
// itself (T0008 §5.3) -- only a timer is armed below, nothing calls .focus().
import { computed, onBeforeUnmount, onMounted } from 'vue'
import IconButton from './IconButton.vue'

export type ToastTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger'

const props = withDefaults(
  defineProps<{
    tone?: ToastTone
    /** Auto-dismiss delay in ms. 0 or null disables auto-dismiss (manual close only). */
    duration?: number | null
    dismissLabel?: string
  }>(),
  { tone: 'neutral', duration: 4000, dismissLabel: 'Dismiss' },
)

const emit = defineEmits<{ dismiss: [] }>()

// A strong/danger toast is announced immediately (role="alert", assertive live region);
// every other tone waits for a pause in speech (role="status", polite) -- T0008 §5.3's
// "정보성 status / 강한 오류 alert" split, mirrored on aria-live so screen readers get the
// same urgency signal through two independent AT mechanisms.
const toastRole = computed(() => (props.tone === 'danger' ? 'alert' : 'status'))
const ariaLive = computed(() => (props.tone === 'danger' ? 'assertive' : 'polite'))

let timer: ReturnType<typeof setTimeout> | null = null

onMounted(() => {
  if (props.duration) timer = setTimeout(() => emit('dismiss'), props.duration)
})

onBeforeUnmount(() => {
  if (timer !== null) clearTimeout(timer)
})
</script>

<style scoped>
.ui-toast {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 240px;
  max-width: 360px;
  padding: 10px 12px;
  border-radius: var(--fg2-control-radius);
  border: 1px solid var(--fg2-line-strong);
  border-left-width: 3px;
  /* Opaque: a toast floats over arbitrary content (header, tables), so nothing may show through. */
  background: var(--fg2-panel);
  box-shadow: 0 12px 32px rgba(2, 6, 12, 0.6);
  color: var(--fg2-text);
  font-size: 13px;
  line-height: 1.5;
}

.ui-toast-icon {
  display: inline-flex;
  flex: none;
}

.ui-toast-content {
  flex: 1;
  min-width: 0;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}

.ui-toast-close {
  flex: none;
}

.ui-toast--neutral {
  border-left-color: var(--fg2-line-strong);
}

.ui-toast--info {
  border-left-color: var(--fg2-status-info-border);
  /* The tone tint sits on the opaque panel colour instead of replacing it. */
  background: linear-gradient(var(--fg2-status-info-bg), var(--fg2-status-info-bg)), var(--fg2-panel);
  color: var(--fg2-status-info-fg);
}

.ui-toast--success {
  border-left-color: var(--fg2-status-success-border);
  /* The tone tint sits on the opaque panel colour instead of replacing it. */
  background: linear-gradient(var(--fg2-status-success-bg), var(--fg2-status-success-bg)), var(--fg2-panel);
  color: var(--fg2-status-success-fg);
}

.ui-toast--warning {
  border-left-color: var(--fg2-status-warning-border);
  /* The tone tint sits on the opaque panel colour instead of replacing it. */
  background: linear-gradient(var(--fg2-status-warning-bg), var(--fg2-status-warning-bg)), var(--fg2-panel);
  color: var(--fg2-status-warning-fg);
}

.ui-toast--danger {
  border-left-color: var(--fg2-status-danger-border);
  /* The tone tint sits on the opaque panel colour instead of replacing it. */
  background: linear-gradient(var(--fg2-status-danger-bg), var(--fg2-status-danger-bg)), var(--fg2-panel);
  color: var(--fg2-status-danger-fg);
}
</style>
