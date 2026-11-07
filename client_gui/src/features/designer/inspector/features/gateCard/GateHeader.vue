<template>
  <span v-if="glyph" class="gate-header-state" :data-tone="glyph.tone" role="img" :aria-label="title" :title="title" data-test="gate-header-state" :data-state="state.key">
    <svg viewBox="0 0 24 24" aria-hidden="true"><path :d="glyph.path" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" /></svg>
  </span>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { InspectorTarget } from '../../inspectorTarget'
import { gateHeaderState } from '../../../center/canvas/gateHeaderState'
import { flowNodeHeaderIcon } from '../../../center/canvas/flowNodeKinds'
import { useGateContext } from './useGateContext'
const props = defineProps<{ target: InspectorTarget }>()
const context = useGateContext(() => props.target.nodeType === 'gate' ? props.target : null)
const { t } = useI18n()
const state = computed(() => { const c = context.value; const node = c?.status.value === 'ready' ? c.node.value : props.target.nodeRun; return gateHeaderState(node?.gate, node) })
const glyph = computed(() => flowNodeHeaderIcon(state.value))
const title = computed(() => [t(state.value.labelKey), state.value.reason].filter(Boolean).join(' · '))
</script>
<style scoped>
.gate-header-state { display: inline-flex; flex: none; width: 18px; height: 18px; color: var(--fg2-muted); }
.gate-header-state svg { width: 100%; height: 100%; }
.gate-header-state[data-tone="success"] { color: var(--fg2-status-success-fg); }
.gate-header-state[data-tone="warning"] { color: var(--fg2-status-warning-fg); }
.gate-header-state[data-tone="danger"] { color: var(--fg2-status-danger-fg); }
</style>
