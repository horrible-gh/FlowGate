<template>
  <!-- D0007 §6: live gate result and decisions; other card document lines are unchanged. -->
  <section v-if="lines.length" class="inspector-gate-card" data-test="inspector-gate-card" :data-shape="shape">
    <div v-if="shape === 'gate'" class="inspector-gate-result" data-test="gate-result" :aria-busy="decision.busy.value !== null">
      <strong>{{ t(k('slot.result')) }}</strong>
      <span class="inspector-gate-result-value" :data-tone="decision.result.value.tone" :title="decision.result.value.description" :aria-label="[t(k(`result.${decision.result.value.key}`)), decision.result.value.description].filter(Boolean).join(' · ')" data-test="gate-result-value">
        <i aria-hidden="true" />{{ t(k(`result.${decision.result.value.key}`)) }}
      </span>
      <span v-if="decision.result.value.archived" class="inspector-gate-archive" data-test="gate-archived">{{ t(k('result.archived')) }}</span>
      <GateActions :target="target" :state="decision" />
    </div>
    <GateSettingsSummary v-if="shape === 'gate'" :node="decision.settings.value" :decision="decision.receipt.value" :error-code="decision.errorCode.value" />
    <InspectorSlotList v-if="shape !== 'gate'" :slots="lines" :editable="!target.readOnly" @open="open" @edit="edit" @history="history" @action="action">
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
import { gateCardShape, gateCardSlots, type GateSlot } from './gateCardModel'
import GateActions from './GateActions.vue'
import GateSettingsSummary from './GateSettingsSummary.vue'
import { useGateDecision } from './useGateDecision'
import { useGateCard } from './useGateCard'

const props = defineProps<{
  target: InspectorTarget
}>()

const { t, te } = useI18n()

const host = inject(INSPECTOR_HOST, null)

const k = (key: string) => `gui.inspector.gateCard.${key}`

const shape = computed(() => gateCardShape(props.target))
const decision = useGateDecision(() => shape.value === 'gate' ? props.target : null)

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
    // flowgate.v02.0088 F14/F15: the kind's key travels with the line (see openCardLineAction).
    nameKey: k(`slot.${slot.key}`),
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

/**
 * flowgate.v02.0056 F2-2: a row action of a card line — [작성] opens the line's write window, [AI 호출] the
 * document's AI call window (openCardLineAction; the Canvas is read again after them).
 */
function action(line: InspectorSlotView, key: SlotActionKey) {
  openCardLineAction(props.target, line, key, host)
}
</script>

<style scoped>
.inspector-gate-card {
  min-width: 0;
}
.inspector-gate-result { display: flex; align-items: center; flex-wrap: wrap; gap: 16px; min-height: 54px; box-sizing: border-box; border: 1px dashed #28425c; border-radius: 9px; padding: 10px 14px; background: #091a2b; font-size: 12px; }
.inspector-gate-result strong { min-width: 116px; color: #b8c8db; }
.inspector-gate-result-value { display: inline-flex; align-items: center; gap: 7px; color: #8ea4bc; }
.inspector-gate-result-value i { width: 7px; height: 7px; border-radius: 50%; background: #5c7491; }
.inspector-gate-result-value[data-tone="success"] i { background: #19b68e; }
.inspector-gate-result-value[data-tone="danger"] i { background: #e56b82; }
.inspector-gate-archive { padding: 2px 8px; border: 1px solid #28635b; border-radius: 5px; background: #163f37; color: #88d8c2; font-size: 11px; }
</style>
