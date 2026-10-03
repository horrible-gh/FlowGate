<template>
  <div class="dfx-filter">
    <div class="dfx-filter-tabs" role="tablist" :aria-label="t('main.designer_explorer.filter_label')">
      <button
        type="button"
        role="tab"
        class="dfx-filter-tab"
        data-test="dfx-filter-all"
        :class="{ 'dfx-filter-tab--on': modelValue === 'all' }"
        :aria-selected="modelValue === 'all'"
        @click="$emit('update:modelValue', 'all')"
      >{{ t('main.designer_explorer.filter_all') }}</button>
      <button
        type="button"
        role="tab"
        class="dfx-filter-tab"
        data-test="dfx-filter-changed"
        :class="{ 'dfx-filter-tab--on': modelValue === 'changed' }"
        :aria-selected="modelValue === 'changed'"
        @click="$emit('update:modelValue', 'changed')"
      >
        {{ t('main.designer_explorer.filter_changed') }}
        <span v-if="changes.kind === 'ready'" class="dfx-filter-count" data-test="dfx-changed-count">{{ changedCount }}</span>
        <span v-else-if="changes.kind === 'unresolved' || changes.kind === 'error'" class="dfx-filter-count dfx-filter-count--warn">?</span>
      </button>
    </div>
    <span class="dfx-filter-base" :class="{ 'dfx-filter-base--warn': baseWarn }" data-test="dfx-base-label">{{ baseText }}</span>
  </div>
</template>

<script setup lang="ts">
// flowgate.v02.0024 T0003 — 전체 / 변경됨 filter (0024-D §5). Also states which Base the
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
    case 'ready': return t('main.designer_explorer.base_label', { branch: props.changes.baseBranch })
    case 'unresolved': return t('main.designer_explorer.base_unresolved')
    case 'error': return t('main.designer_explorer.changes_failed_short')
    case 'loading': return t('main.designer_explorer.changes_loading')
    default: return ''
  }
})
</script>

<style scoped>
.dfx-filter { display: flex; align-items: center; gap: 8px; padding: 6px 8px; }
.dfx-filter-tabs { display: inline-flex; padding: 2px; border-radius: 6px; background: rgba(255, 255, 255, 0.06); }
.dfx-filter-tab {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 10px;
  font-size: 0.74rem;
  color: var(--text-m, rgba(255, 255, 255, 0.65));
  background: transparent;
  border: none;
  border-radius: 4px;
  cursor: pointer;
}
.dfx-filter-tab--on { color: #fff; background: rgba(59, 130, 246, 0.35); }
.dfx-filter-count { font-size: 0.66rem; opacity: 0.85; }
.dfx-filter-count--warn { color: #fcd34d; }
.dfx-filter-base { margin-left: auto; font-size: 0.68rem; color: var(--text-m, rgba(255, 255, 255, 0.55)); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.dfx-filter-base--warn { color: #fcd34d; }
</style>
