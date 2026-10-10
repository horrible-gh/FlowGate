<template>
  <!-- flowgate.v02.0051 stage 2 — the documents tab body of an `other` card kind Node: a Graph gate shows
       the v7 확인 결과 result line (empty, "확인 전": the gate result is not connected); a document Node of
       an open type shows its one document line; anything else draws nothing. A thin container:
       gateCardModel decides; ↗ opens a document in the v0.1 screen. No server call. -->
  <section v-if="lines.length" class="inspector-gate-card" data-test="inspector-gate-card" :data-shape="shape">
    <InspectorSlotList :slots="lines" @open="open" />
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { openDocument } from '../../openDocument'
import type { InspectorTarget } from '../../inspectorTarget'
import InspectorSlotList, { type InspectorSlotView } from '../../parts/InspectorSlotList.vue'
import { gateCardShape, gateCardSlots, type GateSlot } from './gateCardModel'

const props = defineProps<{
  target: InspectorTarget
}>()

const { t, te } = useI18n()

const k = (key: string) => `gui.inspector.gateCard.${key}`

const shape = computed(() => gateCardShape(props.target))

function stateLabel(state: NonNullable<GateSlot['state']>): string {
  const key = `gui.designer.inspector.${state.key}`
  return te(key) ? t(key) : state.raw
}

function lineOf(slot: GateSlot): InspectorSlotView {
  return {
    key: slot.key,
    kind: slot.kind,
    name: slot.key === 'document' && slot.type ? `${t(k('slot.document'))} (${slot.type})` : t(k(`slot.${slot.key}`)),
    id: slot.id,
    empty: slot.empty,
    emptyText: slot.emptyKey ? t(k(slot.emptyKey)) : null,
    state: slot.state ? { label: stateLabel(slot.state), tone: slot.state.tone } : null,
    focus: slot.focus,
    danger: slot.state?.tone === 'danger',
    open: slot.open,
  }
}

const lines = computed(() => gateCardSlots(props.target).map(lineOf))

function open(line: InspectorSlotView) {
  if (line.id) openDocument(line.id)
}
</script>

<style scoped>
.inspector-gate-card {
  min-width: 0;
}
</style>
