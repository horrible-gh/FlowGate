<template>
  <!-- flowgate.v02.0051 stage 2 — the documents tab body of an `other` card kind Node: a Graph gate shows
       the v7 확인 결과 result line (empty, "확인 전": the gate result is not connected); a document Node of
       an open type shows its one document line; anything else draws nothing. A thin container:
       gateCardModel decides; the document icon (or the name) opens a document in its document window, ✎ in its
       editor. No server call of its own.
       flowgate.v02.0053 T#5 (0053 CH) — a Custom card shows 지시서 · 레포트 like 조사 · 작업 (its 지시서 line hidden
       when its settings omit the instruction, useGateCard); a document line's name has no type code. -->
  <section v-if="lines.length" class="inspector-gate-card" data-test="inspector-gate-card" :data-shape="shape">
    <InspectorSlotList :slots="lines" :editable="!target.readOnly" @open="open" @edit="edit" @history="history" />
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { openDocument, openDocumentHistory } from '../../openDocument'
import type { InspectorTarget } from '../../inspectorTarget'
import InspectorSlotList, { type InspectorSlotView } from '../../parts/InspectorSlotList.vue'
import { gateCardShape, gateCardSlots, type GateSlot } from './gateCardModel'
import { useGateCard } from './useGateCard'

const props = defineProps<{
  target: InspectorTarget
}>()

const { t, te } = useI18n()

const k = (key: string) => `gui.inspector.gateCard.${key}`

const shape = computed(() => gateCardShape(props.target))

const { instructionOmitted } = useGateCard(() => (shape.value === 'custom' ? props.target : null))

function stateLabel(state: NonNullable<GateSlot['state']>): string {
  const key = `gui.designer.inspector.${state.key}`
  return te(key) ? t(key) : state.raw
}

function lineOf(slot: GateSlot): InspectorSlotView {
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
    author: authorOf(slot.key),
  }
}

/** flowgate.v02.0056 F2-1: the card line's author (the instruction line: 지시 AI 또는 사람), as the server sent it. */
function authorOf(key: string): string | null {
  return props.target.cardDocuments.find((line) => line.slot === key)?.author ?? null
}

const lines = computed(() => gateCardSlots(props.target, instructionOmitted.value).map(lineOf))

function open(line: InspectorSlotView) {
  if (line.id) openDocument(line.id)
}

/** ✎: the same document window, straight in its editor (flowgate.v02.0053 T#3). */
function edit(line: InspectorSlotView) {
  if (line.id) openDocument(line.id, 'edit')
}

/** The history button: the document's 검수·반려 이력 window (flowgate.v02.0056 F1). */
function history(line: InspectorSlotView) {
  if (line.id) openDocumentHistory(line.id)
}
</script>

<style scoped>
.inspector-gate-card {
  min-width: 0;
}
</style>
