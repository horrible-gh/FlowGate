<template>
  <div class="dfx-scope" data-test="dfx-scope">
    <div class="dfx-scope-main">
      <span class="dfx-scope-dot" aria-hidden="true" />
      <span class="dfx-scope-name" data-test="dfx-scope-name">{{ t('gui.designer.file_explorer.scope_flow', { name: flow.name }) }}</span>
    </div>
    <div class="dfx-scope-meta">
      <span v-if="groupLabel" class="dfx-scope-group" data-test="dfx-scope-group">{{ groupLabel }}</span>
      <span v-if="flow.branch" class="dfx-scope-branch" data-test="dfx-scope-branch">{{ flow.branch }}</span>
      <Badge v-if="busy" tone="warning" size="sm" data-test="dfx-scope-busy">{{ t('gui.designer.file_explorer.busy') }}</Badge>
      <Badge v-else-if="readonly" tone="info" size="sm" data-test="dfx-scope-readonly">{{ t('gui.designer.file_explorer.readonly') }}</Badge>
    </div>
  </div>
</template>

<script setup lang="ts">
// flowgate.v02.0024 T0005 — which file scope is on screen (0024-D §4). The scope is the
// active Flow; Group is only the parent context, and read-only / AI-busy state is shown
// here so "why can't I change anything" has a visible answer.
import { useI18n } from 'vue-i18n'
import type { FlowRef } from '@/features/project'
import Badge from '../../../components/ui/Badge.vue'

defineProps<{ flow: FlowRef; groupLabel?: string | null; readonly: boolean; busy: boolean }>()
const { t } = useI18n()
</script>

<style scoped>
.dfx-scope {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 4px 12px 8px;
  border-bottom: 1px solid var(--fg2-line);
}

.dfx-scope-main {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.dfx-scope-dot {
  flex: none;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--fg2-cyan);
}

.dfx-scope-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12.5px;
  font-weight: 650;
  color: var(--fg2-text);
}

.dfx-scope-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  font-size: 11.5px;
  color: var(--fg2-muted);
}

.dfx-scope-branch {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  color: var(--fg2-muted-2);
}
</style>
