<template>
  <!-- flowgate.v02.0051 — one v7 slot line (0035 insp-v4 `.slot`): the document tab shows every
       card as slot lines — a document slot (지시서 · 레포트 · 요구사항 · 작업계획 · 케이스 (TSC)) or
       a result slot (결과 (TSR) · 확인 결과 · 전개 · AI 위임 · 최종 결과). One grid line, 38px:
       icon box · name · value (state pill + summary, a progress bar, or the empty text) · buttons.
       Display only: the feature decides the values and puts its buttons (🛡 n · ↗ · ＋ · nav) in the
       default slot. `empty` draws the dashed "not yet" line; `focus` marks the line the user should
       look at (amber bar); `danger` the failed one (red bar).
       Stage 2: a feature may hand its own test ids for the state and the summary (`testIds`) and may
       render the summary itself through the `summary` slot (several spans with their own marks). -->
  <div
    class="inspector-slot"
    :class="[`inspector-slot--${kind}`, { 'is-empty': empty, 'is-focus': focus, 'is-danger': danger }]"
    data-test="inspector-slot"
    :data-kind="kind"
    :data-empty="empty ? 'true' : 'false'"
    :data-name="name"
  >
    <span class="inspector-slot-icon" aria-hidden="true">{{ glyph }}</span>
    <span class="inspector-slot-name" :title="id ?? name" data-test="inspector-slot-name">{{ name }}</span>
    <span class="inspector-slot-value">
      <span v-if="empty" class="inspector-slot-empty" data-test="inspector-slot-empty">{{ emptyText ?? t('gui.designer.inspector.slot.empty') }}</span>
      <template v-else>
        <span v-if="state" class="inspector-slot-state" :data-test="testIds?.state ?? undefined">
          <InspectorStatusChip :label="state.label" :tone="state.tone" :title="state.title" :sub="state.sub" />
        </span>
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
import InspectorStatusChip, { type InspectorChipTone } from './InspectorStatusChip.vue'

export interface InspectorSlotState {
  readonly label: string
  readonly tone: InspectorChipTone
  readonly sub?: string | null
  readonly title?: string
}

/** Test ids a feature may put on the line's state wrapper and summary (its own specs read them). */
export interface InspectorSlotTestIds {
  readonly state?: string
  readonly summary?: string
}

const props = withDefaults(
  defineProps<{
    /** document: 📝 (or 🧪 with `form`); result: ▤. */
    kind?: 'document' | 'result'
    name: string
    /** The document id behind the line (hover title); null when there is none yet. */
    id?: string | null
    /** A form document (케이스 (TSC)): the 🧪 icon. */
    form?: boolean
    empty?: boolean
    /** Two-word empty text ("실행 후", "작성 필요"); the common wording otherwise. */
    emptyText?: string | null
    state?: InspectorSlotState | null
    summary?: string | null
    /** 0..100: a progress bar after the summary (a running result). */
    progress?: number | null
    focus?: boolean
    danger?: boolean
    testIds?: InspectorSlotTestIds | null
  }>(),
  { kind: 'document', id: null, form: false, empty: false, emptyText: null, state: null, summary: null, progress: null, focus: false, danger: false, testIds: null },
)

const { t } = useI18n()

const glyph = computed(() => (props.kind === 'result' ? '▤' : props.form ? '🧪' : '📝'))
const clamped = computed(() => Math.max(0, Math.min(100, Math.round(props.progress ?? 0))))
</script>

<style scoped>
.inspector-slot {
  display: grid;
  grid-template-columns: 24px minmax(72px, 104px) minmax(0, 1fr) auto;
  gap: 9px;
  align-items: center;
  box-sizing: border-box;
  height: 38px;
  min-width: 0;
  padding: 0 9px;
  border: 1px solid var(--fg2-line);
  border-radius: 8px;
  background: #0e2034;
}

.inspector-slot-icon {
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  border: 1px solid #285985;
  border-radius: 6px;
  background: #132f52;
  color: #8fc4ff;
  font-size: 12px;
}

.inspector-slot--result .inspector-slot-icon {
  border-color: #4a4f9a;
  background: #1a2246;
  color: #c4c0ff;
}

.inspector-slot-name {
  overflow: hidden;
  color: #dbe7f2;
  font-size: 12px;
  font-weight: 750;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.inspector-slot-value {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
}

.inspector-slot-state {
  display: inline-flex;
  flex: 0 0 auto;
  min-width: 0;
}

.inspector-slot-summary {
  overflow: hidden;
  color: #9cb1c7;
  font-size: 11.5px;
  text-overflow: ellipsis;
}

.inspector-slot-empty {
  color: #7d93ab;
  font-size: 11.5px;
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
  gap: 6px;
}

.inspector-slot.is-empty {
  border-style: dashed;
  background: transparent;
}

.inspector-slot.is-empty .inspector-slot-icon {
  border-style: dashed;
  background: transparent;
  color: #56708b;
}

.inspector-slot.is-focus {
  border-color: #7b5a17;
  box-shadow: inset 3px 0 0 #f59e0b;
}

.inspector-slot.is-danger {
  border-color: #6e2c38;
  box-shadow: inset 3px 0 0 #ef4444;
}

.inspector-slot.is-danger .inspector-slot-empty {
  color: #ffb2bf;
}
</style>
