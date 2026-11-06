<template>
  <p v-if="isNonLatestRevision(graph)" class="revision-notice" role="status" data-test="designer-revision-notice">
    {{ t(graph.run?.revisionNo === graph.viewRevisionNo
      ? 'gui.designer.center.pinned_revision_notice' : 'gui.designer.center.past_revision_notice',
      { revision: graph.viewRevisionNo, latest: graph.latestRevisionNo }) }}
  </p>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { FlowGraph } from './graph/flowGraph'
import { isNonLatestRevision } from './graph/revisionView'

defineProps<{ graph: FlowGraph }>()
const { t } = useI18n()
</script>

<style scoped>
.revision-notice {
  order: -1;
  flex: 0 0 auto;
  margin: 0;
  padding: 8px 12px;
  border-bottom: 1px solid var(--fg2-line);
  background: var(--fg2-bg);
  color: var(--fg2-text);
  font-size: 12px;
}
</style>
