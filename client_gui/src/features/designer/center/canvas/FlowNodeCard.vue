<template>
  <!-- flowgate.v02.0026 — FlowGate Node card (0003-D §6.3, look of MirageGlass 03-designer-shell):
       a fixed 176x92 card in a kind-colored frame. The head row holds the kind icon box, the kind
       name and the state; the body holds the one-line title. The handles give Edges an anchor
       (target on the left, source on the right). flowgate.v02.0045 S3: on a node of an editable
       draft (`data.connectable`) they are shown and a connection can be dragged from / to them;
       otherwise they stay invisible and never connectable. `data.issue` marks a node the last
       validation reported. flowgate.v02.0045 T0019: a 설계 card is named like its Palette card.
       flowgate.v02.0045 TR0020 rev2: no handle dots. On a connectable card a connection ends
       anywhere on the target card — the target handle covers the whole card and takes the pointer
       only while a connection is being dragged (FlowCanvas `is-connecting`). A 설계 card shows its
       document type's glyph. TR0020 rev4 (rejection item 3): a connection starts from any side —
       four start strips run along the card's top, right, bottom and left edges (one source handle
       each); the Edge itself is drawn between the facing sides (FlowEdgePath / edgeGeometry.ts).
       A card the last validation reported names the problem on hover (`data.issueText`).
       A Graph card with a run shows a state icon and tooltip (working / awaiting approval / approved);
       pending has no mark. Other states keep their labels. A card waiting for
       its approval offers [승인] under its title (`data.approvable`; an `approve` event, never the selection or a
       drag). A done card stays 승인됨 while the run is stopped: the waiting successors are the run band's.
       flowgate.v02.0061 T#14: under the title, the card attempt's result integration when it needs a look (병합 대기 ·
       병합 중 · 병합 충돌 · 병합 실패) only. 병합 충돌 is a button: it opens the Git tool panel, where the Flow's
       conflict session is resolved (a `conflict` event, never the selection or a drag).
       flowgate.v02.0061 T#15: a held card shows the Hold mark (an open palm in a round frame) at the end of its head row,
       with its tooltip / accessible name. While held, the state text and duplicate Hold chip are hidden.
       flowgate.v02.0063: a card whose document holds a question still unanswered shows the [Q] mark in its head row.
       flowgate.v02.0064 T#4 (0062 D0005 §3.2 / §6): a card whose title may be edited now (`data.titleEditable`, with
       `save-title`) opens an inline edit on a click on its title — or on ✎, shown on hover only, also when the title is
       the kind name and so not repeated. The edit is an input with ✓ / ×: Enter or ✓ saves, Esc or × cancels, Enter / Esc
       while an IME composes only end the composition, a click outside cancels; the input and its buttons keep the pointer
       and every key to themselves (no drag, pan, selection, connection, remove or undo) — the whole edit does, so a key on
       ✓ / × (focused by Tab) never reaches the Canvas either; there Enter / Space press the button as usual and Esc
       cancels. Under it, the length (30, as the
       server counts) or why the title was refused; the input stays. A card that starts running meanwhile, or a save the
       server refuses as running (`node_title_locked`), closes the edit with the lock note. Nothing is shown before the
       server answered: the title shown is the session's (its version order). Never while a connection is dragged. -->
  <div
    class="flow-node-card"
    :class="{ 'is-selected': data.selected, 'is-unknown': kind.key === 'unknown', 'has-issue': data.issue === true }"
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
      <span
        v-if="!data.hold && data.cardState && data.cardState.key !== 'pending'"
        class="flow-node-state is-card"
        :class="[`flow-node-state--${data.cardState.tone}`, { 'is-icon': cardStateIcon }]"
        :role="cardStateIcon ? 'img' : undefined"
        :aria-label="cardStateIcon ? t(data.cardState.labelKey) : undefined"
        :title="t(data.cardState.labelKey)"
        data-test="designer-canvas-node-card-state"
        :data-state="data.cardState.key"
        :data-phase="data.cardState.phase"
      >
        <svg v-if="cardStateIcon" viewBox="0 0 24 24" :class="{ 'is-spinning': data.cardState.phase === 'working' }" aria-hidden="true">
          <path :d="cardStateIcon" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
        <template v-else>{{ t(data.cardState.labelKey) }}</template>
      </span>
      <span
        v-else-if="!data.hold && !data.cardState && state"
        class="flow-node-state"
        :class="`flow-node-state--${state.tone}`"
        data-test="designer-canvas-node-state"
      >{{ t(`gui.designer.center.node_states.${state.key}`) }}</span>
      <span v-if="data.bodyUnreadable" class="flow-node-body-warning" role="img" :aria-label="t('gui.card.bodyUnreadable')" :title="t('gui.card.bodyUnreadable')" data-test="designer-canvas-node-body-warning">⚠</span>
      <span
        v-if="data.question"
        class="flow-node-question"
        role="img"
        aria-label="Q"
        data-test="designer-canvas-node-question-icon"
      >Q</span>
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
      <div
        v-if="editing"
        ref="editorEl"
        class="flow-node-title-editor nodrag nopan"
        data-test="designer-canvas-node-title-editor"
        @pointerdown.stop
        @mousedown.stop
        @click.stop
        @dblclick.stop
        @keydown.stop="onEditorKeydown"
        @keyup.stop
      >
        <input
          ref="inputEl"
          v-model="draft"
          type="text"
          class="flow-node-title-input nodrag nopan"
          :aria-label="t('gui.designer.nodeTitle.input')"
          :title="t('gui.designer.nodeTitle.hint')"
          :aria-invalid="error ? 'true' : undefined"
          :disabled="saving"
          data-test="designer-canvas-node-title-input"
          @input="error = null"
          @compositionstart="composing = true"
          @compositionend="composing = false"
        />
        <button
          type="button"
          class="flow-node-title-action nodrag nopan"
          :disabled="saving"
          :aria-label="t('gui.designer.nodeTitle.save')"
          :title="t('gui.designer.nodeTitle.save')"
          data-test="designer-canvas-node-title-save"
          @click.stop="save"
        >✓</button>
        <button
          type="button"
          class="flow-node-title-action nodrag nopan"
          :disabled="saving"
          :aria-label="t('gui.designer.nodeTitle.cancel')"
          :title="t('gui.designer.nodeTitle.cancel')"
          data-test="designer-canvas-node-title-cancel"
          @click.stop="cancel"
        >×</button>
      </div>
      <div v-else-if="showTitle || titleEditable" class="flow-node-title-row">
        <p
          v-if="showTitle"
          class="flow-node-title"
          :class="{ 'is-editable': titleEditable }"
          :title="data.title"
          data-test="designer-canvas-node-title"
          @pointerdown="markPress"
          @click="onTitleClick"
        >{{ data.title }}</p>
        <button
          v-if="titleEditable"
          type="button"
          class="flow-node-title-pen nodrag nopan"
          :aria-label="t('gui.designer.nodeTitle.edit')"
          :title="t('gui.designer.nodeTitle.edit')"
          data-test="designer-canvas-node-title-edit"
          @pointerdown.stop
          @mousedown.stop
          @click.stop="openEdit"
        >✎</button>
      </div>
      <p
        v-if="note"
        class="flow-node-title-note"
        :class="`is-${note.tone}`"
        :role="note.tone === 'hint' ? undefined : note.tone === 'error' ? 'alert' : 'status'"
        data-test="designer-canvas-node-title-note"
        :data-tone="note.tone"
      >{{ note.text }}</p>
      <button
        v-if="data.approvable && !editing"
        type="button"
        class="flow-node-approve nodrag nopan"
        :disabled="data.approving === true"
        :aria-busy="data.approving === true ? 'true' : undefined"
        :title="t('gui.designer.center.approval.hint')"
        data-test="designer-canvas-node-approve"
        @pointerdown.stop
        @mousedown.stop
        @click.stop="emit('approve', id)"
      >{{ t('gui.designer.center.approval.approve') }}</button>
      <div v-if="data.integration && !editing" class="flow-node-flags" data-test="designer-canvas-node-flags">
        <button
          v-if="data.integration === 'conflict'"
          type="button"
          class="flow-node-flag is-conflict nodrag nopan"
          :title="t('gui.designer.center.integration.open_conflict')"
          data-test="designer-canvas-node-conflict"
          @pointerdown.stop
          @mousedown.stop
          @click.stop="emit('conflict', id)"
        >{{ t('gui.designer.center.integration.conflict') }}</button>
        <span
          v-else-if="data.integration"
          class="flow-node-flag"
          :class="`is-${data.integration}`"
          data-test="designer-canvas-node-integration"
          :data-integration="data.integration"
        >{{ t(`gui.designer.center.integration.${data.integration}`) }}</span>
      </div>
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
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Handle, Position } from '@vue-flow/core'
import { NODE_TITLE_MAX_CHARS, checkNodeTitle, type NodeTitleSaveOutcome } from '../graph/nodeTitle'
import { FLOW_NODE_HOLD_ICON, flowNodeKindDisplay, flowNodeKindLabelKey, flowNodeStateDisplay } from './flowNodeKinds'
import type { FlowGateNodeData } from './vueFlowMapping'

const props = withDefaults(defineProps<{
  id: string
  data: FlowGateNodeData
  /** flowgate.v02.0064 T#4: saves this card's title (the session's); null = the title never opens for editing. */
  saveTitle?: ((nodeId: string, title: string) => Promise<NodeTitleSaveOutcome>) | null
  /** flowgate.v02.0064 T#4: a connection is being dragged on the Canvas (no edit opens). */
  connecting?: boolean
}>(), { saveTitle: null, connecting: false })

const emit = defineEmits<{
  approve: [nodeId: string]
  /** flowgate.v02.0061 T#14: the card's 병합 충돌 was pressed (the Git tool panel resolves it). */
  conflict: [nodeId: string]
}>()

/** The four start strips of a connectable card, one source handle per side. */
const LINK_SIDES = [
  { id: 'top', position: Position.Top },
  { id: 'right', position: Position.Right },
  { id: 'bottom', position: Position.Bottom },
  { id: 'left', position: Position.Left },
] as const

const { t, te } = useI18n()

const kind = computed(() => flowNodeKindDisplay(props.data.kind, props.data.docType))
const kindLabelKey = computed(() => flowNodeKindLabelKey(props.data.kind, props.data.docType))
const state = computed(() => flowNodeStateDisplay(props.data.state))

// Presentation only: retain the shared card state and approval rules.
const cardStateIcon = computed(() => {
  switch (props.data.cardState?.phase) {
    case 'approved':
      return 'M5 12l4 4L19 6'
    case 'awaiting':
      return 'M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18z M12 7v5l3 2'
    case 'working':
      return 'M21 12a9 9 0 1 1-9-9'
    default:
      return null
  }
})

// ── flowgate.v02.0064 T#4: the inline title edit ──────────────────────────────────────────────────────────────

/** The title is shown unless it only repeats the kind name of the head row. */
const showTitle = computed(() => props.data.title.trim() !== t(kindLabelKey.value).trim())
const titleEditable = computed(() => props.data.titleEditable === true && props.saveTitle !== null)

const editing = ref(false)
const draft = ref('')
const saving = ref(false)
const composing = ref(false)
/** Why the title was refused (the input stays), or null. */
const error = ref<string | null>(null)
/** The lock note left once the edit closed because the card runs, or null. */
const closedNote = ref<string | null>(null)
const editorEl = ref<HTMLElement | null>(null)
const inputEl = ref<HTMLInputElement | null>(null)
/** One per opening: an answer for an earlier opening is dropped. */
let opening = 0
let noteTimer: ReturnType<typeof setTimeout> | null = null
let pressedAt: { x: number; y: number } | null = null

const note = computed<{ tone: 'hint' | 'error' | 'notice'; text: string } | null>(() => {
  if (!editing.value) return closedNote.value ? { tone: 'notice', text: closedNote.value } : null
  if (error.value) return { tone: 'error', text: error.value }
  const { length } = checkNodeTitle(draft.value)
  const text = t('gui.designer.nodeTitle.length', { count: length, max: NODE_TITLE_MAX_CHARS })
  return { tone: length > NODE_TITLE_MAX_CHARS ? 'error' : 'hint', text }
})

function clearClosedNote() {
  if (noteTimer !== null) clearTimeout(noteTimer)
  noteTimer = null
  closedNote.value = null
}

function close(lockNote = false) {
  editing.value = false
  saving.value = false
  composing.value = false
  error.value = null
  opening += 1
  document.removeEventListener('pointerdown', onOutsidePointer, true)
  if (!lockNote) return
  closedNote.value = t('gui.designer.nodeTitle.locked')
  noteTimer = setTimeout(clearClosedNote, 4000)
}

function openEdit() {
  if (!titleEditable.value || props.connecting || editing.value) return
  clearClosedNote()
  opening += 1
  // The input starts from the title shown now; a newer title arriving meanwhile changes only what × keeps.
  draft.value = props.data.title
  error.value = null
  editing.value = true
  document.addEventListener('pointerdown', onOutsidePointer, true)
  void nextTick(() => {
    inputEl.value?.focus()
    inputEl.value?.select()
  })
}

function cancel() {
  if (editing.value && !saving.value) close()
}

/** A press elsewhere cancels (only an explicit save saves); not while the save is answered. */
function onOutsidePointer(event: Event) {
  const target = event.target as Node | null
  if (!editing.value || saving.value || (target && editorEl.value?.contains(target))) return
  close()
}

/** A refusal in the screen's words: the code's own sentence, else the server's, else a general one. */
function refusalText(code: string | null, message: string): string {
  const key = `gui.designer.nodeTitle.error.${code}`
  if (code && te(key)) return t(key)
  return message || t('gui.designer.nodeTitle.failed')
}

async function save() {
  if (!editing.value || saving.value || composing.value || !props.saveTitle) return
  const check = checkNodeTitle(draft.value)
  if (check.refusal) {
    error.value = t(`gui.designer.nodeTitle.error.${check.refusal}`)
    return
  }
  const asked = opening
  saving.value = true
  error.value = null
  let outcome: NodeTitleSaveOutcome
  try {
    outcome = await props.saveTitle(props.id, draft.value)
  } catch {
    outcome = { ok: false, code: null, message: '' }
  }
  if (asked !== opening || !editing.value) return
  saving.value = false
  if (outcome.ok) close()
  else if (outcome.code === 'node_title_locked') close(true)
  else {
    error.value = refusalText(outcome.code, outcome.message)
    void nextTick(() => inputEl.value?.focus())
  }
}

/**
 * Every key in the edit stops here (the Canvas never sees it). Enter in the input saves — on ✓ / × it presses that button
 * (the browser's own activation); Esc anywhere in the edit cancels.
 */
function onEditorKeydown(event: KeyboardEvent) {
  // An IME composing (Korean, Japanese …): Enter / Esc only end the composition.
  if (event.isComposing || composing.value || event.keyCode === 229) return
  if (event.key === 'Enter' && event.target === inputEl.value) {
    event.preventDefault()
    void save()
  } else if (event.key === 'Escape') {
    event.preventDefault()
    cancel()
  }
}

function markPress(event: PointerEvent) {
  pressedAt = { x: event.clientX, y: event.clientY }
}

/** A click on an editable title opens the edit — not the selection; a drag that ended on it opens nothing. */
function onTitleClick(event: MouseEvent) {
  const from = pressedAt
  pressedAt = null
  if (!titleEditable.value || props.connecting) return
  if (from && Math.hypot(event.clientX - from.x, event.clientY - from.y) > 3) return
  event.stopPropagation()
  openEdit()
}

// The card starts running (or can no longer be edited) while its edit is open: the edit closes, nothing is saved.
watch(titleEditable, (open) => {
  if (!open && editing.value) close(props.data.titleLock === 'running')
})

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onOutsidePointer, true)
  if (noteTimer !== null) clearTimeout(noteTimer)
})
</script>

<style scoped>
.flow-node-body-warning { color: var(--fg2-status-warning-fg); margin-left: 4px; }

/* Card size is defined here only: fixed 176x92 (03-designer-shell `.node`). Long text never
   grows the card — every text line stays one line and ends with an ellipsis. */
.flow-node-card {
  position: relative;
  box-sizing: border-box;
  width: 176px;
  height: 92px;
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

.flow-node-card.is-selected {
  box-shadow:
    0 0 0 2px color-mix(in srgb, var(--flow-node-accent) 70%, transparent),
    0 0 0 5px color-mix(in srgb, var(--flow-node-accent) 18%, transparent),
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

/* flowgate.v02.0061 T#15: the Hold mark — the warning tokens, at the end of the head row (after the state, if any). */
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

/* flowgate.v02.0063: the [Q] mark — the info tokens, before the Hold mark (after the state, if any). */
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
.flow-node-question + .flow-node-hold {
  margin-left: 4px;
}

.flow-node-body {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 5px;
  padding: 8px 11px;
}

/* flowgate.v02.0064 T#4: the title row (title + ✎ on hover) and the inline edit, above the connection strips. */
.flow-node-title-row {
  display: flex;
  align-self: stretch;
  align-items: center;
  gap: 3px;
  min-width: 0;
}

.flow-node-title-row .flow-node-title {
  flex: 0 1 auto;
  min-width: 0;
}

.flow-node-title.is-editable,
.flow-node-title-pen,
.flow-node-title-editor {
  position: relative;
  z-index: 4;
}

.flow-node-title.is-editable {
  cursor: text;
}

.flow-node-title-pen {
  flex: none;
  width: 16px;
  height: 16px;
  padding: 0;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: #8fa7bf;
  font: inherit;
  font-size: 11px;
  line-height: 16px;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.12s ease;
}

.flow-node-card:hover .flow-node-title-pen,
.flow-node-title-pen:focus-visible {
  opacity: 1;
}

.flow-node-title-pen:hover {
  color: var(--fg2-text);
}

.flow-node-title-editor {
  display: flex;
  align-self: stretch;
  align-items: center;
  gap: 3px;
  min-width: 0;
}

.flow-node-title-input {
  flex: 1 1 auto;
  box-sizing: border-box;
  min-width: 0;
  height: 22px;
  padding: 0 5px;
  border: 1px solid color-mix(in srgb, var(--flow-node-accent) 60%, transparent);
  border-radius: 4px;
  background: #0b1626;
  color: var(--fg2-text);
  font: inherit;
  font-size: 11.5px;
  font-weight: 650;
}

.flow-node-title-input:focus {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 0;
}

.flow-node-title-input[aria-invalid='true'] {
  border-color: var(--fg2-status-danger-border);
}

.flow-node-title-action {
  flex: none;
  width: 20px;
  height: 20px;
  padding: 0;
  border: 1px solid rgba(128, 160, 199, 0.35);
  border-radius: 4px;
  background: rgba(19, 36, 58, 0.9);
  color: var(--fg2-text);
  font: inherit;
  font-size: 11px;
  line-height: 18px;
  cursor: pointer;
}

.flow-node-title-action:disabled {
  cursor: progress;
  opacity: var(--fg2-control-disabled-opacity);
}

.flow-node-title-action:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 1px;
}

.flow-node-title-note {
  align-self: stretch;
  margin: -2px 0 0;
  overflow: hidden;
  color: #8fa7bf;
  font-size: 9.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.flow-node-title-note.is-error {
  color: var(--fg2-status-danger-fg);
}

.flow-node-title-note.is-notice {
  color: var(--fg2-status-warning-fg);
}

.flow-node-title {
  align-self: stretch;
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

/* Above the connection strips (z-index 2) so a press reaches it. */
.flow-node-approve {
  position: relative;
  z-index: 4;
  height: 20px;
  padding: 0 9px;
  border: 1px solid var(--fg2-status-warning-border);
  border-radius: 5px;
  background: var(--fg2-status-warning-bg);
  color: var(--fg2-status-warning-fg);
  font: inherit;
  font-size: 10.5px;
  font-weight: 700;
  cursor: pointer;
}

.flow-node-approve:hover:not(:disabled) {
  filter: brightness(1.15);
}

.flow-node-approve:disabled {
  cursor: progress;
  opacity: var(--fg2-control-disabled-opacity);
}

.flow-node-approve:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 1px;
}

.flow-node-title {
  margin: 0;
  overflow: hidden;
  font-size: 12px;
  font-weight: 680;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* flowgate.v02.0061 T#14: the integration line under the title (status tokens; above the strips). */
.flow-node-flags {
  position: relative;
  z-index: 4;
  display: flex;
  gap: 4px;
  max-width: 100%;
  overflow: hidden;
}

.flow-node-flag {
  flex: none;
  height: 16px;
  padding: 0 6px;
  border: 1px solid var(--fg2-status-info-border);
  border-radius: 4px;
  background: var(--fg2-status-info-bg);
  color: var(--fg2-status-info-fg);
  font: inherit;
  font-size: 9.5px;
  line-height: 14px;
  white-space: nowrap;
}

.flow-node-flag.is-conflict,
.flow-node-flag.is-failed {
  border-color: var(--fg2-status-danger-border);
  background: var(--fg2-status-danger-bg);
  color: var(--fg2-status-danger-fg);
}

.flow-node-flag.is-conflict {
  font-weight: 700;
  cursor: pointer;
}

.flow-node-flag.is-conflict:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 1px;
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

/* flowgate.v02.0045 TR0020 rev2 / rev4: a draft node's connection areas, no dots.
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
