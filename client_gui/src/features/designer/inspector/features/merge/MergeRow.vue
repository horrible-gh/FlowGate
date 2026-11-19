<template>
  <InspectorSlotRow v-if="visible" :name="t('gui.inspector.merge.row.name')" openable
    :state="{ label: t(`gui.inspector.merge.row.${state}`), tone: tone ?? 'success' }"
    :progress="state === 'waiting' || state === 'catching_up' ? 50 : null"
    :class="['merge-row', `is-${tone ?? 'success'}`]" data-test="merge-row" @open="open">
    <button type="button" class="merge-open" :aria-label="t('gui.inspector.merge.row.open')" data-test="merge-open" @click="open">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 4l10 16 M17 4L7 20 M4 9h4 M16 15h4" /></svg>
    </button>
  </InspectorSlotRow>
</template>
<script setup lang="ts">
import { computed, toRef } from 'vue'
import { useI18n } from 'vue-i18n'
import InspectorSlotRow from '../../parts/InspectorSlotRow.vue'
import type { InspectorTarget } from '../../inspectorTarget'
import { mergeRowState, openCardMerge, useMergeRowTone, useMergeRowVisible } from './useMerge'
const props = defineProps<{ target: InspectorTarget }>()
const { t } = useI18n()
const visible = useMergeRowVisible(toRef(props, 'target'))
const tone = useMergeRowTone(toRef(props, 'target'))
const state = computed(() => mergeRowState(props.target))
const open = () => openCardMerge(props.target.flowId, props.target.nodeId)
</script>
<style scoped>
.merge-row.is-danger { border-color: #8a3341; }
.merge-row.is-info { border-color: #2d5f9b; }
.merge-open { display: inline-flex; align-items: center; justify-content: center; width: 30px; height: 28px; border: 1px solid #2d5f9b; border-radius: 6px; background: #10294a; color: #8cc0ff; cursor: pointer; }
.is-danger .merge-open { border-color: #8a3341; background: #3a1620; color: #ff8fa0; }
.is-success .merge-open { border-color: #1f6b52; background: #0f2e25; color: #5fe0b0; }
.merge-open svg { width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; }
.merge-row :deep(.inspector-slot-progress) { width: 46px; flex: 0 0 46px; }
.merge-row :deep(.inspector-slot-progress i) { background: #8cc0ff; }
</style>
