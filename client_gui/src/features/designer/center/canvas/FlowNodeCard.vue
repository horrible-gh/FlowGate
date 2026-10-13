<template>
  <!-- flowgate.v02.0026 — FlowGate Node card (0003-D §6.3, look of MirageGlass 03-designer-shell):
       a fixed 176x92 card in a kind-colored frame. The head row holds the kind icon box, the kind
       name and the state; the body holds the one-line title. The handles give Edges an anchor
       (target on the left, source on the right). flowgate.v02.0045 S3: on a node of an editable
       draft (`data.connectable`) they are shown and a connection can be dragged from / to them;
       otherwise they stay invisible and never connectable. `data.issue` marks a node the last
       validation reported. flowgate.v02.0045 T0019: a 설계 card is named like its Palette card.
       flowgate.v02.0045 TR0020 rev2: no handle dots. On a connectable card a connection ends
       anywhere on the target card — the target handle covers the whole card and takes the pointer
       only while a connection is being dragged (FlowCanvas `is-connecting`). A 설계 card shows its
       document type's glyph. TR0020 rev4 (rejection item 3): a connection starts from any side —
       four start strips run along the card's top, right, bottom and left edges (one source handle
       each); the Edge itself is drawn between the facing sides (FlowEdgePath / edgeGeometry.ts).
       A card the last validation reported names the problem on hover (`data.issueText`).
       flowgate.v02.0056 F2-1: a Graph card with a run shows its card state in the chip (cardStateView: 지시서
       작성 중 · 결과 작성 중 · 작업 중 · 승인 대기 · 승인됨 …) instead of the Canvas state, and a card waiting for
       its approval offers [승인] under its title (`data.approvable`; an `approve` event, never the selection or a
       drag). A done card stays 승인됨 while the run is stopped: the waiting successors are the run band's. -->
  <div
    class="flow-node-card"
    :class="{ 'is-selected': data.selected, 'is-unknown': kind.key === 'unknown', 'has-issue': data.issue === true }"
    :style="{ '--flow-node-accent': kind.accent }"
    data-test="designer-canvas-node"
    :data-node-id="id"
    :data-kind="kind.key"
    :data-connectable="data.connectable ? 'true' : 'false'"
    :data-issue="data.issue ? 'true' : 'false'"
    :title="data.issue && data.issueText ? data.issueText : undefined"
  >
    <Handle
      type="target"
      :position="Position.Left"
      :connectable="data.connectable === true"
      class="flow-node-handle"
      :class="{ 'is-connectable': data.connectable, 'flow-node-drop': data.connectable }"
    />
    <div class="flow-node-head">
      <span class="flow-node-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24">
          <path :d="kind.icon" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </span>
      <span class="flow-node-kind" data-test="designer-canvas-node-kind">{{ t(kindLabelKey) }}</span>
      <span
        v-if="data.cardState"
        class="flow-node-state is-card"
        :class="`flow-node-state--${data.cardState.tone}`"
        data-test="designer-canvas-node-card-state"
        :data-state="data.cardState.key"
        :data-phase="data.cardState.phase"
      >{{ t(data.cardState.labelKey) }}</span>
      <span
        v-else-if="state"
        class="flow-node-state"
        :class="`flow-node-state--${state.tone}`"
        data-test="designer-canvas-node-state"
      >{{ t(`gui.designer.center.node_states.${state.key}`) }}</span>
    </div>
    <div class="flow-node-body">
      <p class="flow-node-title" :title="data.title" data-test="designer-canvas-node-title">{{ data.title }}</p>
      <button
        v-if="data.approvable"
        type="button"
        class="flow-node-approve nodrag nopan"
        :disabled="data.approving === true"
        :aria-busy="data.approving === true ? 'true' : undefined"
        :title="t('gui.designer.center.approval.hint')"
        data-test="designer-canvas-node-approve"
        @pointerdown.stop
        @mousedown.stop
        @click.stop="emit('approve', id)"
      >{{ t('gui.designer.center.approval.approve') }}</button>
    </div>
    <Handle
      v-for="side in LINK_SIDES"
      :id="side.id"
      :key="side.id"
      type="source"
      :position="side.position"
      :connectable="data.connectable === true"
      class="flow-node-handle"
      :class="{ 'is-connectable': data.connectable, 'flow-node-link': data.connectable, [`flow-node-link-${side.id}`]: data.connectable }"
    />
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { Handle, Position } from '@vue-flow/core'
import { flowNodeKindDisplay, flowNodeKindLabelKey, flowNodeStateDisplay } from './flowNodeKinds'
import type { FlowGateNodeData } from './vueFlowMapping'

const props = defineProps<{
  id: string
  data: FlowGateNodeData
}>()

const emit = defineEmits<{ approve: [nodeId: string] }>()

/** The four start strips of a connectable card, one source handle per side. */
const LINK_SIDES = [
  { id: 'top', position: Position.Top },
  { id: 'right', position: Position.Right },
  { id: 'bottom', position: Position.Bottom },
  { id: 'left', position: Position.Left },
] as const

const { t } = useI18n()

const kind = computed(() => flowNodeKindDisplay(props.data.kind, props.data.docType))
const kindLabelKey = computed(() => flowNodeKindLabelKey(props.data.kind, props.data.docType))
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
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 5px;
  padding: 8px 11px;
}

.flow-node-title {
  align-self: stretch;
}

/* flowgate.v02.0056 F2-1: the card state colours are the status tokens (승인 대기 stands out, 승인됨 is quiet). */
.flow-node-state.is-card.flow-node-state--info {
  color: var(--fg2-status-info-fg);
}

.flow-node-state.is-card.flow-node-state--warning {
  color: var(--fg2-status-warning-fg);
  font-weight: 700;
}

.flow-node-state.is-card.flow-node-state--success {
  color: var(--fg2-status-success-fg);
  opacity: 0.8;
}

.flow-node-state.is-card.flow-node-state--danger {
  color: var(--fg2-status-danger-fg);
}

/* Above the connection strips (z-index 2) so a press reaches it. */
.flow-node-approve {
  position: relative;
  z-index: 4;
  height: 20px;
  padding: 0 9px;
  border: 1px solid var(--fg2-status-warning-border);
  border-radius: 5px;
  background: var(--fg2-status-warning-bg);
  color: var(--fg2-status-warning-fg);
  font: inherit;
  font-size: 10.5px;
  font-weight: 700;
  cursor: pointer;
}

.flow-node-approve:hover:not(:disabled) {
  filter: brightness(1.15);
}

.flow-node-approve:disabled {
  cursor: progress;
  opacity: var(--fg2-control-disabled-opacity);
}

.flow-node-approve:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 1px;
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

/* flowgate.v02.0045 TR0020 rev2 / rev4: a draft node's connection areas, no dots.
   Start: a strip along each of the four sides (inside the card — the card clips what sticks
   out). Hovering the card shows the strips as a soft frame in the card color; the cursor turns to
   a crosshair over them. The middle of the card still drags the card.
   End: the target handle covers the whole card and stays out of the pointer's way until a
   connection is dragged (FlowCanvas). Where the drawn Edge attaches is not these handles' business
   (FlowEdgePath picks the facing sides). */
.flow-node-card :deep(.flow-node-handle.is-connectable) {
  min-width: 0;
  min-height: 0;
  border: 0;
  border-radius: 0;
  background: transparent;
  opacity: 1;
  transform: none;
}

.flow-node-card :deep(.flow-node-link) {
  z-index: 2;
  pointer-events: all;
  cursor: crosshair;
  transition: background 0.14s ease, box-shadow 0.14s ease;
}

.flow-node-card :deep(.flow-node-link-top),
.flow-node-card :deep(.flow-node-link-bottom) {
  right: auto;
  left: 0;
  width: 100%;
  height: 14px;
}

.flow-node-card :deep(.flow-node-link-top) {
  top: 0;
  bottom: auto;
}

.flow-node-card :deep(.flow-node-link-bottom) {
  top: auto;
  bottom: 0;
}

.flow-node-card :deep(.flow-node-link-left),
.flow-node-card :deep(.flow-node-link-right) {
  top: 0;
  bottom: auto;
  width: 24px;
  height: 100%;
}

.flow-node-card :deep(.flow-node-link-left) {
  right: auto;
  left: 0;
}

.flow-node-card :deep(.flow-node-link-right) {
  right: 0;
  left: auto;
}

.flow-node-card[data-connectable='true']:hover :deep(.flow-node-link-top) {
  background: linear-gradient(to top, transparent, color-mix(in srgb, var(--flow-node-accent) 16%, transparent));
  box-shadow: inset 0 2px 0 color-mix(in srgb, var(--flow-node-accent) 70%, transparent);
}

.flow-node-card[data-connectable='true']:hover :deep(.flow-node-link-bottom) {
  background: linear-gradient(to bottom, transparent, color-mix(in srgb, var(--flow-node-accent) 16%, transparent));
  box-shadow: inset 0 -2px 0 color-mix(in srgb, var(--flow-node-accent) 70%, transparent);
}

.flow-node-card[data-connectable='true']:hover :deep(.flow-node-link-left) {
  background: linear-gradient(to left, transparent, color-mix(in srgb, var(--flow-node-accent) 16%, transparent));
  box-shadow: inset 2px 0 0 color-mix(in srgb, var(--flow-node-accent) 70%, transparent);
}

.flow-node-card[data-connectable='true']:hover :deep(.flow-node-link-right) {
  background: linear-gradient(to right, transparent, color-mix(in srgb, var(--flow-node-accent) 16%, transparent));
  box-shadow: inset -2px 0 0 color-mix(in srgb, var(--flow-node-accent) 70%, transparent);
}

.flow-node-card :deep(.flow-node-link:hover) {
  background-color: color-mix(in srgb, var(--flow-node-accent) 22%, transparent);
}

.flow-node-card :deep(.flow-node-drop) {
  top: 0;
  bottom: auto;
  left: 0;
  z-index: 3;
  width: 100%;
  height: 100%;
  pointer-events: none;
}

.flow-node-card.has-issue {
  outline: 2px solid #ff9d51;
  outline-offset: 2px;
}
</style>
