<template>
  <!-- flowgate.v02.0053 T#7 — a settings form whose read failed, in its place: the red ! icon (InspectorFailureIcon,
       the common '불러오지 못했습니다.' its name, `reason` its tooltip) and a ↻ retry button whose name and
       tooltip are the common '다시 시도'; no text on screen (0053 CH: the state is an icon, the explanation a
       tooltip). Display only: the caller owns what a retry does (the `retry` event). -->
  <div class="inspector-read-failure" role="alert" data-test="inspector-read-failure">
    <InspectorFailureIcon :name="t('gui.designer.inspector.pane.error')" :reason="reason" />
    <button
      type="button"
      class="inspector-read-failure-retry"
      :aria-label="t('gui.designer.inspector.pane.retry')"
      :title="t('gui.designer.inspector.pane.retry')"
      data-test="inspector-read-retry"
      @click="emit('retry')"
    >
      <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false" data-icon="retry">
        <path d="M12.6 6.2A4.9 4.9 0 1 0 12.9 9.6" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
        <path d="M13 2.9v3.6H9.4" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
      </svg>
    </button>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import InspectorFailureIcon from './InspectorFailureIcon.vue'

defineProps<{
  /** Why the read failed (the icon's tooltip). */
  reason: string
}>()

const emit = defineEmits<{ retry: [] }>()

const { t } = useI18n()
</script>

<style scoped>
.inspector-read-failure {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  padding: 6px 12px;
}

.inspector-read-failure-retry {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  padding: 0;
  border: 1px solid #2b4562;
  border-radius: 6px;
  background: transparent;
  color: #94a8bd;
  cursor: pointer;
}

.inspector-read-failure-retry:hover {
  color: #d9e5f0;
  border-color: #3d5f84;
}

.inspector-read-failure-retry:focus-visible {
  outline: 2px solid var(--fg2-focus, #4c9aff);
  outline-offset: 1px;
}

.inspector-read-failure-retry svg {
  width: 14px;
  height: 14px;
}
</style>
