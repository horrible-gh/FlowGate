<template>
  <!-- flowgate.v02.0051 stage 2 — the 요구사항 / 버그 card's documents tab body (0035 v7 req.draft /
       req.ok). A thin container: requirementCardModel decides the lines; the document icon (or the line's
       name) opens a document in its window, ✎ straight in its editor. No server call.
       flowgate.v02.0053 T#5 (0053 CH) — 요구사항 (버그 리포트 on a bug) · AI 위임 while AI 위임 is on. -->
  <section class="inspector-requirement-card" data-test="inspector-requirement-card" :data-doc-type="target.docType ?? ''">
    <InspectorSlotList :slots="lines" :editable="!target.readOnly" @open="open" @edit="edit" />
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
  }
}

const lines = computed(() => requirementCardSlots(props.target).map(lineOf))

function open(line: InspectorSlotView) {
  if (line.id) openDocument(line.id)
}

/** ✎: the same document window, straight in its editor (flowgate.v02.0053 T#3). */
function edit(line: InspectorSlotView) {
  if (line.id) openDocument(line.id, 'edit')
}
</script>

<style scoped>
.inspector-requirement-card {
  min-width: 0;
}
</style>
