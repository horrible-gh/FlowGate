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
       A Graph card with a run shows a state icon and tooltip (working / awaiting approval / approved);
       pending has no mark. Other states keep their labels. A card waiting for
       its approval offers [승인] under its title (`data.approvable`; an `approve` event, never the selection or a
       drag). A done card stays 승인됨 while the run is stopped: the waiting successors are the run band's.
       flowgate.v02.0061 T#14: under the title, the card attempt's result integration when it needs a look (병합 대기 ·
       병합 중 · 병합 충돌 · 병합 실패) only. 병합 충돌 is a button: it opens the Git tool panel, where the Flow's
       conflict session is resolved (a `conflict` event, never the selection or a drag).
       flowgate.v02.0061 T#15: a held card shows the Hold mark (an open palm in a round frame) at the end of its head row,
       with its tooltip / accessible name. While held, the state text and duplicate Hold chip are hidden.
       flowgate.v02.0063: a card whose document holds a question still unanswered shows the [Q] mark in its head row.
       flowgate.v02.0070 T#1 (B1): the card only shows its title (the session's, one line) — a click on it is the card's
       selection as anywhere else on the card. The title is edited in the Inspector header (InspectorHeader). -->
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
        v-if="!data.hold && data.cardState && data.cardState.key !== 'pending'"
        class="flow-node-state is-card"
        :class="[`flow-node-state--${data.cardState.tone}`, { 'is-icon': cardStateIcon }]"
        :role="cardStateIcon ? 'img' : undefined"
        :aria-label="cardStateIcon ? t(data.cardState.labelKey) : undefined"
        :title="t(data.cardState.labelKey)"
        data-test="designer-canvas-node-card-state"
        :data-state="data.cardState.key"
        :data-phase="data.cardState.phase"
      >
        <svg v-if="cardStateIcon" viewBox="0 0 24 24" :class="{ 'is-spinning': data.cardState.phase === 'working' }" aria-hidden="true">
          <path :d="cardStateIcon" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
        <template v-else>{{ t(data.cardState.labelKey) }}</template>
      </span>
      <span
        v-else-if="!data.hold && !data.cardState && state"
        class="flow-node-state"
        :class="`flow-node-state--${state.tone}`"
        data-test="designer-canvas-node-state"
      >{{ t(`gui.designer.center.node_states.${state.key}`) }}</span>
      <span v-if="data.bodyUnreadable" class="flow-node-body-warning" role="img" :aria-label="t('gui.card.bodyUnreadable')" :title="t('gui.card.bodyUnreadable')" data-test="designer-canvas-node-body-warning">⚠</span>
      <span
        v-if="data.question"
        class="flow-node-question"
        role="img"
        aria-label="Q"
        data-test="designer-canvas-node-question-icon"
      >Q</span>
      <span
        v-if="data.hold"
        class="flow-node-hold"
        role="img"
        :aria-label="t('gui.designer.center.hold_icon')"
        :title="t('gui.designer.center.hold_icon')"
        data-test="designer-canvas-node-hold-icon"
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path :d="FLOW_NODE_HOLD_ICON" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </span>
    </div>
    <div class="flow-node-body">
      <p
        v-if="showTitle"
        class="flow-node-title"
        :title="data.title"
        data-test="designer-canvas-node-title"
      >{{ data.title }}</p>
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
      <div v-if="data.integration" class="flow-node-flags" data-test="designer-canvas-node-flags">
        <button
          v-if="data.integration === 'conflict'"
          type="button"
          class="flow-node-flag is-conflict nodrag nopan"
          :title="t('gui.designer.center.integration.open_conflict')"
          data-test="designer-canvas-node-conflict"
          @pointerdown.stop
          @mousedown.stop
          @click.stop="emit('conflict', id)"
        >{{ t('gui.designer.center.integration.conflict') }}</button>
        <span
          v-else-if="data.integration"
          class="flow-node-flag"
          :class="`is-${data.integration}`"
          data-test="designer-canvas-node-integration"
          :data-integration="data.integration"
        >{{ t(`gui.designer.center.integration.${data.integration}`) }}</span>
      </div>
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
import { FLOW_NODE_HOLD_ICON, flowNodeKindView, flowNodeStateDisplay } from './flowNodeKinds'
import type { FlowGateNodeData } from './vueFlowMapping'

const props = defineProps<{
  id: string
  data: FlowGateNodeData
}>()

const emit = defineEmits<{
  approve: [nodeId: string]
  /** flowgate.v02.0061 T#14: the card's 병합 충돌 was pressed (the Git tool panel resolves it). */
  conflict: [nodeId: string]
}>()

/** The four start strips of a connectable card, one source handle per side. */
const LINK_SIDES = [
  { id: 'top', position: Position.Top },
  { id: 'right', position: Position.Right },
  { id: 'bottom', position: Position.Bottom },
  { id: 'left', position: Position.Left },
] as const

const { t } = useI18n()

// flowgate.v02.0070 T#4 (B4): the same kind display as the Inspector header (a B root is a 버그).
const kind = computed(() => flowNodeKindView(props.data.kind, props.data.docType))
const kindLabelKey = computed(() => kind.value.labelKey)
const state = computed(() => flowNodeStateDisplay(props.data.state))

// Presentation only: retain the shared card state and approval rules.
const cardStateIcon = computed(() => {
  switch (props.data.cardState?.phase) {
    case 'approved':
      return 'M5 12l4 4L19 6'
    case 'awaiting':
      return 'M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18z M12 7v5l3 2'
    case 'working':
      return 'M21 12a9 9 0 1 1-9-9'
    default:
      return null
  }
})

/** The title is shown unless it only repeats the kind name of the head row. */
const showTitle = computed(() => props.data.title.trim() !== t(kindLabelKey.value).trim())
</script>

<style scoped>
.flow-node-body-warning { color: var(--fg2-status-warning-fg); margin-left: 4px; }

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

.flow-node-state.is-icon {
  position: relative;
  z-index: 4;
  display: grid;
  flex: 0 0 18px;
  place-items: center;
  width: 18px;
  height: 18px;
  padding-left: 0;
}

.flow-node-state.is-icon svg {
  display: block;
  width: 16px;
  height: 16px;
}

.flow-node-state.is-icon.flow-node-state--success {
  color: #22c98c;
  opacity: 1;
}

.flow-node-state .is-spinning {
  animation: flow-node-state-spin 1s linear infinite;
}

@keyframes flow-node-state-spin {
  to { transform: rotate(360deg); }
}

/* flowgate.v02.0061 T#15: the Hold mark — the warning tokens, at the end of the head row (after the state, if any). */
.flow-node-hold {
  display: grid;
  flex: 0 0 18px;
  place-items: center;
  width: 18px;
  height: 18px;
  margin-left: auto;
  border-radius: 50%;
  background: var(--fg2-status-warning-bg);
  color: var(--fg2-status-warning-fg);
}

.flow-node-hold svg {
  display: block;
  width: 16px;
  height: 16px;
}

/* flowgate.v02.0063: the [Q] mark — the info tokens, before the Hold mark (after the state, if any). */
.flow-node-question {
  display: grid;
  flex: 0 0 18px;
  place-items: center;
  width: 18px;
  height: 18px;
  margin-left: auto;
  border-radius: 4px;
  background: var(--fg2-status-info-bg);
  color: var(--fg2-status-info-fg);
  font-size: 11px;
  font-weight: 800;
  line-height: 1;
}

.flow-node-state + .flow-node-question,
.flow-node-question + .flow-node-hold {
  margin-left: 4px;
}

.flow-node-body {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 5px;
  padding: 8px 11px;
}

/* flowgate.v02.0056 F2-1: the card state colours are the status tokens (승인 대기 stands out, 승인됨 is quiet). */
.flow-node-state.is-card.flow-node-state--info {
  color: var(--fg2-status-info-fg);
}

.flow-node-state.is-card.flow-node-state--warning {
  color: var(--fg2-status-warning-fg);
  font-weight: 700;
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
  align-self: stretch;
  margin: 0;
  overflow: hidden;
  font-size: 12px;
  font-weight: 680;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* flowgate.v02.0061 T#14: the integration line under the title (status tokens; above the strips). */
.flow-node-flags {
  position: relative;
  z-index: 4;
  display: flex;
  gap: 4px;
  max-width: 100%;
  overflow: hidden;
}

.flow-node-flag {
  flex: none;
  height: 16px;
  padding: 0 6px;
  border: 1px solid var(--fg2-status-info-border);
  border-radius: 4px;
  background: var(--fg2-status-info-bg);
  color: var(--fg2-status-info-fg);
  font: inherit;
  font-size: 9.5px;
  line-height: 14px;
  white-space: nowrap;
}

.flow-node-flag.is-conflict,
.flow-node-flag.is-failed {
  border-color: var(--fg2-status-danger-border);
  background: var(--fg2-status-danger-bg);
  color: var(--fg2-status-danger-fg);
}

.flow-node-flag.is-conflict {
  font-weight: 700;
  cursor: pointer;
}

.flow-node-flag.is-conflict:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 1px;
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
