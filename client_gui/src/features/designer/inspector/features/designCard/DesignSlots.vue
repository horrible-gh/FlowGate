<template>
  <!-- flowgate.v02.0051 stage 2 — the 설계 card's documents tab body. A thin container: designCardModel
       decides the lines; the document icon (or the name) opens a document in its document window, ✎ straight
       in its editor. No server call of its own.
       flowgate.v02.0053 T#5 (0053 CH) — shaped like 조사 · 작업: 지시서 · 레포트, without the "설명 대기" note;
       the 지시서 line is hidden when the card's settings omit the instruction (useDesignCard). -->
  <section class="inspector-design-card" data-test="inspector-design-card" :data-doc-type="target.docType ?? ''">
    <InspectorSlotList :slots="lines" :editable="!target.readOnly" @open="open" @edit="edit" @history="history" />
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { openDocument, openDocumentHistory } from '../../openDocument'
import type { InspectorTarget } from '../../inspectorTarget'
import InspectorSlotList, { type InspectorSlotView } from '../../parts/InspectorSlotList.vue'
import { designCardSlots, type DesignSlot } from './designCardModel'
import { useDesignCard } from './useDesignCard'

const props = defineProps<{
  target: InspectorTarget
}>()

const { t, te } = useI18n()

const k = (key: string) => `gui.inspector.designCard.${key}`

const { instructionOmitted } = useDesignCard(() => props.target)

function stateLabel(state: NonNullable<DesignSlot['state']>): string {
  const key = `gui.designer.inspector.${state.key}`
  return te(key) ? t(key) : state.raw
}

function lineOf(slot: DesignSlot): InspectorSlotView {
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

const lines = computed(() => designCardSlots(props.target, instructionOmitted.value).map(lineOf))

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
.inspector-design-card {
  min-width: 0;
}
</style>
