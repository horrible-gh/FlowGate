<template>
  <!-- flowgate.v02.0051 — one v7 slot line (0035 insp-v4 `.slot`): the document tab shows every card as
       slot lines — a document slot (지시서 · 레포트 · 요구사항 · 작업계획 · 시나리오) or a result slot (확인 결과 ·
       전개 · AI 위임). Display only: the feature decides the values and puts its buttons in the default slot.
       `focus` marks the line the user should look at (amber bar); `danger` the failed one (red bar).
       A feature may hand its own test ids for the state and the summary (`testIds`) and may render the
       summary itself through the `summary` slot.
       flowgate.v02.0053 T#5 (0053 CH) — the line is `name · state dot · [document icon] · ✎`: the state is a
       coloured dot whose wording is its tooltip (no pill text), and the name opens the document too
       (`openable`: underlined on hover, an `open` event). A line without a document (`empty`) draws a hollow
       dot whose tooltip says why — no "not yet" text on the line. -->
  <div
    class="inspector-slot"
    :class="[`inspector-slot--${kind}`, { 'is-empty': empty, 'is-focus': focus, 'is-danger': danger }]"
    data-test="inspector-slot"
    :data-kind="kind"
    :data-empty="empty ? 'true' : 'false'"
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
    <span
      class="inspector-slot-dot"
      :class="`is-${dot.tone}`"
      :role="dot.title ? 'img' : undefined"
      :aria-label="dot.title"
      :aria-hidden="dot.title ? undefined : 'true'"
      :title="dot.title"
      :data-test="testIds?.state ?? 'inspector-slot-state'"
      :data-tone="dot.tone"
    />
    <span class="inspector-slot-value">
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
      <slot />
    </span>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { InspectorChipTone } from './InspectorStatusChip.vue'

export interface InspectorSlotState {
  readonly label: string
  readonly tone: InspectorChipTone
  /** More hover text after the label (a reason, "2/4"). */
  readonly sub?: string | null
  readonly title?: string
}

/** Test ids a feature may put on the line's state dot and summary (its own specs read them). */
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
    /** The name opens the document (an `open` event). */
    openable?: boolean
    /** No document behind the line: a hollow dot, `emptyText` its tooltip. */
    empty?: boolean
    /** Why the line has no document (the hollow dot's tooltip); the common wording otherwise. */
    emptyText?: string | null
    state?: InspectorSlotState | null
    summary?: string | null
    /** 0..100: a progress bar after the summary (a running result). */
    progress?: number | null
    focus?: boolean
    danger?: boolean
    testIds?: InspectorSlotTestIds | null
  }>(),
  { kind: 'document', id: null, openable: false, empty: false, emptyText: null, state: null, summary: null, progress: null, focus: false, danger: false, testIds: null },
)

const emit = defineEmits<{ open: [] }>()

const { t } = useI18n()

/**
 * The dot: the state's tone and wording. A line without a document is a hollow `none` dot saying why; a
 * document whose state is not known is a plain neutral dot.
 */
const dot = computed<{ tone: InspectorChipTone | 'none'; title: string | undefined }>(() => {
  const state = props.state
  if (props.empty) return { tone: 'none', title: props.emptyText ?? t('gui.designer.inspector.slot.none') }
  if (!state) return { tone: 'neutral', title: undefined }
  const more = [state.sub, state.title].filter((text): text is string => !!text)
  return { tone: state.tone, title: [state.label, ...more].join(' · ') }
})

const clamped = computed(() => Math.max(0, Math.min(100, Math.round(props.progress ?? 0))))
</script>

<style scoped>
.inspector-slot {
  display: grid;
  /* T#4: the buttons column may shrink below its content on a narrow Inspector (264px); the line's
     nav label then ellipsises (InspectorSlotList) and the document icon / ✎ keep their place. */
  grid-template-columns: minmax(72px, 120px) 10px minmax(0, 1fr) minmax(0, max-content);
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
    grid-template-columns: 72px 10px minmax(0, 1fr) minmax(0, max-content);
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

/* The state dot (0053 CH: colour, the wording in the tooltip). Tones follow InspectorStatusChip. */
.inspector-slot-dot {
  width: 10px;
  height: 10px;
  box-sizing: border-box;
  border-radius: 50%;
  background: #8ea4bb;
}

.inspector-slot-dot.is-info {
  background: #8cc0ff;
}

.inspector-slot-dot.is-running {
  background: #c9b6ff;
}

.inspector-slot-dot.is-warning {
  background: #ffc75d;
}

.inspector-slot-dot.is-danger {
  background: #ff9eae;
}

.inspector-slot-dot.is-success {
  background: #72e4b8;
}

.inspector-slot-dot.is-hold {
  background: #9fc4e8;
}

.inspector-slot-dot.is-none {
  border: 1.5px solid #56708b;
  background: transparent;
}

.inspector-slot-value {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
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

.inspector-slot.is-focus {
  border-color: #7b5a17;
  box-shadow: inset 3px 0 0 #f59e0b;
}

.inspector-slot.is-danger {
  border-color: #6e2c38;
  box-shadow: inset 3px 0 0 #ef4444;
}
</style>
