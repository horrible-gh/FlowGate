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
        @click="$emit('update:modelValue', 'changed')"
      >
        {{ t('gui.designer.file_explorer.filter_changed') }}
        <span v-if="changes.kind === 'ready'" class="dfx-filter-count" data-test="dfx-changed-count">{{ changedCount }}</span>
        <span v-else-if="baseWarn" class="dfx-filter-count dfx-filter-count--warn" data-test="dfx-changed-unknown">?</span>
      </button>
    </div>
    <span class="dfx-filter-base" :class="{ 'dfx-filter-base--warn': baseWarn }" data-test="dfx-base-label">{{ baseText }}</span>
  </div>
</template>

<script setup lang="ts">
// flowgate.v02.0024 T0005 — 전체 / 변경됨 filter (0024-D §5). Also states which Base the
// "변경됨" count was computed against, or that it could not be established — a count is
// only ever shown next to a confirmed Base.
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
const baseText = computed(() => {
  switch (props.changes.kind) {
    case 'ready': return t('gui.designer.file_explorer.base_label', { branch: props.changes.baseBranch })
    case 'unresolved': return t('gui.designer.file_explorer.base_unresolved')
    case 'error': return t('gui.designer.file_explorer.changes_failed_short')
    case 'loading': return t('gui.designer.file_explorer.changes_loading')
    default: return ''
  }
})
</script>

<style scoped>
.dfx-filter {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
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
  color: var(--fg2-muted);
}

.dfx-filter-base--warn {
  color: var(--fg2-status-warning-fg);
}
</style>
