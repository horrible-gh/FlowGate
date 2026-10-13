<template>
  <!-- flowgate.v02.0051 — the v7 documents tab body of a card (0035 insp.js docPane): the card's slot
       lines, one InspectorSlotRow each, with the buttons on the right — 🛡 n (review rounds) and
       "{nav} ↗" (go somewhere else). Display only: the card feature builds the lines (which slots the
       card has, what each shows, which buttons are usable) and hears the presses as events; a button
       whose operation is not connected is shown disabled with its reason (0051-R §2.3), never left out.
       A feature may hand its own test ids per line (`testIds`) and may render a line's summary itself
       through the `summary` scoped slot.
       flowgate.v02.0053 T#3 — opening shows the document in its document window in this tab
       (openDocument). With `editable`, a line that opens also has ✎, which opens the same window straight
       in its editor (an `edit` event).
       flowgate.v02.0053 T#5 (0053 CH) — `name · state · [document icon] · ✎`: the document icon
       (tooltip 열기) replaces ↗, and the line's name opens the document as well (both an `open` event).
       flowgate.v02.0056 F1 (0056 CH §2) — a line that opens also has the history button (a clock, before
       the document icon): the document's 검수·반려 이력 window (a `history` event). A line may leave it out
       (`history: false`) or hand the number of entries it knows (`historyCount`).
       flowgate.v02.0056 F2-1 — a line may carry its `author` (the row names the instruction line's).
       flowgate.v02.0056 F2-2 (O3 0009-TR §4) — the three states of a card line: no document ("문서 없음" with
       [작성]), a document without a body (`blank`: "비어 있음" — its name and document icon open it straight in
       the editor) and a usual line. A line carries its row actions (`actions`, the docRowActions list: [작성] ·
       [AI 호출], an `action` event with the key) and its history number with the AI review / rejection split
       for the history button's hover text (`historyDetail`).
       flowgate.v02.0056 (0017-CH) — [AI 호출] is a robot icon button (its words the tooltip, a spinner while it runs).
       A blank line's document icon is the hollow outline (no "비어 있음" words). -->
  <div class="inspector-slot-list" data-test="inspector-slot-list">
    <InspectorSlotRow
      v-for="line in slots"
      :key="line.key ?? line.name"
      :kind="line.kind ?? 'document'"
      :name="line.name"
      :id="line.id ?? null"
      :openable="line.open === 'enabled'"
      :empty="line.empty ?? false"
      :empty-text="line.emptyText ?? null"
      :state="line.state ?? null"
      :summary="line.summary ?? null"
      :progress="line.progress ?? null"
      :focus="line.focus ?? false"
      :danger="line.danger ?? false"
      :test-ids="line.testIds ?? null"
      :author="line.author ?? null"
      :blank="line.blank ?? false"
      :data-slot="line.key ?? line.name"
      @open="open(line)"
    >
      <template v-if="$slots.summary" #summary>
        <slot name="summary" :line="line" />
      </template>
      <InspectorReviewBadge
        v-if="line.review"
        :count="line.review.count"
        :state="line.review.state"
        :remain="line.review.remain ?? null"
        :disabled="line.review.disabled ?? false"
        @click="emit('review', line)"
      />
      <Button
        v-if="line.nav"
        size="sm"
        variant="secondary"
        :disabled="line.nav.disabled ?? false"
        class="inspector-slot-nav"
        :title="line.nav.title ?? undefined"
        :data-test="line.testIds?.nav ?? 'inspector-slot-nav'"
        @click="emit('nav', line)"
      >{{ line.nav.label }} ↗</Button>
      <template v-for="action in line.actions ?? []" :key="action.key">
        <!-- 0017-CH: [AI 호출] is the robot icon (Phosphor Icons, MIT — the same glyph as client/shared/iconData
             `robot`), its words the tooltip; a running call turns it into a spinner. -->
        <IconButton
          v-if="action.key === 'ai_invoke'"
          :label="actionLabel(action)"
          :title="actionLabel(action)"
          variant="secondary"
          size="sm"
          class="inspector-slot-action inspector-slot-ai"
          :class="`is-${action.state}`"
          data-test="inspector-slot-action"
          data-icon="robot"
          :data-action="action.key"
          :data-state="action.state"
          @click="emit('action', line, action.key)"
        >
          <span v-if="action.state === 'running'" class="inspector-slot-ai-spinner" aria-hidden="true" />
          <svg v-else viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false">
            <path :d="ROBOT_PATH" />
          </svg>
        </IconButton>
        <Button
          v-else
          size="sm"
          variant="secondary"
          class="inspector-slot-action"
          :class="`is-${action.state}`"
          :title="actionLabel(action)"
          data-test="inspector-slot-action"
          :data-action="action.key"
          :data-state="action.state"
          @click="emit('action', line, action.key)"
        >{{ actionLabel(action) }}</Button>
      </template>
      <InspectorHistoryButton
        v-if="line.open === 'enabled' && line.id && line.history !== false"
        :count="line.historyCount ?? null"
        :title="historyTitle(line)"
        data-test="inspector-slot-history"
        @click="emit('history', line)"
      />
      <InspectorOpenButton
        v-if="line.open && line.open !== 'none'"
        :title="line.open === 'disabled' ? (line.openReason ?? t('gui.designer.inspector.unconnected')) : null"
        :disabled="line.open === 'disabled'"
        :hollow="!!line.blank && !line.empty"
        :data-test="line.testIds?.open ?? 'inspector-slot-open'"
        @click="open(line)"
      />
      <IconButton
        v-if="editable && line.open === 'enabled'"
        :label="t('gui.designer.inspector.slot.edit')"
        :title="t('gui.designer.inspector.slot.edit')"
        variant="secondary"
        size="sm"
        data-test="inspector-slot-edit"
        @click="emit('edit', line)"
      >✎</IconButton>
    </InspectorSlotRow>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import Button from '../../../../components/ui/Button.vue'
import IconButton from '../../../../components/ui/IconButton.vue'
import InspectorHistoryButton from './InspectorHistoryButton.vue'
import InspectorOpenButton from './InspectorOpenButton.vue'
import InspectorReviewBadge, { type InspectorReviewState } from './InspectorReviewBadge.vue'
import InspectorSlotRow, { type InspectorSlotState, type InspectorSlotTestIds } from './InspectorSlotRow.vue'
import type { SlotAction, SlotActionKey } from './slotStateView'

export type { SlotAction, SlotActionKey }

/** Phosphor Icons `robot` (regular, 256 viewBox; MIT): the [AI 호출] icon. */
const ROBOT_PATH =
  'M200,48H136V16a8,8,0,0,0-16,0V48H56A32,32,0,0,0,24,80V192a32,32,0,0,0,32,32H200a32,32,0,0,0,32-32V80A32,32,0,0,0,200,48Zm16,144a16,16,0,0,1-16,16H56a16,16,0,0,1-16-16V80A16,16,0,0,1,56,64H200a16,16,0,0,1,16,16Zm-52-56H92a28,28,0,0,0,0,56h72a28,28,0,0,0,0-56Zm-24,16v24H116V152ZM80,164a12,12,0,0,1,12-12h8v24H92A12,12,0,0,1,80,164Zm84,12h-8V152h8a12,12,0,0,1,0,24ZM72,108a12,12,0,1,1,12,12A12,12,0,0,1,72,108Zm88,0a12,12,0,1,1,12,12A12,12,0,0,1,160,108Z'

/** A button that exists on the line: usable, or shown disabled (its operation is not connected). */
export type InspectorSlotButton = 'enabled' | 'disabled' | 'none'

/** One slot line as a card feature hands it over (every field but `name` optional). */
export interface InspectorSlotView {
  /** Stable key (data-slot); the name when absent. */
  readonly key?: string
  readonly kind?: 'document' | 'result'
  readonly name: string
  readonly id?: string | null
  readonly empty?: boolean
  /** Why the line has no document (shown in the line); the common wording otherwise. */
  readonly emptyText?: string | null
  readonly state?: InspectorSlotState | null
  readonly summary?: string | null
  readonly progress?: number | null
  readonly focus?: boolean
  readonly danger?: boolean
  /** 🛡 n: AI review rounds of the line's document. */
  readonly review?: { readonly count: number; readonly state: InspectorReviewState; readonly remain?: number | null; readonly disabled?: boolean } | null
  /** "{label} ↗": go somewhere else (a Host intent). */
  readonly nav?: { readonly label: string; readonly disabled?: boolean; readonly title?: string | null } | null
  /** The document icon (and the name, when enabled): open the line's document. */
  readonly open?: InspectorSlotButton
  readonly openReason?: string | null
  /** flowgate.v02.0056 F1 — the history button (default: on every line that opens); false leaves it out. */
  readonly history?: boolean
  /** Entries in the line's review history when the feature knows them (a number on the history button). */
  readonly historyCount?: number | null
  /** flowgate.v02.0056 F2-1: who writes the line (the card line's `author`, as sent); the row names some. */
  readonly author?: string | null
  /** flowgate.v02.0056 F2-2: the card line (its slot) behind the line, when the line shows one. */
  readonly cardSlot?: string | null
  /** flowgate.v02.0056 F2-2: the line's document has no body yet ("비어 있음"; opening it opens its editor). */
  readonly blank?: boolean
  /** flowgate.v02.0056 F2-2: the AI reviews and rejections behind `historyCount` (the history button's hover text). */
  readonly historyDetail?: { readonly reviews: number; readonly rejections: number } | null
  /** flowgate.v02.0056 F2-2: the line's row actions ([작성] · [AI 호출]), drawn before the history button. */
  readonly actions?: readonly SlotAction[]
  /** The feature's own test ids for the line's state, summary, nav and open (its specs read them). */
  readonly testIds?: (InspectorSlotTestIds & { readonly nav?: string; readonly open?: string }) | null
}

withDefaults(
  defineProps<{
    slots: readonly InspectorSlotView[]
    /** ✎ (open straight in the editor) on every line that opens. */
    editable?: boolean
  }>(),
  { editable: false },
)

const emit = defineEmits<{
  open: [line: InspectorSlotView]
  edit: [line: InspectorSlotView]
  review: [line: InspectorSlotView]
  nav: [line: InspectorSlotView]
  history: [line: InspectorSlotView]
  /** flowgate.v02.0056 F2-2: a row action was pressed. */
  action: [line: InspectorSlotView, key: SlotActionKey]
}>()

const { t } = useI18n()

/** Opening a line: a line whose document has no body yet opens straight in its editor (flowgate.v02.0056 F2-2). */
function open(line: InspectorSlotView) {
  if (line.blank && !line.empty) emit('edit', line)
  else emit('open', line)
}

/** A row action's words (the common wording; a running one says it runs). */
function actionLabel(action: SlotAction): string {
  return t(`gui.designer.inspector.slot.actions.${action.state === 'running' ? `${action.key}_running` : action.key}`)
}

/** The history button's hover text: the common wording, with the AI review / rejection split when known. */
function historyTitle(line: InspectorSlotView): string | null {
  const detail = line.historyDetail
  return detail ? t('gui.designer.inspector.slot.history_detail', { n: detail.reviews + detail.rejections, reviews: detail.reviews, rejections: detail.rejections }) : null
}
</script>

<style scoped>
.inspector-slot-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
  container-type: inline-size;
}

/* flowgate.v02.0052 T#4 — on a narrow line (264px Inspector) the "{nav} ↗" label gives way first
   (ellipsis), so the document icon and ✎ after it stay in the line. */
.inspector-slot-nav {
  flex: 0 1 auto;
  min-width: 0;
}

.inspector-slot-nav :deep(.ui-btn-content) {
  display: block;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

@container (max-width: 360px) {
  .inspector-slot-nav {
    padding: 0 8px;
  }
}

/* flowgate.v02.0056 F2-2 — a row action ([작성] · [AI 호출]); a running one wears the info status tokens. */
.inspector-slot-action {
  flex: 0 1 auto;
  min-width: 0;
}

.inspector-slot-action :deep(.ui-btn-content) {
  display: block;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.inspector-slot-action.is-running {
  border-color: var(--fg2-status-info-border);
  background: var(--fg2-status-info-bg);
  color: var(--fg2-status-info-fg);
}

/* 0017-CH: the robot icon of [AI 호출]; a running call spins in its place. */
.inspector-slot-action.inspector-slot-ai {
  flex: 0 0 auto;
  color: #9fc4e8;
}

.inspector-slot-action.inspector-slot-ai :deep(svg) {
  width: 16px;
  height: 16px;
}

.inspector-slot-ai-spinner {
  width: 12px;
  height: 12px;
  box-sizing: border-box;
  border: 2px solid rgba(140, 192, 255, 0.3);
  border-top-color: currentColor;
  border-radius: 50%;
  animation: inspector-slot-ai-spin 0.7s linear infinite;
}

@keyframes inspector-slot-ai-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
