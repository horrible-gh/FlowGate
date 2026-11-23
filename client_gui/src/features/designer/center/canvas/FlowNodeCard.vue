<template>
  <!-- FlowGate Node card (D, look of MirageGlass 03-designer-shell):
       a fixed 176x108 card in a kind-colored frame. The head row holds the kind icon box, the kind
       name and the state; the body holds the one-line title. The handles give Edges an anchor
       (target on the left, source on the right). the earlier change S3: on a node of an editable
       draft (`data.connectable`) they are shown and a connection can be dragged from / to them;
       otherwise they stay invisible and never connectable. `data.issue` marks a node the last
       validation reported. flowgate.v02.0045 T0019: a 설계 card is named like its Palette card.
       no handle dots. On a connectable card a connection ends
       anywhere on the target card — the target handle covers the whole card and takes the pointer
       only while a connection is being dragged (FlowCanvas `is-connecting`). A 설계 card shows its
       document type's glyph. the earlier implementation (rejection item 3): a connection starts from any side —
       four start strips run along the card's top, right, bottom and left edges (one source handle
       each); the Edge itself is drawn between the facing sides (FlowEdgePath / edgeGeometry.ts).
       A card the last validation reported names the problem on hover (`data.issueText`).
       A Graph card with a run shows a state icon and tooltip (working / awaiting approval / approved);
       pending has no mark. Other states keep their labels. A card waiting for
       its approval offers [승인] in its footer (`data.approvable`; an `approve` event, never the selection or a
       drag). A done card stays 승인됨 while the run is stopped: the waiting successors are the run band's.
       flowgate.v02.0061 T#14: the card attempt's result integration when it needs a look (병합 대기 ·
       병합 중 · 병합 충돌 · 병합 실패) only. 병합 충돌 is a button: it opens the Git tool panel, where the Flow's
       conflict session is resolved (a `conflict` event, never the selection or a drag).
       a held card shows the Hold mark (an open palm in a round frame) at the end of its head row,
       with its tooltip / accessible name. While held, the state text and duplicate Hold chip are hidden.
       a card whose document holds a question still unanswered shows the [Q] mark in its head row.
       (B1): the card only shows its title (the session's, one line) — a click on it is the card's
       selection as anywhere else on the card. The title is edited in the Inspector header (InspectorHeader).
       (mockup v4): every card is exactly 176x108, whatever it shows, so cards line up and the
       Edges meet them evenly. The head row carries the integration states one only looks at (병합 대기 · 병합 중 ·
       병합 실패) and 미인계 as 18px icons next to the state / Q / Hold marks. The footer is the card's buttons only, all
       icon buttons on one line: its documents on the left; 병합 충돌 and [승인] at the right end. Every icon names
       itself on hover and to assistive technology.
       flowgate.v02test.0063 (#38, mockup v19 B): the footer is a 26px strip added below the content and always drawn,
       also on a card with nothing to press: head 32 + title 47 + footer 26 + border 3 = 108. The title stays where it
       was (same top padding); the buttons are 20x20 with a 13px icon. -->
  <div
    class="flow-node-card"
    :class="{ 'is-selected': data.selected, 'is-unknown': kind.key === 'unknown', 'has-issue': data.issue === true, 'has-conflict': data.integration === 'conflict' }"
    :style="{ '--flow-node-accent': kind.accent }"
    data-test="designer-canvas-node"
    :data-node-id="id"
    :data-kind="kind.key"
    :data-connectable="data.connectable ? 'true' : 'false'"
    :data-issue="data.issue ? 'true' : 'false'"
    :title="data.issue && data.issueText ? data.issueText : undefined"
  >
    <Handle
      type="target"
      :position="Position.Left"
      :connectable="data.connectable === true"
      class="flow-node-handle"
      :class="{ 'is-connectable': data.connectable, 'flow-node-drop': data.connectable }"
    />
    <div class="flow-node-head">
      <span class="flow-node-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24">
          <path :d="kind.icon" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </span>
      <span class="flow-node-kind" data-test="designer-canvas-node-kind">{{ t(kindLabelKey) }}</span>
      <!-- flowgate.v02test.0077: the card number right after the kind name (`작업 #3`); its hover names the short id. -->
      <span v-if="data.cardLocal" class="flow-node-no" :title="data.cardShort" data-test="designer-canvas-node-no">{{ data.cardLocal }}</span>
      <span v-if="!data.hold && headerState && headerGlyph" class="flow-node-state is-card is-icon"
        :class="`flow-node-state--${headerGlyph!.tone}`" role="img" :aria-label="headerTitle"
        :title="headerTitle" :data-test="data.gateState ? 'designer-canvas-node-gate-state' : data.cardState ? 'designer-canvas-node-card-state' : 'designer-canvas-node-state'"
        :data-state="headerState.key" :data-phase="data.cardState?.phase">
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path :d="headerIcon" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </span>
      <span v-if="data.bodyUnreadable" class="flow-node-body-warning" role="img" :aria-label="t('gui.card.bodyUnreadable')" :title="t('gui.card.bodyUnreadable')" data-test="designer-canvas-node-body-warning">⚠</span>
      <span
        v-if="data.question"
        class="flow-node-question"
        role="img"
        :aria-label="questionTitle"
        :title="questionTitle"
        data-test="designer-canvas-node-question-icon"
      >Q</span>
      <!-- 병합 대기 / 병합 중 / 병합 충돌 / 병합 실패: shown only, never pressed (병합 충돌 is also the footer's button).
           flowgate.v02test.0042 (시안 v7): the conflicting card carries the conflict mark in its head too. -->
      <span
        v-if="data.integration"
        class="flow-node-mark"
        :class="[`flow-node-mark--${integrationTone}`, { 'is-spinning': data.integration === 'integrating' }]"
        role="img"
        :aria-label="integrationTitle"
        :title="integrationTitle"
        data-test="designer-canvas-node-integration"
        :data-integration="data.integration"
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path :d="integrationIcon" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
        <span class="flow-node-sr">{{ integrationLabel }}</span>
      </span>
      <!-- v02gui.0015 T2: an earlier attempt's uncommitted changes this attempt did not take (kept, not carried). -->
      <span
        v-if="data.uncarried"
        class="flow-node-mark flow-node-mark--warning"
        role="img"
        :aria-label="uncarriedTitle"
        :title="uncarriedTitle"
        data-test="designer-canvas-node-uncarried"
        :data-files="data.uncarried.files"
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path :d="CARD_MARK_ICONS.uncarried" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
        <span class="flow-node-sr">{{ uncarriedLabel }}</span>
      </span>
      <span
        v-if="data.hold"
        class="flow-node-hold"
        role="img"
        :aria-label="t('gui.designer.center.hold_icon')"
        :title="t('gui.designer.center.hold_icon')"
        data-test="designer-canvas-node-hold-icon"
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path :d="FLOW_NODE_HOLD_ICON" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </span>
    </div>
    <div class="flow-node-body">
      <p
        v-if="showTitle"
        class="flow-node-title"
        :title="data.cardShort ? `${data.cardShort} · ${data.title}` : data.title"
        data-test="designer-canvas-node-title"
      >{{ data.title }}</p>
    </div>
    <div class="flow-node-footer" data-test="designer-canvas-node-footer">
      <div class="flow-node-documents">
        <button v-for="doc in data.documents" :key="cardDocumentButtonKey(doc)" type="button"
          class="flow-node-document nodrag nopan" :class="{ 'is-empty': doc.empty }"
          :title="documentLabel(doc)" :aria-label="documentLabel(doc)"
          data-test="designer-canvas-node-document" :data-doc-id="doc.docId" :data-report="doc.report" :data-scenario="doc.scenario ? 'true' : 'false'"
          :data-empty="doc.empty ? 'true' : 'false'"
          @pointerdown.stop @mousedown.stop @dblclick.stop @keydown.stop @keyup.stop
          @click.stop="emit('open-document', doc.scenario ? { nodeId: id, docId: doc.docId, scenario: true } : { nodeId: id, docId: doc.docId })">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path
            :d="documentIcon(doc)"
            fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" /></svg>
        </button>
      </div>
      <!-- flowgate.v02.0061 T#14: 병합 충돌 (a `conflict` event, never the selection or a drag). flowgate.v02test.0042: it opens
           the card's '병합 · 충돌' Inspector tab (DesignerCenter), no longer the Git tool panel; same place and size. -->
      <button v-if="showConflict" type="button"
        class="flow-node-action flow-node-conflict nodrag nopan" :title="conflictTitle" :aria-label="conflictTitle"
        data-test="designer-canvas-node-conflict"
        @pointerdown.stop @mousedown.stop @dblclick.stop @click.stop="emit('conflict', id)">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path :d="CARD_MARK_ICONS.conflict"
          fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" /></svg>
      </button>
      <button v-if="data.cancelable" type="button" class="flow-node-action flow-node-cancel nodrag nopan"
        :disabled="data.approving || data.approvalBlocked || !data.gateAttemptId" :title="t('gui.inspector.gateCard.cancel')" :aria-label="t('gui.inspector.gateCard.cancel')"
        data-test="designer-canvas-node-cancel" @pointerdown.stop @mousedown.stop @dblclick.stop @click.stop="cancelAttempt = data.gateAttemptId ?? null">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" /></svg>
      </button>
      <ConfirmDialog :open="cancelAttempt !== null" :title="t('gui.inspector.gateCard.cancel_dialog.title')" :message="t('gui.inspector.gateCard.cancel_dialog.message')"
        :confirm-label="t('gui.inspector.gateCard.cancel')" :cancel-label="t('gui.inspector.gateCard.cancel_dialog.keep')"
        @cancel="cancelAttempt = null" @confirm="confirmCancel" />
      <button v-if="showApprove" type="button"
        class="flow-node-action flow-node-approve nodrag nopan" :disabled="data.approving || data.approvalBlocked"
        :title="approveTitle" :aria-label="approveTitle"
        data-test="designer-canvas-node-approve" @pointerdown.stop @mousedown.stop @dblclick.stop @click.stop="emit('approve', id)">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path :d="CARD_MARK_ICONS.approve"
          fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" /></svg>
      </button>
    </div>
    <Handle
      v-for="side in LINK_SIDES"
      :id="side.id"
      :key="side.id"
      type="source"
      :position="side.position"
      :connectable="data.connectable === true"
      class="flow-node-handle"
      :class="{ 'is-connectable': data.connectable, 'flow-node-link': data.connectable, [`flow-node-link-${side.id}`]: data.connectable }"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import ConfirmDialog from '@/components/ui/ConfirmDialog.vue'
import { useI18n } from 'vue-i18n'
import { Handle, Position } from '@vue-flow/core'
import { FLOW_NODE_HOLD_ICON, flowNodeKindView, flowNodeStateDisplay, flowNodeHeaderIcon } from './flowNodeKinds'
import type { FlowGateNodeData } from './vueFlowMapping'
import { cardDocumentButtonKey, type CardDocumentButton, type CardDocumentOpen } from './cardDocumentButtons'
import { cardStateReasonText } from '../graph/cardStateView'

const props = defineProps<{
  id: string
  data: FlowGateNodeData
}>()

const emit = defineEmits<{
  /** flowgate.v02.0061 T#14: the card's 병합 충돌 was pressed (the Git tool panel resolves it). */
  conflict: [nodeId: string]
  approve: [nodeId: string]
  cancel: [nodeId: string, attemptId: string]
  'open-document': [document: CardDocumentOpen]
}>()

const cancelAttempt = ref<string | null>(null)
watch(() => `${props.id}/${props.data.gateAttemptId}/${props.data.cancelable}`, () => { cancelAttempt.value = null })
function confirmCancel() {
  const attempt = cancelAttempt.value
  cancelAttempt.value = null
  if (attempt && attempt === props.data.gateAttemptId && props.data.cancelable && !props.data.approving) emit('cancel', props.id, attempt)
}

/** The four start strips of a connectable card, one source handle per side. */
const LINK_SIDES = [
  { id: 'top', position: Position.Top },
  { id: 'right', position: Position.Right },
  { id: 'bottom', position: Position.Bottom },
  { id: 'left', position: Position.Left },
] as const

/** Card document buttons: same rounded, stroke-2 glyph family as the head row's kind icons. */
const CARD_DOCUMENT_ICONS = {
  instruction: 'M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z M14 3v5h5 M9 13h6 M9 17h4',
  report: 'M9 4H7a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2h-2 M9 3h6v3H9z M9 14l2 2l4-4',
  // flowgate.v02test.0041: the test card's 시나리오 — a checkbox list, apart from the report's clipboard with a check.
  scenario: 'M3.5 6.5l1.5 1.5l3-3 M3.5 14.5l1.5 1.5l3-3 M12 7h8 M12 15h8 M12 19h5',
} as const
function documentIcon(doc: CardDocumentButton): string {
  return doc.scenario ? CARD_DOCUMENT_ICONS.scenario : doc.report ? CARD_DOCUMENT_ICONS.report : CARD_DOCUMENT_ICONS.instruction
}

/** flowgate.v02test.0044: the integration / Not handed over marks and the footer's action glyphs (mockup v4). 병합 실패 is a merge
 * with a cross — never the card state 오류's circled cross, so the two stay apart. */
const CARD_MARK_ICONS = {
  // (mockup v7): a waiting merge is an hourglass; its tooltip names the card it waits for.
  pending: 'M6 3h12 M6 21h12 M7 3c0 5 10 6 10 9s-10 4-10 9 M17 3c0 5-10 6-10 9s10 4 10 9',
  integrating: 'M21 12a9 9 0 1 1-3-6.7 M21 4v5h-5',
  failed: 'M5 3v4l6 6v8 M17 3v4l-6 6 M15 16l5 5 M20 16l-5 5',
  conflict: 'M6 3v5l12 8v5 M18 3v5L6 16v5 M3 3h6 M15 3h6',
  uncarried: 'M3 13h5l1.5 3h5L16 13h5 M5 13l2-8h10l2 8v6H5z M4 4l16 16',
  approve: 'M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18z M8.5 12l2.5 2.5l4.5-5',
} as const

const { t } = useI18n()
const questionTitle = computed(() => (props.data.questionWait ?? []).map(item => {
  const state = ['pending', 'dispatched', 'failed', 'user_decision'].includes(item.state) ? item.state : 'pending'
  return [`Q${item.seq}`, t(`gui.inspector.qna.wait_${state}`), item.code, item.message].filter(Boolean).join(' · ')
}).join('\\n') || t('gui.inspector.qna.wait_pending'))

function documentLabel(doc: CardDocumentButton): string {
  const kind = doc.scenario ? t('gui.designer.center.card_document_scenario') : doc.docType ?? t('gui.designer.center.document')
  const label = t('gui.designer.center.open_card_document', { kind, title: doc.title })
  return doc.empty ? t('gui.designer.center.card_document_empty', { label }) : label
}

// flowgate.v02.0070 T#4 (B4): the same kind display as the Inspector header (a B root is a 버그).
const kind = computed(() => flowNodeKindView(props.data.kind, props.data.docType))
const kindLabelKey = computed(() => kind.value.labelKey)
const state = computed(() => flowNodeStateDisplay(props.data.state))

const headerState = computed(() => props.data.gateState ?? (props.data.cardState ? props.data.cardState.key === 'pending' ? null : props.data.cardState : state.value ? {
  ...state.value, labelKey: `gui.designer.center.node_states.${state.value.key}`, reason: null,
} : null))
const headerGlyph = computed(() => flowNodeHeaderIcon(headerState.value))
const headerIcon = computed(() => headerGlyph.value?.path)
const headerTitle = computed(() => {
  const shown = headerState.value
  if (!shown) return undefined
  const label = t(shown.labelKey)
  // a failed AI call's short cause is a sentence of its own, worded as the Inspector words it
  // (cardStateReasonText); any other reason as sent.
  const reason = cardStateReasonText(shown, t)
  return reason ? `${label} (${reason})` : label
})

/** v02gui.0015 T2: the Not handed over mark's name and its hover sentence (a count that could not be read is said so). */
const uncarriedLabel = computed(() => {
  const value = props.data.uncarried
  if (!value) return ''
  return value.unknown && value.files === 0
    ? t('gui.designer.center.uncarried.flag_unknown')
    : t('gui.designer.center.uncarried.flag', { files: value.files })
})
const uncarriedTitle = computed(() => {
  const value = props.data.uncarried
  if (!value) return undefined
  const base = t('gui.designer.center.uncarried.title', { attempts: value.attempts, files: value.files })
  const sentence = value.unknown ? `${base} ${t('gui.designer.center.uncarried.unknown')}` : base
  return `${uncarriedLabel.value} · ${sentence}`
})

/** The head row's integration mark (병합 충돌 is the footer's button instead). */
const integrationLabel = computed(() => props.data.integration ? t(`gui.designer.center.integration.${props.data.integration}`) : '')
const integrationTitle = computed(() => {
  if (props.data.verificationFailed) return `${integrationLabel.value} · ${t('gui.designer.center.integration.verification_failed')}`
  // a waiting merge says what it waits for — the named card's conflict, else its turn.
  if (props.data.integration === 'pending') {
    return props.data.integrationBlockedTitle
      ? t(props.data.integrationBlockedConflict ? 'gui.designer.center.integration.waiting_for' : 'gui.designer.center.integration.waiting_behind', { title: props.data.integrationBlockedTitle })
      : t('gui.designer.center.integration.waiting_turn')
  }
  if (props.data.integration === 'conflict') return conflictTitle.value
  return integrationLabel.value
})
const integrationTone = computed(() => props.data.integration === 'failed' || props.data.integration === 'conflict' ? 'danger' : 'info')
const integrationIcon = computed(() => props.data.integration === 'failed' ? CARD_MARK_ICONS.failed
  : props.data.integration === 'conflict' ? CARD_MARK_ICONS.conflict
    : props.data.integration === 'integrating' ? CARD_MARK_ICONS.integrating : CARD_MARK_ICONS.pending)

const showConflict = computed(() => props.data.integration === 'conflict')
const conflictTitle = computed(() => `${t('gui.designer.center.integration.conflict')} · ${t('gui.designer.center.integration.open_conflict')}`)
const showApprove = computed(() => props.data.gateState?.key === 'waiting_confirm' && props.data.approvable === true)
const approveTitle = computed(() => {
  const label = t('gui.inspector.gateCard.confirm')
  return props.data.approvalReason ? `${label} · ${t(props.data.approvalReason)}` : label
})

/** The title is shown unless it only repeats the kind name of the head row. */
const showTitle = computed(() => props.data.title.trim() !== t(kindLabelKey.value).trim())
</script>

<style scoped>
.flow-node-body-warning { color: var(--fg2-status-warning-fg); margin-left: 4px; }

/* a fixed 176x108 whatever the card shows (Edges are drawn from the measured size). */
.flow-node-card {
  position: relative;
  box-sizing: border-box;
  width: 176px;
  height: 108px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border: 1.5px solid var(--flow-node-accent);
  border-radius: 10px;
  background: linear-gradient(145deg, #13243a, #0f1e31);
  box-shadow: 0 8px 22px rgba(0, 0, 0, 0.47);
  color: var(--fg2-text);
  font-family: var(--fg2-font);
  cursor: pointer;
  transition: box-shadow 0.16s ease;
}

.flow-node-card:hover:not(.is-selected) {
  box-shadow:
    0 0 0 2px color-mix(in srgb, var(--flow-node-accent) 25%, transparent),
    0 8px 22px rgba(0, 0, 0, 0.53);
}

/* (mockup v7): a card whose merge conflicts waits for a person — a red ring, also when selected. */
.flow-node-card.has-conflict {
  box-shadow:
    0 0 0 3px color-mix(in srgb, var(--fg2-status-danger-fg) 35%, transparent),
    0 8px 22px rgba(0, 0, 0, 0.47);
}

.flow-node-card.is-selected:not(.has-conflict) {
  box-shadow:
    0 0 0 1px color-mix(in srgb, var(--flow-node-accent) 40%, transparent),
    0 8px 22px rgba(0, 0, 0, 0.53);
}

.flow-node-head {
  display: flex;
  align-items: center;
  box-sizing: border-box;
  height: 32px;
  min-width: 0;
  padding: 0 10px;
  border-bottom: 1px solid rgba(128, 160, 199, 0.18);
  background: color-mix(in srgb, var(--flow-node-accent) 11%, transparent);
}

.flow-node-icon {
  display: grid;
  flex: 0 0 24px;
  place-items: center;
  width: 24px;
  height: 24px;
  border-radius: 6px;
  background: color-mix(in srgb, var(--flow-node-accent) 88%, #0a1422);
  color: #fff;
}

.flow-node-icon svg {
  display: block;
  width: 15px;
  height: 15px;
}

/* When the head row runs short the state shrinks first; the kind name keeps its place. */
.flow-node-kind {
  flex: 0 1 auto;
  min-width: 0;
  margin-left: 7px;
  overflow: hidden;
  color: #c8d7e7;
  font-size: 10.5px;
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* flowgate.v02test.0077: the card number after the kind name — a small monospace chip in the card's accent; it keeps its
   width (the kind name and the state shrink first). */
.flow-node-no {
  flex: 0 0 auto;
  margin-left: 6px;
  padding: 0 5px;
  border: 1px solid color-mix(in srgb, var(--flow-node-accent) 55%, transparent);
  border-radius: 4px;
  background: color-mix(in srgb, var(--flow-node-accent) 22%, #0a1422);
  color: color-mix(in srgb, var(--flow-node-accent) 35%, #fff);
  font-family: ui-monospace, Consolas, monospace;
  font-size: 10.5px;
  font-weight: 500;
  line-height: 16px;
  white-space: nowrap;
}

.flow-node-state {
  flex: 0 100 auto;
  min-width: 0;
  margin-left: auto;
  padding-left: 6px;
  overflow: hidden;
  color: #8fa7bf;
  font-size: 9px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.flow-node-state.is-icon {
  position: relative;
  z-index: 4;
  display: grid;
  flex: 0 0 18px;
  place-items: center;
  width: 18px;
  height: 18px;
  padding-left: 0;
}

.flow-node-state.is-icon svg {
  display: block;
  width: 16px;
  height: 16px;
}

.flow-node-state.is-icon.flow-node-state--success {
  color: #22c98c;
  opacity: 1;
}

.flow-node-state .is-spinning {
  animation: flow-node-state-spin 1s linear infinite;
}

@keyframes flow-node-state-spin {
  to { transform: rotate(360deg); }
}

/* the Hold mark — the warning tokens, at the end of the head row (after the state, if any). */
.flow-node-hold {
  display: grid;
  flex: 0 0 18px;
  place-items: center;
  width: 18px;
  height: 18px;
  margin-left: auto;
  border-radius: 50%;
  background: var(--fg2-status-warning-bg);
  color: var(--fg2-status-warning-fg);
}

.flow-node-hold svg {
  display: block;
  width: 16px;
  height: 16px;
}

/* the [Q] mark — the info tokens, before the Hold mark (after the state, if any). */
.flow-node-question {
  display: grid;
  flex: 0 0 18px;
  place-items: center;
  width: 18px;
  height: 18px;
  margin-left: auto;
  border-radius: 4px;
  background: var(--fg2-status-info-bg);
  color: var(--fg2-status-info-fg);
  font-size: 11px;
  font-weight: 800;
  line-height: 1;
}

.flow-node-state + .flow-node-question,
.flow-node-state + .flow-node-mark,
.flow-node-question + .flow-node-mark,
.flow-node-mark + .flow-node-mark,
.flow-node-question + .flow-node-hold,
.flow-node-mark + .flow-node-hold {
  margin-left: 4px;
}

/* flowgate.v02test.0044: 병합 대기 · 병합 중 · 병합 실패 and Not handed over — the head row's 18px box / 16px glyph, shown only. */
.flow-node-mark {
  position: relative;
  display: grid;
  flex: 0 0 18px;
  place-items: center;
  width: 18px;
  height: 18px;
  margin-left: auto;
}
.flow-node-mark svg { display: block; width: 16px; height: 16px; }
.flow-node-mark--info { color: var(--fg2-status-info-fg); }
.flow-node-mark--warning { color: var(--fg2-status-warning-fg); }
.flow-node-mark--danger { color: var(--fg2-status-danger-fg); }
.flow-node-mark.is-spinning svg { animation: flow-node-state-spin 1s linear infinite; }

/* The name an icon carries for assistive technology (its hover text is the title). */
.flow-node-sr {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}

/* flowgate.v02test.0063 (#38 B): a 26px strip below the content, always there — the head row's 1px line on top
   (inside the 26px), a little darker than the card. */
.flow-node-footer {
  display: flex;
  flex: none;
  align-items: center;
  box-sizing: border-box;
  gap: 4px;
  height: 26px;
  padding: 0 6px 0 8px;
  border-top: 1px solid rgba(128, 160, 199, 0.18);
  background: rgba(4, 10, 18, 0.35);
}
/* One line only: the card's height is fixed, a second row would be cut off. */
.flow-node-documents { display: flex; flex: 1; flex-wrap: nowrap; gap: 4px; min-width: 0; overflow: hidden; }
/* Tinted with the card's kind color, the same rounded box as the head row's kind icon (quieter). */
.flow-node-document {
  position: relative; z-index: 4; display: grid; place-items: center;
  box-sizing: border-box; width: 20px; height: 20px; padding: 0;
  border: 1px solid color-mix(in srgb, var(--flow-node-accent) 38%, transparent);
  border-radius: 5px; background: color-mix(in srgb, var(--flow-node-accent) 14%, transparent);
  flex: none; color: color-mix(in srgb, var(--flow-node-accent) 45%, #fff); cursor: pointer;
  transition: background 0.14s ease, border-color 0.14s ease, color 0.14s ease;
}
.flow-node-document svg { display: block; width: 13px; height: 13px; }
.flow-node-document:hover {
  border-color: color-mix(in srgb, var(--flow-node-accent) 75%, transparent);
  background: color-mix(in srgb, var(--flow-node-accent) 30%, transparent);
  color: #fff;
}
.flow-node-document:focus-visible { outline: 2px solid var(--fg2-focus); outline-offset: 1px; }
/* a document without a body yet looks disabled (dashed, faded) but still opens on a press. */
.flow-node-document.is-empty {
  border-style: dashed;
  border-color: color-mix(in srgb, var(--flow-node-accent) 30%, transparent);
  background: transparent;
  color: color-mix(in srgb, var(--flow-node-accent) 30%, #8a8f98);
  opacity: 0.55;
}
.flow-node-document.is-empty:hover {
  opacity: 0.85;
  border-color: color-mix(in srgb, var(--flow-node-accent) 55%, transparent);
  background: color-mix(in srgb, var(--flow-node-accent) 10%, transparent);
  color: color-mix(in srgb, var(--flow-node-accent) 45%, #fff);
}

/* flowgate.v02test.0063: the title cell is a fixed 47px; the title keeps the top padding it had on the 92px card. */
.flow-node-body {
  display: flex;
  flex: none;
  flex-direction: column;
  align-items: flex-start;
  box-sizing: border-box;
  gap: 5px;
  height: 47px;
  padding: 7px 11px 0;
}

/* flowgate.v02.0056 F2-1: the card state colours are the status tokens (승인 대기 stands out, 승인됨 is quiet). */
.flow-node-state.is-card.flow-node-state--info {
  color: var(--fg2-status-info-fg);
}

.flow-node-state.is-card.flow-node-state--warning {
  color: var(--fg2-status-warning-fg);
  font-weight: 700;
}

.flow-node-state.is-card.flow-node-state--danger {
  color: var(--fg2-status-danger-fg);
}

/* flowgate.v02test.0044: the footer's action buttons (병합 충돌, [승인]) — 20x20 icon buttons like the document buttons,
   at the right end; above the connection strips (z-index 2) so a press reaches them. */
.flow-node-action {
  position: relative;
  z-index: 4;
  display: grid;
  flex: none;
  place-items: center;
  box-sizing: border-box;
  width: 20px;
  height: 20px;
  padding: 0;
  border: 1px solid;
  border-radius: 5px;
  font: inherit;
  cursor: pointer;
  transition: background 0.14s ease, filter 0.14s ease;
}
.flow-node-action svg { display: block; width: 13px; height: 13px; }
.flow-node-action:focus-visible { outline: 2px solid var(--fg2-focus); outline-offset: 1px; }

.flow-node-conflict {
  border-color: var(--fg2-status-danger-border);
  background: var(--fg2-status-danger-bg);
  color: var(--fg2-status-danger-fg);
}
.flow-node-conflict:hover { background: color-mix(in srgb, var(--fg2-status-danger-fg) 26%, transparent); }

/* flowgate.v02test.0064 #22/#29: [취소] is a red outline X — colored so it reads as "stop this run", but unfilled so it
   stays lighter than the filled conflict mark and the amber approve button; it fills lightly on hover. */
.flow-node-cancel {
  border-color: var(--fg2-status-danger-border);
  background: transparent;
  color: var(--fg2-status-danger-fg);
}
.flow-node-cancel:hover:not(:disabled) { border-color: var(--fg2-status-danger-fg); background: var(--fg2-status-danger-bg); }
.flow-node-cancel:disabled { cursor: not-allowed; opacity: var(--fg2-control-disabled-opacity); }

.flow-node-approve {
  border-color: var(--fg2-status-warning-border);
  background: var(--fg2-status-warning-bg);
  color: var(--fg2-status-warning-fg);
}
.flow-node-approve:hover:not(:disabled) { filter: brightness(1.25); }
.flow-node-approve:disabled {
  cursor: progress;
  opacity: var(--fg2-control-disabled-opacity);
}

.flow-node-title {
  align-self: stretch;
  margin: 0;
  overflow: hidden;
  font-size: 12px;
  font-weight: 680;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Edge anchors only: invisible and inert. */
.flow-node-card :deep(.flow-node-handle) {
  width: 1px;
  min-width: 0;
  height: 1px;
  min-height: 0;
  border: 0;
  background: transparent;
  opacity: 0;
  pointer-events: none;
}

/* : a draft node's connection areas, no dots.
   Start: a strip along each of the four sides (inside the card — the card clips what sticks
   out). Hovering the card shows the strips as a soft frame in the card color; the cursor turns to
   a crosshair over them. The middle of the card still drags the card.
   End: the target handle covers the whole card and stays out of the pointer's way until a
   connection is dragged (FlowCanvas). Where the drawn Edge attaches is not these handles' business
   (FlowEdgePath picks the facing sides). */
.flow-node-card :deep(.flow-node-handle.is-connectable) {
  min-width: 0;
  min-height: 0;
  border: 0;
  border-radius: 0;
  background: transparent;
  opacity: 1;
  transform: none;
}

.flow-node-card :deep(.flow-node-link) {
  z-index: 2;
  pointer-events: all;
  cursor: crosshair;
  transition: background 0.14s ease, box-shadow 0.14s ease;
}

.flow-node-card :deep(.flow-node-link-top),
.flow-node-card :deep(.flow-node-link-bottom) {
  right: auto;
  left: 0;
  width: 100%;
  height: 14px;
}

.flow-node-card :deep(.flow-node-link-top) {
  top: 0;
  bottom: auto;
}

.flow-node-card :deep(.flow-node-link-bottom) {
  top: auto;
  bottom: 0;
}

.flow-node-card :deep(.flow-node-link-left),
.flow-node-card :deep(.flow-node-link-right) {
  top: 0;
  bottom: auto;
  width: 24px;
  height: 100%;
}

.flow-node-card :deep(.flow-node-link-left) {
  right: auto;
  left: 0;
}

.flow-node-card :deep(.flow-node-link-right) {
  right: 0;
  left: auto;
}

.flow-node-card[data-connectable='true']:hover :deep(.flow-node-link-top) {
  background: linear-gradient(to top, transparent, color-mix(in srgb, var(--flow-node-accent) 16%, transparent));
  box-shadow: inset 0 2px 0 color-mix(in srgb, var(--flow-node-accent) 70%, transparent);
}

.flow-node-card[data-connectable='true']:hover :deep(.flow-node-link-bottom) {
  background: linear-gradient(to bottom, transparent, color-mix(in srgb, var(--flow-node-accent) 16%, transparent));
  box-shadow: inset 0 -2px 0 color-mix(in srgb, var(--flow-node-accent) 70%, transparent);
}

.flow-node-card[data-connectable='true']:hover :deep(.flow-node-link-left) {
  background: linear-gradient(to left, transparent, color-mix(in srgb, var(--flow-node-accent) 16%, transparent));
  box-shadow: inset 2px 0 0 color-mix(in srgb, var(--flow-node-accent) 70%, transparent);
}

.flow-node-card[data-connectable='true']:hover :deep(.flow-node-link-right) {
  background: linear-gradient(to right, transparent, color-mix(in srgb, var(--flow-node-accent) 16%, transparent));
  box-shadow: inset -2px 0 0 color-mix(in srgb, var(--flow-node-accent) 70%, transparent);
}

.flow-node-card :deep(.flow-node-link:hover) {
  background-color: color-mix(in srgb, var(--flow-node-accent) 22%, transparent);
}

.flow-node-card :deep(.flow-node-drop) {
  top: 0;
  bottom: auto;
  left: 0;
  z-index: 3;
  width: 100%;
  height: 100%;
  pointer-events: none;
}

.flow-node-card.has-issue {
  outline: 2px solid #ff9d51;
  outline-offset: 2px;
}
</style>
