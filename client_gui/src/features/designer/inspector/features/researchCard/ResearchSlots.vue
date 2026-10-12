<template>
  <!-- flowgate.v02.0051 stage 2 — the 조사 card's documents tab body (0035 v7 research.*): the 지시서 and 레포트 slot
       lines, built from the common slot list part. A thin container: researchCardModel decides the lines from
       the target; the document icon (or the line's name) opens a document in its window, ✎ straight in its
       editor. No server call of its own.
       flowgate.v02.0053 T#5 (0053 CH) — the lines are the card's documents (made when it was placed); the
       지시서 line is hidden when the card's settings omit the instruction (useResearchCard). -->
  <section class="inspector-research-card" data-test="inspector-research-card" :data-doc-type="target.docType ?? ''">
    <InspectorSlotList :slots="lines" :editable="!target.readOnly" @open="open" @edit="edit" />
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { openDocument } from '../../openDocument'
import type { InspectorTarget } from '../../inspectorTarget'
import InspectorSlotList, { type InspectorSlotView } from '../../parts/InspectorSlotList.vue'
import { researchCardSlots, type ResearchSlot } from './researchCardModel'
import { useResearchCard } from './useResearchCard'

const props = defineProps<{
  target: InspectorTarget
}>()

const { t, te } = useI18n()

const k = (key: string) => `gui.inspector.researchCard.${key}`

const { instructionOmitted } = useResearchCard(() => props.target)

/** The dot's wording: the common wording of a known value, the raw value otherwise. */
function stateLabel(state: NonNullable<ResearchSlot['state']>): string {
  const key = `gui.designer.inspector.${state.key}`
  return te(key) ? t(key) : state.raw
}

function lineOf(slot: ResearchSlot): InspectorSlotView {
  return {
    key: slot.key,
    kind: slot.kind,
    name: t(k(`slot.${slot.key}`)),
    id: slot.id,
    empty: slot.empty,
    emptyText: slot.emptyKey ? t(k(slot.emptyKey)) : null,
    state: slot.state ? { label: stateLabel(slot.state), tone: slot.state.tone } : null,
    focus: slot.focus,
    danger: slot.state?.tone === 'danger',
    open: slot.open,
  }
}

const lines = computed(() => researchCardSlots(props.target, instructionOmitted.value).map(lineOf))

function open(line: InspectorSlotView) {
  if (line.id) openDocument(line.id)
}

/** ✎: the same document window, straight in its editor (flowgate.v02.0053 T#3). */
function edit(line: InspectorSlotView) {
  if (line.id) openDocument(line.id, 'edit')
}
</script>

<style scoped>
.inspector-research-card {
  min-width: 0;
}
</style>
