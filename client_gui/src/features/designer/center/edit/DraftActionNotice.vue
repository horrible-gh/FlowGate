<template>
  <p v-if="notice?.kind === 'error'" class="draft-action-notice" role="alert"
    data-test="designer-draft-notice" :data-code="notice.code">
    {{ t('gui.designer.center.draft_action_failed', { code: notice.code }) }}
  </p>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { DraftEditState } from './draftController'

// 0156 T6 / Q589: a refused confirmation stores a notice before rereading the
// graph. Keep that reason visible through the reread, until the next edit/action.
defineProps<{ notice: DraftEditState['notice'] }>()
const { t } = useI18n()
</script>

<style scoped>
.draft-action-notice {
  position: absolute;
  top: 12px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 10;
  max-width: calc(100% - 24px);
  margin: 0;
  padding: 8px 12px;
  border: 1px solid var(--fg2-line);
  border-radius: 6px;
  background: var(--fg2-bg);
  color: var(--fg2-danger, #c43b3b);
}
</style>
