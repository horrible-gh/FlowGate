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
       flowgate.v02.0052 묶음 A — the v7 single status pill (0035 insp.js render `● {st} · {sub}`,
       stTip): one user-facing label — the Canvas state, else the document review status, else the
       run status — and a `· sub` only for a server state that says something else than the label
       (the server's own Canvas mapping decides "the same"). Internal values (wf_*, run status) never
       show as text: a value without a display name is left to the tooltip (unless it is all there
       is); the raw values sit in the tooltip, in data attributes and in hidden spans. -->
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
    <span class="inspector-header-title" :title="target.title" data-test="designer-inspector-title">{{ target.title }}</span>
    <span
      v-if="target.readOnly"
      class="inspector-header-read-only"
      :title="t(`gui.designer.inspector.header.read_only_${target.readOnly}`)"
      data-test="designer-inspector-read-only"
      :data-reason="target.readOnly"
    >{{ t('gui.designer.inspector.header.read_only') }}</span>
    <span class="inspector-header-gap" />
    <slot name="meta" />
    <button
      type="button"
      class="inspector-header-memo"
      disabled
      :title="t('gui.designer.inspector.header.memo_unavailable')"
      data-test="designer-inspector-memo"
    >✎ <span class="inspector-header-memo-label">{{ t('gui.designer.inspector.header.memo') }}</span></button>
    <span
      v-if="pill"
      class="inspector-header-pill"
      :class="`inspector-header-pill--${pill.tone}`"
      :title="pill.title"
      data-test="designer-inspector-pill"
      :data-tone="pill.tone"
      :data-state="target.canvasState ?? ''"
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
</template>

<script setup lang="ts">
import { computed, onScopeDispose, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import Dropdown from '../../../components/ui/Dropdown.vue'
import IconButton from '../../../components/ui/IconButton.vue'
import { flowNodeKindDisplay, flowNodeKindLabelKey, flowNodeStateDisplay } from '../center/canvas/flowNodeKinds'
import type { InspectorTarget } from './inspectorTarget'

const props = defineProps<{
  target: InspectorTarget
}>()

const { t, te } = useI18n()

/** v7 (0035 insp.js iconOf / colorOf): a 버그 (B) root takes the bug glyph and the red accent. */
const BUG_KIND = { icon: 'M8 8a4 4 0 0 1 8 0v7a4 4 0 0 1-8 0Z M4 12h4M16 12h4M5 7l3 2M19 7l-3 2M5 18l3-2M19 18l-3-2', accent: '#ef4444' }

const isBug = computed(() => props.target.docType?.trim().toUpperCase() === 'B')
const kind = computed(() => {
  const base = flowNodeKindDisplay(props.target.nodeKind, props.target.docType)
  return isBug.value && base.key === 'requirement' ? { ...base, ...BUG_KIND } : base
})
const kindLabelKey = computed(() =>
  isBug.value && kind.value.key === 'requirement' ? 'gui.designer.inspector.header.kind_bug' : flowNodeKindLabelKey(props.target.nodeKind, props.target.docType),
)
const state = computed(() => flowNodeStateDisplay(props.target.canvasState))

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
  if (canvas) {
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
  if (canvas) {
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

onScopeDispose(() => {
  if (copiedTimer !== null) clearTimeout(copiedTimer)
})
</script>

<style scoped>
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

/* A long title shrinks first; the kind badge and the right side keep their place. */
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

/* v7 `.hd-memo.none`: the memo button with no memo — dim; disabled until a memo API exists. */
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
