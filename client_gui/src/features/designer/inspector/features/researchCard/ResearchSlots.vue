<template>
  <!-- flowgate.v02.0051 stage 2 — the 조사 card's documents tab body (0035 v7 research.*): the 지시서 (N)
       and 레포트 (NR) slot lines, built from the common slot list part. A thin container: researchCardModel
       decides the lines from the target; ↗ opens a known document in the v0.1 screen; ＋ (writing) is not
       connected and shows so. No server call. -->
  <section class="inspector-research-card" data-test="inspector-research-card" :data-doc-type="target.docType ?? ''">
    <InspectorSlotList :slots="lines" @open="open" />
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { openDocument } from '../../openDocument'
import type { InspectorTarget } from '../../inspectorTarget'
import InspectorSlotList, { type InspectorSlotView } from '../../parts/InspectorSlotList.vue'
import { researchCardSlots, type ResearchSlot } from './researchCardModel'

const props = defineProps<{
  target: InspectorTarget
}>()

const { t, te } = useI18n()

const k = (key: string) => `gui.inspector.researchCard.${key}`

/** The pill text of a state: the common wording of a known value, the raw value otherwise. */
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
    summary: slot.summaryKey ? t(k(slot.summaryKey)) : null,
    focus: slot.focus,
    danger: slot.state?.tone === 'danger',
    open: slot.open,
    write: slot.write,
    writeReason: slot.write === 'disabled' ? t(k('write_unconnected')) : null,
  }
}

const lines = computed(() => researchCardSlots(props.target).map(lineOf))

function open(line: InspectorSlotView) {
  if (line.id) openDocument(line.id)
}
</script>

<style scoped>
.inspector-research-card {
  min-width: 0;
}
</style>
