<template>
  <!-- flowgate.v02.0051 stage 2 — the 요구사항 / 버그 card's documents tab body (0035 v7 req.draft /
       req.ok): the one document line, 요구사항 or 버그 리포트. A thin container: requirementCardModel
       decides the line; ↗ opens the document in the v0.1 screen; ＋ (writing) is not connected and shows
       so. No server call. -->
  <section class="inspector-requirement-card" data-test="inspector-requirement-card" :data-doc-type="target.docType ?? ''">
    <InspectorSlotList :slots="lines" @open="open" />
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { openDocument } from '../../openDocument'
import type { InspectorTarget } from '../../inspectorTarget'
import InspectorSlotList, { type InspectorSlotView } from '../../parts/InspectorSlotList.vue'
import { requirementCardSlots, type RequirementSlot } from './requirementCardModel'

const props = defineProps<{
  target: InspectorTarget
}>()

const { t, te } = useI18n()

const k = (key: string) => `gui.inspector.requirementCard.${key}`

function stateLabel(state: NonNullable<RequirementSlot['state']>): string {
  const key = `gui.designer.inspector.${state.key}`
  return te(key) ? t(key) : state.raw
}

function lineOf(slot: RequirementSlot): InspectorSlotView {
  return {
    key: slot.key,
    kind: slot.kind,
    name: t(k(`slot.${slot.name}`)),
    id: slot.id,
    empty: slot.empty,
    emptyText: slot.emptyKey ? t(k(slot.emptyKey)) : null,
    state: slot.state ? { label: stateLabel(slot.state), tone: slot.state.tone } : null,
    focus: slot.focus,
    danger: slot.state?.tone === 'danger',
    open: slot.open,
    write: slot.write,
    writeReason: slot.write === 'disabled' ? t(k('write_unconnected')) : null,
  }
}

const lines = computed(() => requirementCardSlots(props.target).map(lineOf))

function open(line: InspectorSlotView) {
  if (line.id) openDocument(line.id)
}
</script>

<style scoped>
.inspector-requirement-card {
  min-width: 0;
}
</style>
