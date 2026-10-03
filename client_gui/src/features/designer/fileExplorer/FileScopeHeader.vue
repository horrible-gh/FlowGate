<template>
  <div v-if="groupLabel || branch || busy || readonly" class="dfx-scope" data-test="dfx-scope">
    <span v-if="groupLabel" class="dfx-scope-group" data-test="dfx-scope-group">{{ groupLabel }}</span>
    <span v-if="branch" class="dfx-scope-branch" data-test="dfx-scope-branch">{{ branch }}</span>
    <Badge v-if="busy" tone="warning" size="sm" data-test="dfx-scope-busy">{{ t('gui.designer.file_explorer.busy') }}</Badge>
    <Badge v-else-if="readonly" tone="info" size="sm" data-test="dfx-scope-readonly">{{ t('gui.designer.file_explorer.readonly') }}</Badge>
  </div>
</template>

<script setup lang="ts">
// flowgate.v02.0024 T0005 — scope context of the file tree (0024-D §4): the optional Group
// label / branch and the read-only / AI-busy state, so "why can't I change anything" has a
// visible answer. 0025 T0006 §6: the active Flow name is not repeated here (the Project
// tree already marks it), and nothing renders when there is nothing to tell.
import { useI18n } from 'vue-i18n'
import Badge from '../../../components/ui/Badge.vue'

defineProps<{ groupLabel?: string | null; branch?: string | null; readonly: boolean; busy: boolean }>()
const { t } = useI18n()
</script>

<style scoped>
.dfx-scope {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  padding: 0 12px 8px;
  font-size: 11.5px;
  color: var(--fg2-muted);
}

.dfx-scope-branch {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  color: var(--fg2-muted-2);
}
</style>
