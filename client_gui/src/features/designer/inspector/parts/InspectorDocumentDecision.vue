<template>
  <span class="document-decision" :title="label">
    <span class="document-decision-divider" role="separator" aria-orientation="vertical" />
    <IconButton :label="label" :title="label" :disabled="disabled" variant="secondary" size="sm"
      class="document-decision-button" :class="action" :data-action="action" data-test="inspector-document-decision"
      @click="emit('click')">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" aria-hidden="true" focusable="false">
        <path v-if="action === 'reject'" d="M6 6l12 12M18 6 6 18" />
        <path v-else d="m5 12 5 5L20 7" />
      </svg>
    </IconButton>
  </span>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import IconButton from '@/components/ui/IconButton.vue'
import { documentDecisionMessages } from './documentDecisionMessages'
const props = defineProps<{ name: string; action: 'approve' | 'reject'; disabled: boolean; reason: string | null }>()
const emit = defineEmits<{ click: [] }>()
const { t, te } = useI18n({ useScope: 'local', messages: documentDecisionMessages })
const label = computed(() => {
  const action = `${props.name} ${t(`decision.${props.action}`)}`
  if (!props.reason) return action
  const key = `decision.${props.reason}`
  return `${action} · ${te(key) ? t(key) : t('decision.blocked') + ' (' + props.reason + ')'}`
})
</script>
<style scoped>
.document-decision { display: inline-flex; align-items: center; gap: 6px; flex: none; }
.document-decision-divider { width: 1px; height: 20px; flex: 0 0 1px; margin: 0 3px; background: #34465b; opacity: .9; }
.document-decision-button.approve { color: #66d5b3; border-color: #2a655e; }
.document-decision-button.reject { color: #e8a1a1; border-color: #6a424b; }
@media (max-width: 520px) {
  .document-decision { gap: 4px; }
  .document-decision-divider { height: 18px; margin: 0 2px; }
  .document-decision-button { width: 26px; height: 26px; }
}
</style>
