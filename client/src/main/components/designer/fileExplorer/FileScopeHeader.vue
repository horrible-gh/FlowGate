<template>
  <div class="dfx-scope" data-test="dfx-scope">
    <div class="dfx-scope-main">
      <AppIcon name="flow-arrow" class="dfx-scope-ico" />
      <span class="dfx-scope-name" data-test="dfx-scope-name">{{ t('main.designer_explorer.scope_flow', { name: flow.name }) }}</span>
    </div>
    <div class="dfx-scope-meta">
      <span v-if="groupLabel" class="dfx-scope-group">{{ groupLabel }}</span>
      <span v-if="flow.branch" class="dfx-scope-branch"><AppIcon name="git-branch" /> {{ flow.branch }}</span>
      <span v-if="busy" class="dfx-scope-flag dfx-scope-flag--busy" data-test="dfx-scope-busy">
        <AppIcon name="lock" /> {{ t('main.designer_explorer.busy') }}
      </span>
      <span v-else-if="readonly" class="dfx-scope-flag" data-test="dfx-scope-readonly">
        <AppIcon name="eye" /> {{ t('main.designer_explorer.readonly') }}
      </span>
    </div>
  </div>
</template>

<script setup lang="ts">
// flowgate.v02.0024 T0003 — which file scope is on screen (0024-D §4). The scope is the
// active Flow; Group is only the parent context, and read-only / AI-busy state is shown
// here so "why can't I change anything" has a visible answer.
import { useI18n } from 'vue-i18n'
import AppIcon from '@shared/AppIcon.vue'
import type { DesignerFlowScope } from './fileExplorerModel'

defineProps<{ flow: DesignerFlowScope; groupLabel?: string | null; readonly: boolean; busy: boolean }>()
const { t } = useI18n()
</script>

<style scoped>
.dfx-scope {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 4px 8px 6px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}
.dfx-scope-main { display: flex; align-items: center; gap: 6px; min-width: 0; }
.dfx-scope-ico { color: #93c5fd; }
.dfx-scope-name { font-size: 0.78rem; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dfx-scope-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; font-size: 0.7rem; color: var(--text-m, rgba(255, 255, 255, 0.6)); }
.dfx-scope-branch { display: inline-flex; align-items: center; gap: 2px; }
.dfx-scope-flag { display: inline-flex; align-items: center; gap: 3px; color: #93c5fd; }
.dfx-scope-flag--busy { color: #fcd34d; }
</style>
