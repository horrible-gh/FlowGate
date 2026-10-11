<template>
  <!-- flowgate.v02.0051 stage 2 — the 설계 card's documents tab body: the v7 "설명 대기" note (0035
       insp.js special 'tbd') above the one line of the bound document. Nothing here decides what the
       설계 card is (0051 turn 7). A thin container: designCardModel decides the line; ↗ opens the
       document in its document window, ✎ straight in its editor. No server call. -->
  <section class="inspector-design-card" data-test="inspector-design-card" :data-doc-type="target.docType ?? ''">
    <InspectorTbdNote :title="t(k('tbd.title'))" :label="t(k('tbd.text'))" />
    <InspectorSlotList :slots="lines" :editable="!target.readOnly" @open="open" @edit="edit" />
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { openDocument } from '../../openDocument'
import type { InspectorTarget } from '../../inspectorTarget'
import InspectorSlotList, { type InspectorSlotView } from '../../parts/InspectorSlotList.vue'
import InspectorTbdNote from '../../parts/InspectorTbdNote.vue'
import { designCardSlots, type DesignSlot } from './designCardModel'

const props = defineProps<{
  target: InspectorTarget
}>()

const { t, te } = useI18n()

const k = (key: string) => `gui.inspector.designCard.${key}`

function stateLabel(state: NonNullable<DesignSlot['state']>): string {
  const key = `gui.designer.inspector.${state.key}`
  return te(key) ? t(key) : state.raw
}

function lineOf(slot: DesignSlot): InspectorSlotView {
  return {
    key: slot.key,
    kind: slot.kind,
    name: slot.type ? t(k(`type.${slot.type}`)) : t(k('slot.document')),
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

const lines = computed(() => designCardSlots(props.target).map(lineOf))

function open(line: InspectorSlotView) {
  if (line.id) openDocument(line.id)
}

/** ✎: the same document window, straight in its editor (flowgate.v02.0053 T#3). */
function edit(line: InspectorSlotView) {
  if (line.id) openDocument(line.id, 'edit')
}
</script>

<style scoped>
.inspector-design-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}
</style>
