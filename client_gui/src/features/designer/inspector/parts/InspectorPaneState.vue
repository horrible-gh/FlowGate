<template>
  <!-- flowgate.v02.0048 T#1 — empty / loading / error state of an Inspector section (D#1 §2.2
       parts). Display only: the caller decides the state and the message, and owns what a retry
       does (the `retry` event). Without a message the Frame's common wording is used.
       flowgate.v02.0051 — `disabled`: the v7 item exists but its operation is not connected yet
       (0051-R §2.3: shown as an inactive state, never removed and never a working-looking button). -->
  <div class="inspector-pane-state" data-test="inspector-pane-state" :data-state="state">
    <LoadingState v-if="state === 'loading'" size="sm" :label="message ?? t('gui.designer.inspector.pane.loading')" />
    <EmptyState v-else-if="state === 'empty'" compact :title="message ?? t('gui.designer.inspector.pane.empty')" />
    <p v-else-if="state === 'disabled'" class="inspector-pane-disabled" role="note" aria-disabled="true">
      <span class="inspector-pane-disabled-mark" aria-hidden="true">⊘</span>
      <span>{{ message ?? t('gui.designer.inspector.pane.disabled') }}</span>
    </p>
    <ErrorState v-else compact :title="t('gui.designer.inspector.pane.error')" :message="message">
      <template v-if="retryable" #action>
        <Button size="sm" variant="secondary" data-test="inspector-pane-retry" @click="emit('retry')">
          {{ t('gui.designer.inspector.pane.retry') }}
        </Button>
      </template>
    </ErrorState>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import Button from '../../../../components/ui/Button.vue'
import EmptyState from '../../../../components/ui/EmptyState.vue'
import ErrorState from '../../../../components/ui/ErrorState.vue'
import LoadingState from '../../../../components/ui/LoadingState.vue'

withDefaults(
  defineProps<{
    state: 'empty' | 'loading' | 'error' | 'disabled'
    /** Shown instead of the common wording (error: shown under the common title). */
    message?: string
    /** Error only: offer a retry button. */
    retryable?: boolean
  }>(),
  { message: undefined, retryable: false },
)

const emit = defineEmits<{ retry: [] }>()

const { t } = useI18n()
</script>

<style scoped>
.inspector-pane-state {
  min-width: 0;
}

/* v7 `.empty` box, dimmed: a dashed frame with one line that says the operation is not available. */
.inspector-pane-disabled {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  margin: 0;
  padding: 12px 14px;
  border: 1px dashed #2b4562;
  border-radius: 8px;
  color: #6f87a0;
  font-size: 12px;
  text-align: center;
}

.inspector-pane-disabled-mark {
  font-size: 13px;
  opacity: 0.8;
}
</style>
