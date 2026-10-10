<template>
  <!-- flowgate.v02.0048 T#1 — empty / loading / error state of an Inspector section (D#1 §2.2
       parts). Display only: the caller decides the state and the message, and owns what a retry
       does (the `retry` event). Without a message the Frame's common wording is used. -->
  <div class="inspector-pane-state" data-test="inspector-pane-state" :data-state="state">
    <LoadingState v-if="state === 'loading'" size="sm" :label="message ?? t('gui.designer.inspector.pane.loading')" />
    <EmptyState v-else-if="state === 'empty'" compact :title="message ?? t('gui.designer.inspector.pane.empty')" />
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
    state: 'empty' | 'loading' | 'error'
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
</style>
