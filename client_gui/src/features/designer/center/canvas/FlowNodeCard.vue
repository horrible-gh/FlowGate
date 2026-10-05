<template>
  <!-- flowgate.v02.0026 — FlowGate Node card (0003-D §6.3): kind glyph + kind name + state on
       top, the title below (two lines at most). The handles only give Edges an anchor (target
       on the left, source on the right); they are invisible and never connectable. -->
  <div
    class="flow-node-card"
    :class="{ 'is-selected': data.selected, 'is-unknown': kind.key === 'unknown' }"
    :style="{ '--flow-node-accent': kind.accent }"
    data-test="designer-canvas-node"
    :data-node-id="id"
    :data-kind="kind.key"
  >
    <Handle type="target" :position="Position.Left" :connectable="false" class="flow-node-handle" />
    <div class="flow-node-head">
      <svg class="flow-node-icon" viewBox="0 0 16 16" aria-hidden="true">
        <path :d="kind.icon" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" />
      </svg>
      <span class="flow-node-kind" data-test="designer-canvas-node-kind">{{ t(`gui.designer.center.node_kinds.${kind.key}`) }}</span>
      <span
        v-if="state"
        class="flow-node-state"
        :class="`flow-node-state--${state.tone}`"
        data-test="designer-canvas-node-state"
      >{{ t(`gui.designer.center.node_states.${state.key}`) }}</span>
    </div>
    <p class="flow-node-title" :title="data.title" data-test="designer-canvas-node-title">{{ data.title }}</p>
    <Handle type="source" :position="Position.Right" :connectable="false" class="flow-node-handle" />
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { Handle, Position } from '@vue-flow/core'
import { flowNodeKindDisplay, flowNodeStateDisplay } from './flowNodeKinds'
import type { FlowGateNodeData } from './vueFlowMapping'

const props = defineProps<{
  id: string
  data: FlowGateNodeData
}>()

const { t } = useI18n()

const kind = computed(() => flowNodeKindDisplay(props.data.kind))
const state = computed(() => flowNodeStateDisplay(props.data.state))
</script>

<style scoped>
/* Card width is defined here only (initial 200px, 0003-D §6.3). */
.flow-node-card {
  position: relative;
  box-sizing: border-box;
  width: 200px;
  padding: 9px 12px 10px;
  border: 1px solid var(--fg2-line-strong);
  border-left: 3px solid var(--flow-node-accent);
  border-radius: var(--fg2-control-radius);
  background: var(--fg2-control-surface-strong);
  color: var(--fg2-text);
  font-family: var(--fg2-font);
  cursor: pointer;
  transition: border-color 0.16s ease, box-shadow 0.16s ease;
}

.flow-node-card:hover:not(.is-selected) {
  border-color: var(--fg2-control-hover-border);
  border-left-color: var(--flow-node-accent);
}

.flow-node-card.is-selected {
  border-color: var(--fg2-blue);
  border-left-color: var(--fg2-blue);
  box-shadow: 0 0 0 1px var(--fg2-blue), 0 8px 22px rgba(75, 163, 255, 0.22);
}

.flow-node-head {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  font-size: 11px;
  font-weight: 650;
}

.flow-node-icon {
  flex: none;
  width: 14px;
  height: 14px;
  color: var(--flow-node-accent);
}

.flow-node-kind {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  color: var(--flow-node-accent);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.flow-node-state {
  flex: none;
  padding: 1px 7px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius-pill);
  color: var(--fg2-muted);
  font-size: 10px;
  font-weight: 600;
  white-space: nowrap;
}

.flow-node-state--info {
  border-color: var(--fg2-status-info-border);
  color: var(--fg2-status-info-fg);
}

.flow-node-state--success {
  border-color: var(--fg2-status-success-border);
  color: var(--fg2-status-success-fg);
}

.flow-node-state--warning {
  border-color: var(--fg2-status-warning-border);
  color: var(--fg2-status-warning-fg);
}

.flow-node-state--danger {
  border-color: var(--fg2-status-danger-border);
  color: var(--fg2-status-danger-fg);
}

.flow-node-title {
  display: -webkit-box;
  margin: 6px 0 0;
  overflow: hidden;
  font-size: 13px;
  font-weight: 600;
  line-height: 1.4;
  word-break: break-all;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  line-clamp: 2;
}

/* Edge anchors only: invisible and inert. */
.flow-node-card :deep(.flow-node-handle) {
  width: 1px;
  min-width: 0;
  height: 1px;
  min-height: 0;
  border: 0;
  background: transparent;
  opacity: 0;
  pointer-events: none;
}
</style>
