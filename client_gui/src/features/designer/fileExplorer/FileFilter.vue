<template>
  <div class="dfx-filter">
    <div class="dfx-filter-tabs" role="tablist" :aria-label="t('gui.designer.file_explorer.filter_label')">
      <button
        type="button"
        role="tab"
        class="dfx-filter-tab"
        data-test="dfx-filter-all"
        :class="{ 'dfx-filter-tab--on': modelValue === 'all' }"
        :aria-selected="modelValue === 'all'"
        @click="$emit('update:modelValue', 'all')"
      >{{ t('gui.designer.file_explorer.filter_all') }}</button>
      <button
        type="button"
        role="tab"
        class="dfx-filter-tab"
        data-test="dfx-filter-changed"
        :class="{ 'dfx-filter-tab--on': modelValue === 'changed' }"
        :aria-selected="modelValue === 'changed'"
        :title="baseTitle"
        @click="$emit('update:modelValue', 'changed')"
      >
        {{ t('gui.designer.file_explorer.filter_changed') }}
        <span v-if="changes.kind === 'ready'" class="dfx-filter-count" data-test="dfx-changed-count">{{ changedCount }}</span>
        <span v-else-if="baseWarn" class="dfx-filter-count dfx-filter-count--warn" data-test="dfx-changed-unknown">?</span>
      </button>
    </div>
    <span v-if="baseWarn" class="dfx-filter-base" data-test="dfx-base-label">{{ baseText }}</span>
  </div>
</template>

<script setup lang="ts">
// flowgate.v02.0024 T0005 — 전체 / 변경됨 filter (0024-D §5). A count is only ever shown for a
// confirmed Base. 0025 T0006 §6: in the normal case the Base is only the 변경됨 tooltip; a
// visible warning label appears only when the Base is unresolved or the comparison failed.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { ChangeState, ExplorerFilter } from './fileExplorerModel'

const props = defineProps<{ modelValue: ExplorerFilter; changes: ChangeState }>()
defineEmits<{ 'update:modelValue': [value: ExplorerFilter] }>()
const { t } = useI18n()

const changedCount = computed(() =>
  props.changes.kind === 'ready' ? Object.keys(props.changes.statuses).length : 0,
)
const baseWarn = computed(() => props.changes.kind === 'unresolved' || props.changes.kind === 'error')
const baseTitle = computed(() =>
  props.changes.kind === 'ready'
    ? t('gui.designer.file_explorer.base_label', { branch: props.changes.baseBranch })
    : undefined,
)
const baseText = computed(() =>
  props.changes.kind === 'unresolved'
    ? t('gui.designer.file_explorer.base_unresolved')
    : t('gui.designer.file_explorer.changes_failed_short'),
)
</script>

<style scoped>
.dfx-filter {
  display: flex;
  flex: 1 1 auto;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.dfx-filter-tabs {
  display: inline-flex;
  padding: 2px;
  border: 1px solid var(--fg2-line);
  border-radius: var(--fg2-control-radius);
  background: var(--fg2-control-surface-flat);
}

.dfx-filter-tab {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 10px;
  border: none;
  border-radius: var(--fg2-control-radius-sm);
  font-size: 12px;
  color: var(--fg2-muted);
  background: transparent;
  cursor: pointer;
}

.dfx-filter-tab:hover {
  color: var(--fg2-text);
}

.dfx-filter-tab--on {
  color: var(--fg2-text);
  background: var(--fg2-status-info-bg);
  box-shadow: inset 0 0 0 1px var(--fg2-status-info-border);
}

.dfx-filter-count {
  font-size: 10.5px;
  color: var(--fg2-status-info-fg);
}

.dfx-filter-count--warn {
  color: var(--fg2-status-warning-fg);
}

.dfx-filter-base {
  margin-left: auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 11px;
  color: var(--fg2-status-warning-fg);
}
</style>
