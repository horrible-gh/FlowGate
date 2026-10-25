<template>
  <!-- flowgate.v02.0036 — Inspector header row (0035 v7, fixed 40px): kind icon box, kind
       badge and the Node title on the left; the meta part, the state chip and the actions on the
       right.
       flowgate.v02.0045 TR0020 rev2: a 설계 Node shows the same glyph and name as its Canvas card
       (기본 설계 / 프로토콜 / 로직 / DB설계).
       flowgate.v02.0048 T#1 (D#1 §8.3 rule 7): the target's real states next to the Canvas state
       chip — the bound document's review status and, on a Graph Node, its run status — each as
       the display text of the server value (an unknown value shows as sent). The meta / actions
       slots take the parts the Frame lays out from the registry.
       flowgate.v02.0051 — the rest of the v7 header line, on every card (0035 insp.js render):
       the 읽기 전용 tag from the target, the ✎ 메모 button (no memo API yet: shown disabled, 0051-R
       §2.3), the ● dot on the state pill, and the ⋯ menu at the end with its one common item,
       ID 복사 (the document id, v7 took it out of the header line). Card-specific menu items come
       through the `menu` slot.
       flowgate.v02.0051 stage 2 — the `kind` slot: a card feature may replace the static kind badge
       (the R/B 종류 배지 ▾ of kindSwitch); the three state texts (document review status · run
       status · ● Canvas state) sit in one pill group, the v7 single status pill; and the line never
       spills: it clips at its own edge, the actions scroll sideways when the Inspector is at its
       264px minimum, and the ✎ 메모 label folds to its glyph below 420px.
       flowgate.v02.0053 T#9 — the ✎ 메모 is the `memo` slot: a feature part (features/memo) writes and opens
       the card's memo; with none (a Node without a document) the dim, disabled ✎ 메모 stays.
       flowgate.v02.0052 묶음 A — the v7 single status pill (0035 insp.js render `● {st} · {sub}`,
       stTip): one user-facing label — the Canvas state, else the document review status, else the
       run status — and a `· sub` only for a server state that says something else than the label
       (the server's own Canvas mapping decides "the same"). Internal values (wf_*, run status) never
       show as text: a value without a display name is left to the tooltip (unless it is all there
       is); the raw values sit in the tooltip, in data attributes and in hidden spans.
       flowgate.v02.0052 T#3 — a Canvas `blocked` is the server's translation of a rejection
       (canvas_mapping.py: rejected → blocked). The pill names what happened — the rejection's own
       display name (반려됨, v7 work.rej `st:['반려됨','danger']`) — instead of the Canvas word (보류);
       the Canvas value stays in data-state, the hidden state span and the Canvas card.
       flowgate.v02.0052 T#4 — at the 264px Inspector the line keeps its ⋯ and its pill label: the title
       gives way first, then the meta part (작성 사람) and the actions shrink and clip instead of pushing
       the ⋯ menu out of the line.
       flowgate.v02.0052 T#4 rev1 (R §2.2: title, state and the real actions stay visible on a narrow
       Inspector) — one line cannot hold them below about 560px (measured: the title went to 0 and the
       pill / actions were cut up to a 504px Inspector), so there the header becomes two lines: the
       title line (icon, kind, title, meta, ✎, ⋯) and the state line (읽기 전용, ● pill, actions), the
       actions wrapping instead of scrolling. The frame around the header is the size container the
       header itself is laid out by. Wider Inspectors keep the v7 single 40px line unchanged.
       flowgate.v02.0070 T#1 (B1, moved here from the Canvas card): the card's own title is edited in its title area. A
       title that may be edited now (`target.canEditTitle`, with the host's `saveTitle`) turns into an input in place on a
       click, or on Enter / F2 when focused; a title that only repeats the kind name (so not shown) keeps a small ✎ entry.
       Enter saves, Esc or a press outside cancels; Enter / Esc while an IME composes only end the composition. Right of
       the input, the length (30, as the server counts) or why the title was refused — the input stays; the input and that
       note are one item of the line (the title area), so at any Inspector width they share the title's room (where the note
       clips, the input's tooltip names the refusal). A card that starts
       running meanwhile, or a save refused as running (`node_title_locked`), closes the edit with the lock note. The save
       goes to the Flow and card the edit was opened on; another card selected meanwhile closes the edit, and a late answer
       only reaches its own card (the session's Graph). Nothing is shown before the server answered: the title shown is the
       target's (the session's, its version order). -->
  <div class="inspector-header-frame">
    <header
      class="inspector-header"
      :style="{ '--inspector-kind-accent': kind.accent }"
      data-test="designer-inspector-header"
      :data-kind="kind.key"
      :data-read-only="target.readOnly ?? ''"
    >
      <span class="inspector-header-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24">
          <path :d="kind.icon" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </span>
      <slot name="kind">
        <span class="inspector-header-kind" data-test="designer-inspector-kind">{{ t(kindLabelKey) }}</span>
      </slot>
      <!-- The title area: one header item holding the title (or its input) and the note right of it, so they share
           the title's room and shrink together instead of pushing the ✎ 메모 and ⋯ off the line. -->
      <span
        v-if="editing || !titleRepeatsKind || titleEditable || note"
        class="inspector-header-title-area"
        :class="{ 'is-editing': editing }"
        data-test="designer-inspector-title-area"
      >
        <span v-if="editing" ref="editorEl" class="inspector-header-title-editor" data-test="designer-inspector-title-editor">
          <input
            ref="inputEl"
            v-model="draft"
            type="text"
            class="inspector-header-title-input"
            :aria-label="t('gui.designer.nodeTitle.input')"
            :title="error ?? t('gui.designer.nodeTitle.hint')"
            :aria-invalid="error ? 'true' : undefined"
            :disabled="saving"
            data-test="designer-inspector-title-input"
            @input="error = null"
            @keydown="onInputKeydown"
            @compositionstart="composing = true"
            @compositionend="composing = false"
          />
        </span>
        <span
          v-else-if="!titleRepeatsKind"
          class="inspector-header-title"
          :class="{ 'is-editable': titleEditable }"
          :title="target.title"
          :role="titleEditable ? 'button' : undefined"
          :tabindex="titleEditable ? 0 : undefined"
          data-test="designer-inspector-title"
          @click="openEdit"
          @keydown="onTitleKeydown"
        >{{ target.title }}</span>
        <button
          v-else-if="titleEditable"
          type="button"
          class="inspector-header-title-edit"
          :aria-label="t('gui.designer.nodeTitle.edit')"
          :title="t('gui.designer.nodeTitle.edit')"
          data-test="designer-inspector-title-edit"
          @click="openEdit"
        >✎</button>
        <span
          v-if="note"
          class="inspector-header-title-note"
          :class="`is-${note.tone}`"
          :role="note.tone === 'hint' ? undefined : note.tone === 'error' ? 'alert' : 'status'"
          :title="note.text"
          data-test="designer-inspector-title-note"
          :data-tone="note.tone"
        >{{ note.text }}</span>
      </span>
      <span class="inspector-header-gap" />
      <span v-if="$slots.meta" class="inspector-header-meta" data-test="designer-inspector-meta"><slot name="meta" /></span>
      <slot name="memo">
        <button
          type="button"
          class="inspector-header-memo"
          disabled
          :title="t('gui.designer.inspector.header.memo_unavailable')"
          data-test="designer-inspector-memo"
        >✎ <span class="inspector-header-memo-label">{{ t('gui.designer.inspector.header.memo') }}</span></button>
      </slot>
      <slot name="state">
      <span
        v-if="pill"
        class="inspector-header-pill"
        :class="`inspector-header-pill--${pill.tone}`"
        :title="pill.title"
        data-test="designer-inspector-pill"
        :data-tone="pill.tone"
        :data-state="target.canvasState ?? ''"
        :data-card-state="cardState?.key ?? ''"
        :data-doc-status="target.docReviewStatus ?? ''"
        :data-run-status="target.nodeRun?.status ?? ''"
      >
        <span class="inspector-header-pill-label" data-test="designer-inspector-pill-label">{{ pill.label }}</span>
        <span v-if="pill.sub" class="inspector-header-pill-sub" data-test="designer-inspector-pill-sub">· {{ pill.sub }}</span>
        <!-- The server values stay in the DOM (hidden: the raw value as data-value, the display name as
             text) for tests and debugging. -->
        <span
          v-if="target.docReviewStatus"
          hidden
          data-test="designer-inspector-doc-status"
          :data-value="target.docReviewStatus"
        >{{ reviewStatusText ?? '' }}</span>
        <span
          v-if="target.nodeRun?.status"
          hidden
          data-test="designer-inspector-run-status"
          :data-value="target.nodeRun.status"
        >{{ runStatusText ?? '' }}</span>
        <span v-if="state" hidden data-test="designer-inspector-state" :data-value="state.key">{{ stateText }}</span>
      </span>
      </slot>
      <div v-if="$slots.actions" class="inspector-header-actions" data-test="designer-inspector-actions">
        <slot name="actions" />
      </div>
      <Dropdown>
        <template #trigger="{ toggle, triggerAttrs }">
          <IconButton
            v-bind="triggerAttrs"
            class="inspector-header-more"
            :label="t('gui.designer.inspector.header.more')"
            :title="t('gui.designer.inspector.header.more')"
            variant="secondary"
            size="sm"
            data-test="designer-inspector-more"
            @click="toggle"
          >⋯</IconButton>
        </template>
        <template #default="{ close }">
          <button
            type="button"
            role="menuitem"
            class="inspector-header-menu-item"
            :disabled="!canCopy"
            data-test="designer-inspector-copy-id"
            @click="copyId"
          >{{ t(copied ? 'gui.designer.inspector.header.copied' : 'gui.designer.inspector.header.copy_id') }}</button>
          <slot name="menu" :close="close" />
        </template>
      </Dropdown>
    </header>
  </div>
</template>

<script setup lang="ts">
import { computed, inject, nextTick, onScopeDispose, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Dropdown from '../../../components/ui/Dropdown.vue'
import IconButton from '../../../components/ui/IconButton.vue'
import { flowNodeKindView, flowNodeStateDisplay } from '../center/canvas/flowNodeKinds'
import { INSPECTOR_HOST } from './inspectorHost'
import { NODE_TITLE_MAX_CHARS, checkNodeTitle, inspectorCardState, type InspectorTarget, type NodeTitleSaveOutcome } from './inspectorTarget'

const props = defineProps<{
  target: InspectorTarget
}>()

const { t, te } = useI18n()

/** flowgate.v02.0070 T#4 (B4): the Canvas card's kind display (a 버그 (B) root: the bug glyph, red accent and name). */
const kind = computed(() => flowNodeKindView(props.target.nodeKind, props.target.docType))
const kindLabelKey = computed(() => kind.value.labelKey)
const state = computed(() => flowNodeStateDisplay(props.target.canvasState))
// flowgate.v02.0083 T#4: a Graph card with a run says what its Canvas card says (작업 중 · 작성 대기 · 결과 없음 …).
const cardState = computed(() => inspectorCardState(props.target))

type PillTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger'

/** Tones of the server states when they lead the pill (no Canvas state). */
const REVIEW_TONES: Readonly<Record<string, PillTone>> = {
  pending_review: 'warning',
  approved: 'success',
  rejected: 'danger',
  revised: 'info',
  wf_in_progress: 'info',
  wf_done: 'success',
}
const RUN_TONES: Readonly<Record<string, PillTone>> = {
  pending: 'neutral',
  open: 'info',
  running: 'info',
  review: 'warning',
  done: 'success',
  completed: 'success',
  rejected: 'danger',
  failed: 'danger',
  stopped: 'neutral',
}

/* The Canvas state each server value stands for (server canvas_mapping.py `_STATE_BY_REVIEW` /
   `_STATE_BY_NODE_RUN`): a server state that maps to the shown Canvas state says the same thing
   and is not shown again; any other (or unknown) value becomes the `· sub`. */
const STATE_BY_REVIEW: Readonly<Record<string, string>> = {
  approved: 'done',
  wf_done: 'done',
  pending_review: 'review',
  revised: 'review',
  rejected: 'blocked',
  wf_in_progress: 'in_progress',
}
const STATE_BY_RUN: Readonly<Record<string, string>> = {
  pending: 'draft',
  open: 'in_progress',
  review: 'review',
  done: 'done',
  rejected: 'blocked',
}

/** The rejected values a Canvas `blocked` comes from (canvas_mapping.py `_STATE_BY_REVIEW` / `_STATE_BY_NODE_RUN`). */
const REJECTED = 'rejected'

/** Display name of a server value, or null when it has none (an internal code never shows as text). */
function displayOf(group: 'review_status' | 'run_status', value: string): string | null {
  const key = `gui.designer.inspector.${group}.${value}`
  return te(key) ? t(key) : null
}

const reviewStatusText = computed(() => {
  const value = props.target.docReviewStatus ?? ''
  return value ? displayOf('review_status', value) : null
})
const runStatusText = computed(() => {
  const value = props.target.nodeRun?.status ?? ''
  return value ? displayOf('run_status', value) : null
})
const stateText = computed(() => (state.value ? t(`gui.designer.center.node_states.${state.value.key}`) : ''))

/** The tooltip part of a server value: "display (raw)", or the raw value alone. */
const tipOf = (display: string | null, raw: string): string => (raw ? (display ? `${display} (${raw})` : raw) : '—')

interface HeaderPill {
  readonly label: string
  readonly tone: PillTone
  readonly sub: string | null
  readonly title: string
}

const pill = computed<HeaderPill | null>(() => {
  const canvas = state.value
  const doc = props.target.docReviewStatus ?? ''
  const run = props.target.nodeRun?.status ?? ''
  if (!canvas && !doc && !run) return null
  const docText = reviewStatusText.value
  const runText = runStatusText.value
  let label: string
  let tone: PillTone
  const card = cardState.value
  if (card) {
    // The card state leads (center/graph/cardStateView.ts — the run's own facts, never the Canvas `in_progress`).
    label = t(card.labelKey)
    tone = card.tone
  } else if (canvas?.key === 'blocked' && doc === REJECTED && docText) {
    // A rejected document: 반려됨, not the Canvas word for it.
    label = docText
    tone = REVIEW_TONES[doc] ?? canvas.tone
  } else if (canvas?.key === 'blocked' && run === REJECTED && runText) {
    // A rejected Graph node run, the same.
    label = runText
    tone = RUN_TONES[run] ?? canvas.tone
  } else if (canvas) {
    label = stateText.value
    tone = canvas.tone
  } else if (doc && (docText || !runText)) {
    // A document value without a display name leads only when nothing better is there.
    label = docText ?? doc
    tone = REVIEW_TONES[doc] ?? 'neutral'
  } else {
    label = runText ?? run
    tone = RUN_TONES[run] ?? 'neutral'
  }
  // The sub: a server state with a display name that says something else than the label.
  const subs: (string | null)[] = []
  if (card) {
    if (doc) subs.push(docText)
  } else if (canvas) {
    if (doc && STATE_BY_REVIEW[doc] !== canvas.key) subs.push(docText)
    if (run && STATE_BY_RUN[run] !== canvas.key) subs.push(runText)
  } else if (doc && run && label === docText && STATE_BY_RUN[run] !== STATE_BY_REVIEW[doc]) {
    subs.push(runText)
  }
  const sub = subs
    .filter((text): text is string => text !== null && text !== label)
    .filter((text, index, all) => all.indexOf(text) === index)
    .join(' · ')
  const title = t('gui.designer.inspector.header.state_tip', { doc: tipOf(docText, doc), run: tipOf(runText, run) })
  return { label, tone, sub: sub || null, title }
})

// ⋯ → ID 복사: the bound document's id to the clipboard. Nothing to copy without a document or
// without the clipboard API (an insecure context): the item stays, disabled.
const clipboard = typeof navigator !== 'undefined' ? navigator.clipboard : undefined
const canCopy = computed(() => !!props.target.docId && !!clipboard)
const copied = ref(false)
let copiedTimer: ReturnType<typeof setTimeout> | null = null

async function copyId() {
  const docId = props.target.docId
  if (!docId || !clipboard) return
  try {
    await clipboard.writeText(docId)
  } catch {
    return
  }
  copied.value = true
  if (copiedTimer !== null) clearTimeout(copiedTimer)
  copiedTimer = setTimeout(() => {
    copied.value = false
  }, 1500)
}

// ── flowgate.v02.0070 T#1 (B1): the card title edit (see the head of this file) ──────────────────────────────────

const host = inject(INSPECTOR_HOST, null)

/** The title only repeats the kind name of the badge: not shown again (the ✎ entry stays when it may be edited). */
const titleRepeatsKind = computed(() => props.target.nodeType !== 'gate' && props.target.title.trim() === t(kindLabelKey.value).trim())
const titleEditable = computed(() => props.target.canEditTitle === true && !!host?.saveTitle)

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
/** The Flow and card the open edit was opened on: its save goes there, whatever is selected meanwhile. */
let editFor: { readonly flowId: string; readonly nodeId: string } | null = null
/** One per opening: an answer for an earlier opening is dropped. */
let opening = 0
let noteTimer: ReturnType<typeof setTimeout> | null = null

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
  editFor = null
  opening += 1
  document.removeEventListener('pointerdown', onOutsidePointer, true)
  if (!lockNote) return
  closedNote.value = t('gui.designer.nodeTitle.locked')
  noteTimer = setTimeout(clearClosedNote, 4000)
}

function openEdit() {
  if (!titleEditable.value || editing.value) return
  clearClosedNote()
  opening += 1
  editFor = { flowId: props.target.flowId, nodeId: props.target.nodeId }
  // The input starts from the title shown now; a newer title arriving meanwhile changes only what Esc keeps.
  draft.value = props.target.title
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

/** A press elsewhere cancels (only Enter saves); not while the save is answered. */
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
  const at = editFor
  if (!editing.value || saving.value || composing.value || !at || !host?.saveTitle) return
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
    outcome = await host.saveTitle(at.flowId, at.nodeId, draft.value)
  } catch {
    outcome = { ok: false, code: null, message: '' }
  }
  // Closed meanwhile (cancelled, another card selected, the card started running): the answer is not this edit's.
  if (asked !== opening || !editing.value) return
  saving.value = false
  if (outcome.ok) close()
  else if (outcome.code === 'node_title_locked') close(true)
  else {
    error.value = refusalText(outcome.code, outcome.message)
    void nextTick(() => inputEl.value?.focus())
  }
}

function onInputKeydown(event: KeyboardEvent) {
  // An IME composing (Korean, Japanese …): Enter / Esc only end the composition.
  if (event.isComposing || composing.value || event.keyCode === 229) return
  if (event.key === 'Enter') {
    event.preventDefault()
    event.stopPropagation()
    void save()
  } else if (event.key === 'Escape') {
    event.preventDefault()
    event.stopPropagation()
    cancel()
  }
}

function onTitleKeydown(event: KeyboardEvent) {
  if (!titleEditable.value || (event.key !== 'Enter' && event.key !== 'F2')) return
  event.preventDefault()
  openEdit()
}

// Another card (or Flow) selected: an open edit closes and a lock note of the card before goes.
watch(
  () => `${props.target.flowId}\n${props.target.nodeId}`,
  () => {
    if (editing.value) close()
    clearClosedNote()
  },
)

// The card starts running (or can no longer be edited) while its edit is open: the edit closes, nothing is saved.
watch(titleEditable, (open) => {
  if (!open && editing.value) close(props.target.titleLock === 'running')
})

onScopeDispose(() => {
  if (copiedTimer !== null) clearTimeout(copiedTimer)
  if (noteTimer !== null) clearTimeout(noteTimer)
  document.removeEventListener('pointerdown', onOutsidePointer, true)
})
</script>

<style scoped>
/* The size container of the header line (T#4 rev1): a container query cannot change the layout of the
   container itself, so the narrow two-line rule below queries this frame. */
.inspector-header-frame {
  flex: 0 0 auto;
  min-width: 0;
  container: inspector-header-frame / inline-size;
}

.inspector-header {
  display: flex;
  flex: 0 0 40px;
  align-items: center;
  gap: 8px;
  box-sizing: border-box;
  height: 40px;
  min-width: 0;
  padding: 0 10px 0 12px;
  overflow: hidden;
  border-bottom: 1px solid var(--fg2-line);
  background: #0c1b2d;
  container-type: inline-size;
}

.inspector-header-icon {
  display: grid;
  flex: 0 0 24px;
  place-items: center;
  width: 24px;
  height: 24px;
  border-radius: 6px;
  background: color-mix(in srgb, var(--inspector-kind-accent) 85%, #0a1422);
  color: #fff;
}

.inspector-header-icon svg {
  display: block;
  width: 14px;
  height: 14px;
}

.inspector-header-kind {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  height: 22px;
  padding: 0 8px;
  border: 1px solid color-mix(in srgb, var(--inspector-kind-accent) 55%, transparent);
  border-radius: 6px;
  background: color-mix(in srgb, var(--inspector-kind-accent) 16%, #0b1827);
  color: #e6f0fb;
  font-size: 11.5px;
  font-weight: 700;
  white-space: nowrap;
}

/* A long title shrinks first; the kind badge and the right side keep their place. The large shrink
   weight lets the title give way before the pill, the meta part and the actions (T#4: 264px).
   flowgate.v02.0070 T#1 rev1: that item is the title area — the title (or its input) with the note right of it —
   so the input and the note take only the title's room and never push the ✎ 메모 and ⋯ onto another line. */
.inspector-header-title-area {
  display: flex;
  flex: 0 1000 auto;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.inspector-header-title {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  color: var(--fg2-text);
  font-size: 13px;
  font-weight: 700;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* flowgate.v02.0070 T#1: the title edit — in place of the title, within the header line (same font). */
.inspector-header-title.is-editable {
  border-radius: 4px;
  cursor: text;
}

.inspector-header-title.is-editable:hover {
  background: color-mix(in srgb, var(--inspector-kind-accent) 14%, transparent);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--inspector-kind-accent) 14%, transparent);
}

.inspector-header-title.is-editable:focus-visible,
.inspector-header-title-edit:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 1px;
}

/* While editing, the area asks for a usable input on the single line (it still gives way first); the input and the
   note right of it shrink together, and on the narrowest line the area clips rather than run over the meta / ✎ 메모. */
.inspector-header-title-area.is-editing {
  flex: 1 1000 240px;
  min-width: 96px;
  max-width: 420px;
  overflow: hidden;
}

.inspector-header-title-editor {
  display: flex;
  flex: 1 1 96px;
  align-items: center;
  min-width: 48px;
}

.inspector-header-title-input {
  box-sizing: border-box;
  width: 100%;
  min-width: 0;
  height: 26px;
  padding: 0 7px;
  border: 1px solid color-mix(in srgb, var(--inspector-kind-accent) 55%, transparent);
  border-radius: 6px;
  background: #0b1827;
  color: var(--fg2-text);
  font: inherit;
  font-size: 13px;
  font-weight: 700;
}

/* Drawn inside the input: the title area clips at its edge. */
.inspector-header-title-input:focus {
  outline: 2px solid var(--fg2-focus);
  outline-offset: -2px;
}

.inspector-header-title-input[aria-invalid='true'] {
  border-color: var(--fg2-status-danger-border);
}

.inspector-header-title-input:disabled {
  cursor: progress;
  opacity: var(--fg2-control-disabled-opacity);
}

/* The ✎ entry of a title that only repeats the kind name: small and quiet, like the ✎ 메모 glyph. */
.inspector-header-title-edit {
  flex: 0 0 auto;
  width: 22px;
  height: 22px;
  padding: 0;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: #6f87a0;
  font: inherit;
  font-size: 12px;
  line-height: 22px;
  cursor: pointer;
}

.inspector-header-title-edit:hover {
  color: var(--fg2-text);
}

/* The length, a refusal or the lock note: small text right of the title, inside the title area; a long sentence
   clips (its tooltip has it). */
.inspector-header-title-note {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  color: var(--fg2-muted);
  font-size: 10.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.inspector-header-title-note.is-hint {
  flex: 0 0 auto;
}

.inspector-header-title-note.is-error {
  color: var(--fg2-status-danger-fg);
}

.inspector-header-title-note.is-notice {
  color: var(--fg2-status-warning-fg);
}

/* v7 `.ro-tag`. */
.inspector-header-read-only {
  flex: 0 0 auto;
  padding: 2px 8px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius-pill);
  color: #9fb3c9;
  font-size: 10.5px;
  white-space: nowrap;
  cursor: help;
}

.inspector-header-gap {
  flex: 1 1 8px;
  min-width: 8px;
}

/* The card meta part (the 작업계획 작성 사람 badge): clips on a narrow line rather than pushing the ⋯ out. */
.inspector-header-meta {
  display: inline-flex;
  flex: 0 1 auto;
  align-items: center;
  gap: 8px;
  min-width: 0;
  overflow: hidden;
}

/* v7 `.hd-memo.none`: the memo button with no memo — dim; disabled on a Node without a document (a card's
   own ✎ 메모 is the features/memo part, drawn the same). */
.inspector-header-memo {
  flex: 0 0 auto;
  height: 26px;
  padding: 0 8px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: 6px;
  background: transparent;
  color: #6f87a0;
  font-size: 11.5px;
  line-height: 1;
  white-space: nowrap;
}

.inspector-header-memo:disabled {
  cursor: not-allowed;
  opacity: 0.7;
}

/* v7 `.status-pill`, one pill: `● {label} · {sub}` in the tone of the label's state (0035
   insp.css `.status-pill.*`). The sub is lighter and shrinks first. */
.inspector-header-pill {
  display: inline-flex;
  flex: 0 1 auto;
  align-items: center;
  gap: 5px;
  height: 20px;
  min-width: 0;
  padding: 0 8px;
  overflow: hidden;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius-pill);
  background: #14243a;
  color: var(--fg2-muted);
  font-size: 11px;
  white-space: nowrap;
  cursor: help;
}

.inspector-header-pill::before {
  content: '';
  flex: 0 0 auto;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}

.inspector-header-pill--info {
  border-color: var(--fg2-status-info-border);
  background: var(--fg2-status-info-bg);
  color: var(--fg2-status-info-fg);
}

.inspector-header-pill--success {
  border-color: var(--fg2-status-success-border);
  background: var(--fg2-status-success-bg);
  color: var(--fg2-status-success-fg);
}

.inspector-header-pill--warning {
  border-color: var(--fg2-status-warning-border);
  background: var(--fg2-status-warning-bg);
  color: var(--fg2-status-warning-fg);
}

.inspector-header-pill--danger {
  border-color: var(--fg2-status-danger-border);
  background: var(--fg2-status-danger-bg);
  color: var(--fg2-status-danger-fg);
}

.inspector-header-pill-label {
  flex: 0 0 auto;
  font-weight: 700;
}

.inspector-header-pill-sub {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  font-weight: 500;
  opacity: 0.85;
  text-overflow: ellipsis;
}

/* The actions keep their buttons whole; on a narrow Inspector the group scrolls sideways instead of
   pushing the ⋯ button out of the line. */
.inspector-header-actions {
  display: flex;
  flex: 0 1 auto;
  align-items: center;
  gap: 6px;
  min-width: 0;
  overflow-x: auto;
  overflow-y: hidden;
  scrollbar-width: none;
}

.inspector-header-actions::-webkit-scrollbar {
  display: none;
}

.inspector-header-actions > * {
  flex: 0 0 auto;
}

/* 264px Inspector (the 360px Center minimum): the memo label folds to its glyph. */
@container (max-width: 420px) {
  .inspector-header-memo-label {
    display: none;
  }

  .inspector-header-memo {
    padding: 0 6px;
  }
}

/* Narrow Inspector (264px up to 560px), T#4 rev1: two lines. The title line keeps the icon, kind,
   title, meta, ✎ and ⋯ (DOM order); the line break (::after) and the state line (읽기 전용, the
   pill, the actions) follow by `order`. The title takes the free room of its line (so the ✎ and ⋯
   stay at its end) and never goes below a few letters; the meta part shares what is left. The pill
   shows its whole label; the actions keep every button whole and wrap.
   flowgate.v02.0070 T#1 rev1: the title area does that, the same while editing — it asks the line for no more
   than the title did (a 0 basis, 48px at least), so the input and its note fit in the title's room and the
   line never breaks again. */
@container inspector-header-frame (max-width: 560px) {
  .inspector-header {
    flex-wrap: wrap;
    row-gap: 4px;
    height: auto;
    min-height: 40px;
    padding-top: 6px;
    padding-bottom: 6px;
  }

  .inspector-header::after {
    content: '';
    flex: 0 0 100%;
    order: 1;
    height: 0;
  }

  .inspector-header-gap {
    display: none;
  }

  .inspector-header-title-area.is-editing,
  .inspector-header-title-area {
    flex: 4 1 0;
    min-width: 48px;
    max-width: none;
  }

  .inspector-header-meta {
    flex: 1 1 0;
    max-width: max-content;
  }

  .inspector-header-read-only,
  .inspector-header-pill,
  .inspector-header-actions {
    order: 2;
  }

  .inspector-header-pill {
    max-width: 100%;
  }

  .inspector-header-actions {
    flex: 1 1 0;
    flex-wrap: wrap;
    row-gap: 4px;
    min-width: auto;
    overflow: visible;
  }
}

/* v7 `.action-btn.more`: 28px square at the end of the line. */
.inspector-header-more {
  flex: 0 0 auto;
  font-size: 15px;
  line-height: 1;
}

/* The ⋯ menu items (teleported with the Dropdown; this scope still applies). */
.inspector-header-menu-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  height: 30px;
  padding: 0 10px;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: #c3d3e3;
  font-size: 12px;
  text-align: left;
  cursor: pointer;
}

.inspector-header-menu-item:hover:not(:disabled) {
  background: #15345a;
}

.inspector-header-menu-item:disabled {
  color: #5d738b;
  cursor: default;
}
</style>
