<template>
  <!-- flowgate.v02.0026 — FlowGate Node card (0003-D §6.3, look of MirageGlass 03-designer-shell):
       a fixed 176x92 card in a kind-colored frame. The head row holds the kind icon box, the kind
       name and the state; the body holds the one-line title. The handles give Edges an anchor
       (target on the left, source on the right). flowgate.v02.0045 S3: on a node of an editable
       draft (`data.connectable`) they are shown and a connection can be dragged from / to them;
       otherwise they stay invisible and never connectable. `data.issue` marks a node the last
       validation reported. -->
  <div
    class="flow-node-card"
    :class="{ 'is-selected': data.selected, 'is-unknown': kind.key === 'unknown', 'has-issue': data.issue === true }"
    :style="{ '--flow-node-accent': kind.accent }"
    data-test="designer-canvas-node"
    :data-node-id="id"
    :data-kind="kind.key"
    :data-connectable="data.connectable ? 'true' : 'false'"
    :data-issue="data.issue ? 'true' : 'false'"
  >
    <Handle
      type="target"
      :position="Position.Left"
      :connectable="data.connectable === true"
      class="flow-node-handle"
      :class="{ 'is-connectable': data.connectable }"
    />
    <div class="flow-node-head">
      <span class="flow-node-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24">
          <path :d="kind.icon" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </span>
      <span class="flow-node-kind" data-test="designer-canvas-node-kind">{{ t(`gui.designer.center.node_kinds.${kind.key}`) }}</span>
      <span
        v-if="state"
        class="flow-node-state"
        :class="`flow-node-state--${state.tone}`"
        data-test="designer-canvas-node-state"
      >{{ t(`gui.designer.center.node_states.${state.key}`) }}</span>
    </div>
    <div class="flow-node-body">
      <p class="flow-node-title" :title="data.title" data-test="designer-canvas-node-title">{{ data.title }}</p>
    </div>
    <Handle
      type="source"
      :position="Position.Right"
      :connectable="data.connectable === true"
      class="flow-node-handle"
      :class="{ 'is-connectable': data.connectable }"
    />
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
/* Card size is defined here only: fixed 176x92 (03-designer-shell `.node`). Long text never
   grows the card — every text line stays one line and ends with an ellipsis. */
.flow-node-card {
  position: relative;
  box-sizing: border-box;
  width: 176px;
  height: 92px;
  overflow: hidden;
  border: 1.5px solid var(--flow-node-accent);
  border-radius: 10px;
  background: linear-gradient(145deg, #13243a, #0f1e31);
  box-shadow: 0 8px 22px rgba(0, 0, 0, 0.47);
  color: var(--fg2-text);
  font-family: var(--fg2-font);
  cursor: pointer;
  transition: box-shadow 0.16s ease;
}

.flow-node-card:hover:not(.is-selected) {
  box-shadow:
    0 0 0 2px color-mix(in srgb, var(--flow-node-accent) 25%, transparent),
    0 8px 22px rgba(0, 0, 0, 0.53);
}

.flow-node-card.is-selected {
  box-shadow:
    0 0 0 2px color-mix(in srgb, var(--flow-node-accent) 70%, transparent),
    0 0 0 5px color-mix(in srgb, var(--flow-node-accent) 18%, transparent),
    0 8px 22px rgba(0, 0, 0, 0.53);
}

.flow-node-head {
  display: flex;
  align-items: center;
  box-sizing: border-box;
  height: 32px;
  min-width: 0;
  padding: 0 10px;
  border-bottom: 1px solid rgba(128, 160, 199, 0.18);
  background: color-mix(in srgb, var(--flow-node-accent) 11%, transparent);
}

.flow-node-icon {
  display: grid;
  flex: 0 0 24px;
  place-items: center;
  width: 24px;
  height: 24px;
  border-radius: 6px;
  background: color-mix(in srgb, var(--flow-node-accent) 88%, #0a1422);
  color: #fff;
}

.flow-node-icon svg {
  display: block;
  width: 15px;
  height: 15px;
}

/* When the head row runs short the state shrinks first; the kind name keeps its place. */
.flow-node-kind {
  flex: 0 1 auto;
  min-width: 0;
  margin-left: 7px;
  overflow: hidden;
  color: #c8d7e7;
  font-size: 10.5px;
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.flow-node-state {
  flex: 0 100 auto;
  min-width: 0;
  margin-left: auto;
  padding-left: 6px;
  overflow: hidden;
  color: #8fa7bf;
  font-size: 9px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.flow-node-body {
  padding: 8px 11px;
}

.flow-node-title {
  margin: 0;
  overflow: hidden;
  font-size: 12px;
  font-weight: 680;
  text-overflow: ellipsis;
  white-space: nowrap;
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

/* flowgate.v02.0045 S3: a draft node's connection points. */
.flow-node-card :deep(.flow-node-handle.is-connectable) {
  width: 10px;
  height: 10px;
  border: 2px solid var(--flow-node-accent);
  border-radius: 50%;
  background: #0d1a2c;
  opacity: 1;
  pointer-events: all;
  cursor: crosshair;
}

.flow-node-card.has-issue {
  outline: 2px solid #ff9d51;
  outline-offset: 2px;
}
</style>
