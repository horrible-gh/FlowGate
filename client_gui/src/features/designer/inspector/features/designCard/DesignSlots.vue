<template>
  <!-- flowgate.v02.0051 stage 2 — the 설계 card's documents tab body. A thin container: designCardModel
       decides the lines; the document icon (or the name) opens a document in its document window, ✎ straight
       in its editor. No server call of its own.
       flowgate.v02.0053 T#5 (0053 CH) — shaped like 조사 · 작업: 지시서 · 레포트, without the "설명 대기" note;
       the 지시서 line is hidden when the card's settings omit the instruction (useDesignCard). -->
  <section class="inspector-design-card" data-test="inspector-design-card" :data-doc-type="target.docType ?? ''">
    <InspectorSlotList :edit-refusal="refusals.update.value" :write-refusal="refusals.update.value ?? refusals.create.value" :decisions="documentDecisions" :slots="lines" :editable="!target.readOnly" @open="open" @edit="edit" @history="history" @action="action">
      <!-- flowgate.v02.0068 T#1: a document line's [업로드][다운로드]. -->
      <template #io="{ docId }"><DocumentIoGroup :doc-id="docId"  :project-id="target.projectId" :group-id="target.flowId" :card="cardDocumentContext(target, docId)" /></template>
    </InspectorSlotList>
  </section>
</template>

<script setup lang="ts">
import { useCardDocumentDecisions } from '../../openDocument'
import { computed, inject } from 'vue'
import { useI18n } from 'vue-i18n'
import { INSPECTOR_HOST } from '../../inspectorHost'
import { cardDocumentContext, cardLineRow, DocumentIoGroup, openCardLineAction, openDocument, openDocumentHistory, useDocumentRefusals } from '../../openDocument'
import type { InspectorTarget } from '../../inspectorTarget'
import InspectorSlotList, { type InspectorSlotView, type SlotActionKey } from '../../parts/InspectorSlotList.vue'
import { designCardSlots, type DesignSlot } from './designCardModel'
import { useDesignCard } from './useDesignCard'

const props = defineProps<{
  target: InspectorTarget
}>()

const { t, te } = useI18n()
// The server's refusals of editing the card's documents: ✎ and the row actions go off with the reason as their tooltip.
const refusals = useDocumentRefusals()

const host = inject(INSPECTOR_HOST, null)

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
    // the kind's key travels with the line (see openCardLineAction).
    nameKey: k(`slot.${slot.key}`),
    id: slot.id,
    empty: slot.empty,
    emptyText: slot.emptyKey ? t(k(slot.emptyKey)) : null,
    state: slot.state ? { label: stateLabel(slot.state), tone: slot.state.tone } : null,
    focus: slot.focus,
    danger: slot.state?.tone === 'danger',
    open: slot.open,
    author: authorOf(slot.key),
    // flowgate.v02.0056 F2-2: 문서 없음 [작성] · Empty · the history number · [AI 호출] of the card's own line.
    ...cardLineRow(props.target, slot, slot.key),
  }
}

/** flowgate.v02.0056 F2-1: the card line's author (the instruction line: 지시 AI or a person), as the server sent it. */
function authorOf(key: string): string | null {
  return props.target.cardDocuments.find((line) => line.slot === key)?.author ?? null
}

const lines = computed(() => designCardSlots(props.target, instructionOmitted.value).map(lineOf))

function open(line: InspectorSlotView) {
  if (line.id) openDocument(line.id, 'view', { card: cardDocumentContext(props.target, line.id, line.key) })
}

/** ✎: the same document window, straight in its editor. */
function edit(line: InspectorSlotView) {
  if (line.id) openDocument(line.id, 'edit', { card: cardDocumentContext(props.target, line.id, line.key) })
}

/** The history button: the document's review·rejection history window. */
function history(line: InspectorSlotView) {
  if (line.id) openDocumentHistory(line.id)
}

/**
 * flowgate.v02.0056 F2-2: a row action of a card line — [작성] opens the line's write window, [AI 호출] the
 * document's AI call window (openCardLineAction; the Canvas is read again after them).
 */
function action(line: InspectorSlotView, key: SlotActionKey) {
  openCardLineAction(props.target, line, key, host)
}

const documentDecisions = useCardDocumentDecisions(() => props.target)
</script>

<style scoped>
.inspector-design-card {
  min-width: 0;
}
</style>
