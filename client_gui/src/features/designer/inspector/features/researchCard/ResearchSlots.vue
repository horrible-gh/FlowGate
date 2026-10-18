<template>
  <!-- flowgate.v02.0051 stage 2 — the 조사 card's documents tab body (0035 v7 research.*): the 지시서 and 레포트 slot
       lines, built from the common slot list part. A thin container: researchCardModel decides the lines from
       the target; the document icon (or the line's name) opens a document in its window, ✎ straight in its
       editor. No server call of its own.
       flowgate.v02.0053 T#5 (0053 CH) — the lines are the card's documents (made when it was placed); the
       지시서 line is hidden when the card's settings omit the instruction (useResearchCard). -->
  <section class="inspector-research-card" data-test="inspector-research-card" :data-doc-type="target.docType ?? ''">
    <InspectorSlotList :slots="lines" :editable="!target.readOnly" @open="open" @edit="edit" @history="history" @action="action">
      <!-- flowgate.v02.0068 T#1: a document line's [업로드][다운로드]. -->
      <template #io="{ docId }"><DocumentIoGroup :doc-id="docId"  :project-id="target.projectId" :group-id="target.flowId" /></template>
    </InspectorSlotList>
  </section>
</template>

<script setup lang="ts">
import { computed, inject } from 'vue'
import { useI18n } from 'vue-i18n'
import { INSPECTOR_HOST } from '../../inspectorHost'
import { cardLineRow, DocumentIoGroup, openCardLineAction, openDocument, openDocumentHistory } from '../../openDocument'
import type { InspectorTarget } from '../../inspectorTarget'
import InspectorSlotList, { type InspectorSlotView, type SlotActionKey } from '../../parts/InspectorSlotList.vue'
import { researchCardSlots, type ResearchSlot } from './researchCardModel'
import { useResearchCard } from './useResearchCard'

const props = defineProps<{
  target: InspectorTarget
}>()

const { t, te } = useI18n()

const host = inject(INSPECTOR_HOST, null)

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
    author: authorOf(slot.key),
    // flowgate.v02.0056 F2-2: 문서 없음 [작성] · 비어 있음 · the history number · [AI 호출] of the card's own line.
    ...cardLineRow(props.target, slot, slot.key),
  }
}

/** flowgate.v02.0056 F2-1: the card line's author (the instruction line: 지시 AI 또는 사람), as the server sent it. */
function authorOf(key: string): string | null {
  return props.target.cardDocuments.find((line) => line.slot === key)?.author ?? null
}

const lines = computed(() => researchCardSlots(props.target, instructionOmitted.value).map(lineOf))

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

/**
 * flowgate.v02.0056 F2-2: a row action of a card line — [작성] opens the line's write window, [AI 호출] the
 * document's AI call window (openCardLineAction; the Canvas is read again after them).
 */
function action(line: InspectorSlotView, key: SlotActionKey) {
  openCardLineAction(props.target, line, key, host)
}
</script>

<style scoped>
.inspector-research-card {
  min-width: 0;
}
</style>
