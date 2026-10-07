<template>
  <!-- flowgate.v02.0036 — Inspector header row (0035 v7, fixed 40px): kind icon box, kind
       badge and the Node title on the left; the meta slot (later: memo icon / origin badges),
       the state chip and the actions slot (later: primary action + its popover, ⋯) on the
       right. It shows only what the Graph Model already has — no action is wired here. -->
  <header
    class="inspector-header"
    :style="{ '--inspector-kind-accent': kind.accent }"
    data-test="designer-inspector-header"
    :data-kind="kind.key"
  >
    <span class="inspector-header-icon" aria-hidden="true">
      <svg viewBox="0 0 24 24">
        <path :d="kind.icon" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
      </svg>
    </span>
    <span class="inspector-header-kind" data-test="designer-inspector-kind">{{ t(`gui.designer.center.node_kinds.${kind.key}`) }}</span>
    <span class="inspector-header-title" :title="node.title" data-test="designer-inspector-title">{{ node.title }}</span>
    <span class="inspector-header-gap" />
    <slot name="meta" />
    <span
      v-if="state"
      class="inspector-header-state"
      :class="`inspector-header-state--${state.tone}`"
      data-test="designer-inspector-state"
    >{{ t(`gui.designer.center.node_states.${state.key}`) }}</span>
    <div v-if="$slots.actions" class="inspector-header-actions" data-test="designer-inspector-actions">
      <slot name="actions" />
    </div>
  </header>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { flowNodeKindDisplay, flowNodeStateDisplay } from '../center/canvas/flowNodeKinds'
import type { FlowGraphNode } from '../center/graph/flowGraph'

const props = defineProps<{
  node: FlowGraphNode
}>()

const { t } = useI18n()

const kind = computed(() => flowNodeKindDisplay(props.node.kind))
const state = computed(() => flowNodeStateDisplay(props.node.state))
</script>

<style scoped>
.inspector-header {
  display: flex;
  flex: 0 0 40px;
  align-items: center;
  gap: 8px;
  box-sizing: border-box;
  height: 40px;
  min-width: 0;
  padding: 0 10px 0 12px;
  border-bottom: 1px solid var(--fg2-line);
  background: #0c1b2d;
}

.inspector-header-icon {
  display: grid;
  flex: 0 0 24px;
  place-items: center;
  width: 24px;
  height: 24px;
  border-radius: 6px;
  background: color-mix(in srgb, var(--inspector-kind-accent) 85%, #0a1422);
  color: #fff;
}

.inspector-header-icon svg {
  display: block;
  width: 14px;
  height: 14px;
}

.inspector-header-kind {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  height: 22px;
  padding: 0 8px;
  border: 1px solid color-mix(in srgb, var(--inspector-kind-accent) 55%, transparent);
  border-radius: 6px;
  background: color-mix(in srgb, var(--inspector-kind-accent) 16%, #0b1827);
  color: #e6f0fb;
  font-size: 11.5px;
  font-weight: 700;
  white-space: nowrap;
}

/* A long title shrinks first; the kind badge and the right side keep their place. */
.inspector-header-title {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  color: var(--fg2-text);
  font-size: 13px;
  font-weight: 700;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.inspector-header-gap {
  flex: 1 1 8px;
  min-width: 8px;
}

.inspector-header-state {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  height: 20px;
  padding: 0 8px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius-pill);
  color: var(--fg2-muted);
  font-size: 11px;
  font-weight: 700;
  white-space: nowrap;
}

.inspector-header-state--info {
  border-color: var(--fg2-status-info-border);
  background: var(--fg2-status-info-bg);
  color: var(--fg2-status-info-fg);
}

.inspector-header-state--success {
  border-color: var(--fg2-status-success-border);
  background: var(--fg2-status-success-bg);
  color: var(--fg2-status-success-fg);
}

.inspector-header-state--warning {
  border-color: var(--fg2-status-warning-border);
  background: var(--fg2-status-warning-bg);
  color: var(--fg2-status-warning-fg);
}

.inspector-header-state--danger {
  border-color: var(--fg2-status-danger-border);
  background: var(--fg2-status-danger-bg);
  color: var(--fg2-status-danger-fg);
}

.inspector-header-actions {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 6px;
}
</style>
