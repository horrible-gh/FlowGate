<template>
  <!-- flowgate.v02.0051 — one v7 slot line (0035 insp-v4 `.slot`): the document tab shows every card as
       slot lines — a document slot (지시서 · 레포트 · 요구사항 · 작업계획 · 시나리오) or a result slot (확인 결과 ·
       전개 · AI 위임). Display only: the feature decides the values and puts its buttons in the default slot.
       `focus` marks the line the user should look at (amber bar); `danger` the failed one (red bar).
       A feature may hand its own test ids for the state and the summary (`testIds`) and may render the
       summary itself through the `summary` slot. The name opens the document (`openable`: underlined on
       hover, an `open` event).
       flowgate.v02.0056 F1 (0056 CH §5) — the 0053 state dot is gone: the line is `name · [state badge] ·
       summary · buttons`. The state is a short text badge drawn only when it says something (a decision
       waits, something runs, the document was revised, or it is settled — dim); a neutral state ('작성 중',
       '완료') draws nothing (slotStateView). A line without a document (`empty`) says why in plain muted
       words where the badge would be.
       flowgate.v02.0056 F2-1 — a line may name who writes it (`author`, O2 0007-TR §4-3): the instruction line
       of a run card says "지시 AI 또는 사람" in muted words before its state (slotAuthorKey; other authors draw
       nothing).
       flowgate.v02.0056 F2-2 — a line whose document has no body yet (`blank`, O3 0009-TR §4-1 has_body).
       flowgate.v02.0056 (0017-CH) — no author words, and a blank line has no words either: a dashed frame and a
       dim name (the list draws its hollow document icon); "비어 있음" is the line's hover text.
       flowgate.v02.0070 T#4 (B4) — the three line states are told apart in the line: no document (`empty`) — a dashed
       frame and "문서 없음"; a document without a body (`blank`) — a solid frame, a dim name and "본문 비어 있음" in the
       same muted words; an unreadable body — the small ⚠ below.
       flowgate.v02.0070 T#2 (B2) — an unreadable body is no longer words before the state: it is a small ⚠ at the
       head of the buttons (before 이력 · 열기), no bigger than the state badge; its words are the tooltip and the
       aria-label. The line reads `name · [state badge] · ⚠ · buttons`, and only the name ellipsises: on a narrow
       list such a line (`is-state-first`) keeps its badge, ⚠ and buttons whole; its summary gives way first, then
       the name (264px: the start of the name and … stay beside ⚠ · AI 호출 · 이력 · 열기 · ✎). -->
  <div
    class="inspector-slot"
    :class="[`inspector-slot--${kind}`, { 'is-empty': empty, 'is-blank': blank && !empty, 'is-focus': focus, 'is-danger': danger, 'is-state-first': warns }]"
    :title="view.kind === 'blank' ? view.title : undefined"
    data-test="inspector-slot"
    :data-kind="kind"
    :data-empty="empty ? 'true' : 'false'"
    :data-blank="blank && !empty ? 'true' : undefined"
    :data-name="name"
    :data-doc="id ?? undefined"
  >
    <button
      v-if="openable"
      type="button"
      class="inspector-slot-name is-link"
      :title="name"
      data-test="inspector-slot-name"
      @click="emit('open')"
    >{{ name }}</button>
    <span v-else class="inspector-slot-name" :title="name" data-test="inspector-slot-name">{{ name }}</span>
    <span class="inspector-slot-value">
      <span v-if="authorKey" class="inspector-slot-author" :title="t(authorKey)" data-test="inspector-slot-author" :data-author="author">{{ t(authorKey) }}</span>
      <span
        v-if="view.kind !== 'none' && view.text"
        class="inspector-slot-state"
        :class="[`is-${view.tone}`, { 'is-quiet': view.quiet, 'is-empty-text': view.kind === 'empty' || view.kind === 'blank' }]"
        :title="view.title"
        :data-test="testIds?.state ?? 'inspector-slot-state'"
        :data-tone="view.tone"
        :data-kind="view.kind"
      >{{ view.text }}</span>
      <template v-if="!empty">
        <slot name="summary">
          <span v-if="summary" class="inspector-slot-summary" :title="summary" :data-test="testIds?.summary ?? 'inspector-slot-summary'">{{ summary }}</span>
        </slot>
        <span
          v-if="progress !== null && progress !== undefined"
          class="inspector-slot-progress"
          role="progressbar"
          :aria-valuenow="clamped"
          aria-valuemin="0"
          aria-valuemax="100"
          :aria-label="t('gui.designer.inspector.slot.progress')"
          data-test="inspector-slot-progress"
        ><i :style="{ width: `${clamped}%` }" /></span>
      </template>
    </span>
    <span class="inspector-slot-buttons" data-test="inspector-slot-buttons">
      <span v-if="warns" class="inspector-slot-unreadable" role="img" :aria-label="t('gui.document.bodyState.unreadable')" :title="t(`gui.document.bodyState.${bodyState}`)" data-test="inspector-slot-unreadable">⚠</span>
      <slot />
    </span>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { lineWarns, slotAuthorKey, slotStateView, type SlotStateInput } from './slotStateView'

export type InspectorSlotState = SlotStateInput

/** Test ids a feature may put on the line's state badge and summary (its own specs read them). */
export interface InspectorSlotTestIds {
  readonly state?: string
  readonly summary?: string
}

const props = withDefaults(
  defineProps<{
    /** document: a document the card holds; result: what a run or a check gives. */
    kind?: 'document' | 'result'
    name: string
    /** The document id behind the line (data-doc); null when there is none. */
    id?: string | null
    bodyState?: string | null
    /** The name opens the document (an `open` event). */
    openable?: boolean
    /** No document behind the line: `emptyText` says why in the line. */
    empty?: boolean
    /** Why the line has no document; the common wording otherwise. */
    emptyText?: string | null
    state?: InspectorSlotState | null
    summary?: string | null
    /** 0..100: a progress bar after the summary (a running result). */
    progress?: number | null
    focus?: boolean
    danger?: boolean
    testIds?: InspectorSlotTestIds | null
    /** flowgate.v02.0056 F2-1: the line's author as the server sends it (`instruction_ai_or_human` is named). */
    author?: string | null
    /** flowgate.v02.0056 F2-2: the line's document has no body yet ("비어 있음"). */
    blank?: boolean
  }>(),
  {
    kind: 'document',
    id: null,
    openable: false,
    empty: false,
    emptyText: null,
    state: null,
    summary: null,
    progress: null,
    focus: false,
    danger: false,
    testIds: null,
    author: null,
    blank: false,
  },
)

const emit = defineEmits<{ open: [] }>()

const { t } = useI18n()

/** The state badge, the empty wording, or nothing (slotStateView). */
const view = computed(() =>
  slotStateView(
    props.state,
    props.empty,
    props.empty ? (props.emptyText ?? t('gui.designer.inspector.slot.none')) : null,
    props.blank ? t('gui.designer.inspector.slot.blank') : null,
  ),
)

const clamped = computed(() => Math.max(0, Math.min(100, Math.round(props.progress ?? 0))))

/** The author's wording key, or null (nothing drawn). */
const authorKey = computed(() => slotAuthorKey(props.author))

/**
 * The line's document body cannot be read: the small ⚠ before the buttons. flowgate.v02.0070 T#2 (B2): such a line
 * is also `is-state-first` — its buttons are fixed icons (the warning comes from a card's document line, which has
 * no shrinking nav), so on a narrow list the badge, ⚠ and buttons keep their width, the summary gives way first and
 * then the name.
 */
const warns = computed(() => lineWarns({ docId: props.id, bodyState: props.bodyState }))
</script>

<style scoped>
.inspector-slot {
  display: grid;
  /* T#4: the buttons column may shrink below its content on a narrow Inspector (264px); the line's
     nav label then ellipsises (InspectorSlotList) and the document icon / ✎ keep their place. */
  grid-template-columns: minmax(72px, 120px) minmax(0, 1fr) minmax(0, max-content);
  gap: 9px;
  align-items: center;
  box-sizing: border-box;
  height: 38px;
  min-width: 0;
  padding: 0 9px 0 12px;
  border: 1px solid var(--fg2-line);
  border-radius: 8px;
  background: #0e2034;
}

/* A narrow slot list (264px Inspector): the name column stays at its minimum so the buttons keep the room. */
@container (max-width: 360px) {
  .inspector-slot {
    grid-template-columns: 72px minmax(0, 1fr) minmax(0, max-content);
  }

  /* flowgate.v02.0070 T#2 (B2): a warning line keeps its badge and ⚠ · [AI 호출] · 이력 · 열기 · ✎ whole; the name
     ellipsises below 72px. Whatever follows the badge (the summary, its slot, the progress bar) gives way first:
     it takes no room of its own (no gap, zero width), only what is left after the name. */
  .inspector-slot.is-state-first {
    grid-template-columns: minmax(0, 72px) minmax(min-content, 1fr) max-content;
  }

  .inspector-slot.is-state-first .inspector-slot-value {
    gap: 0;
  }

  .inspector-slot.is-state-first .inspector-slot-value > :deep(:not(.inspector-slot-state)) {
    flex: 1 1 0;
    width: 0;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  /* The gap, drawn inside the part that follows (so a part with no room costs none). */
  .inspector-slot.is-state-first .inspector-slot-value > :deep(* + *) {
    text-indent: 8px;
  }

  .inspector-slot.is-state-first .inspector-slot-value > :deep(* + .inspector-slot-progress) {
    transform: translateX(8px);
  }

  .inspector-slot.is-state-first .inspector-slot-progress {
    max-width: 120px;
  }
}

.inspector-slot-name {
  min-width: 0;
  overflow: hidden;
  padding: 0;
  border: 0;
  background: none;
  color: #dbe7f2;
  font: inherit;
  font-size: 12px;
  font-weight: 750;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.inspector-slot-name.is-link {
  cursor: pointer;
}

.inspector-slot-name.is-link:hover {
  text-decoration: underline;
  text-underline-offset: 3px;
}

.inspector-slot-name.is-link:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 2px;
}

.inspector-slot-value {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
}

/* flowgate.v02.0070 T#2 (B2): the unreadable-body mark — a glyph the size of the state badge's words, in the
   warning colour; it never gives way, so the state badge before it stays the line's main state. */
.inspector-slot-unreadable {
  flex: 0 0 auto;
  color: var(--fg2-status-warning-fg);
  font-size: 10.5px;
  line-height: 1;
}

/* The state badge (0056 F1): a short word with the tone colours of InspectorStatusChip; drawn only when
   the state says something. */
.inspector-slot-state {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  max-width: 100%;
  height: 18px;
  padding: 0 7px;
  overflow: hidden;
  border: 1px solid #2c4560;
  border-radius: var(--fg2-control-radius-pill);
  background: #14243a;
  color: #8ea4bb;
  font-size: 10.5px;
  font-weight: 700;
  line-height: 1;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.inspector-slot-state.is-info {
  border-color: #2d5f9b;
  background: #10294a;
  color: #8cc0ff;
}

.inspector-slot-state.is-running {
  border-color: #5e4b9e;
  background: #201a3c;
  color: #c9b6ff;
}

.inspector-slot-state.is-warning {
  border-color: #7b5a17;
  background: #3b2a0d;
  color: #ffc75d;
}

.inspector-slot-state.is-danger {
  border-color: #71313d;
  background: #3a1c24;
  color: #ff9eae;
}

.inspector-slot-state.is-success {
  border-color: #246c53;
  background: #0d2b22;
  color: #72e4b8;
}

.inspector-slot-state.is-hold {
  border-color: #3a5a7a;
  background: #13293f;
  color: #9fc4e8;
}

/* Settled: present but not what the operator looks for. */
.inspector-slot-state.is-quiet {
  opacity: 0.7;
}

/* Why a line has no document: plain muted words, no badge frame. */
.inspector-slot-state.is-empty-text {
  height: auto;
  padding: 0;
  border: 0;
  background: none;
  color: #7189a2;
  font-size: 11.5px;
  font-weight: 500;
}

.inspector-slot-summary {
  overflow: hidden;
  color: #9cb1c7;
  font-size: 11.5px;
  text-overflow: ellipsis;
}

.inspector-slot-progress {
  display: inline-block;
  flex: 0 0 auto;
  width: 120px;
  height: 6px;
  overflow: hidden;
  border-radius: 9px;
  background: #1b2f48;
}

.inspector-slot-progress i {
  display: block;
  height: 100%;
  background: linear-gradient(90deg, #8b5cf6, #c4b5fd);
}

.inspector-slot-buttons {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
  min-width: 0;
}

.inspector-slot.is-empty {
  border-style: dashed;
  background: transparent;
}

.inspector-slot.is-empty .inspector-slot-name {
  color: #9cb1c7;
}

/* 0017-CH: a document without a body. flowgate.v02.0070 T#4 (B4): the dashed frame is the line without a document's;
   a blank line keeps the usual frame (the document is there) and says "본문 비어 있음" in the muted words. */
.inspector-slot.is-blank {
  border-color: #33506e;
  background: transparent;
}

.inspector-slot.is-blank .inspector-slot-name {
  color: #7f97b0;
  font-weight: 600;
}

.inspector-slot.is-focus {
  border-color: #7b5a17;
  box-shadow: inset 3px 0 0 #f59e0b;
}

.inspector-slot.is-danger {
  border-color: #6e2c38;
  box-shadow: inset 3px 0 0 #ef4444;
}

/* flowgate.v02.0056 F2-1: who writes the line — muted, before the state; it gives way first when narrow. */
.inspector-slot-author {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  color: var(--fg2-muted);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* flowgate.v02.0070 T#2 (B2): the 264px Inspector (a 236px list) — tighter gaps on every line, so the buttons keep
   lining up from line to line and a warning line with all its buttons still shows the start of its name. */
@container (max-width: 240px) {
  .inspector-slot {
    column-gap: 4px;
    padding: 0 6px 0 8px;
  }

  .inspector-slot-buttons {
    gap: 2px;
  }

  .inspector-slot-state {
    padding: 0 5px;
  }
}
</style>
