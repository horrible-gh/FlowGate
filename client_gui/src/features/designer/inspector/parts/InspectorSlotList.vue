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
       A blank line's document icon is the hollow outline (no "비어 있음" words).
       flowgate.v02.0068 T#1 — the `io` scoped slot (`{ line, docId }`): drawn before the buttons of a line that has
       a document — a document line with an id, not empty, not a nav line. The card feature puts the document's
       [업로드][다운로드] group there; result and nav lines and lines without a document get none. -->
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
      :body-state="line.bodyState"
      :data-slot="line.key ?? line.name"
      @open="open(line)"
    >
      <template v-if="$slots.summary" #summary>
        <slot name="summary" :line="line" />
      </template>
      <template v-if="$slots.io && hasDocument(line)" #io>
        <slot name="io" :line="line" :doc-id="line.id!" />
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
        <!-- 0017-CH: [AI 호출] is the robot icon, its words the tooltip; a running call turns it into a spinner
             (InspectorAiButton, flowgate.v02.0094). -->
        <InspectorAiButton
          v-if="action.key === 'ai_invoke'"
          :label="actionLabel(action)"
          :state="action.state"
          data-test="inspector-slot-action"
          :data-action="action.key"
          @click="emit('action', line, action.key)"
        />
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
import InspectorAiButton from './InspectorAiButton.vue'
import InspectorHistoryButton from './InspectorHistoryButton.vue'
import InspectorOpenButton from './InspectorOpenButton.vue'
import InspectorReviewBadge, { type InspectorReviewState } from './InspectorReviewBadge.vue'
import InspectorSlotRow, { type InspectorSlotState, type InspectorSlotTestIds } from './InspectorSlotRow.vue'
import type { SlotAction, SlotActionKey } from './slotStateView'

export type { SlotAction, SlotActionKey }

/** A button that exists on the line: usable, or shown disabled (its operation is not connected). */
export type InspectorSlotButton = 'enabled' | 'disabled' | 'none'

/** One slot line as a card feature hands it over (every field but `name` optional). */
export interface InspectorSlotView {
  /** Stable key (data-slot); the name when absent. */
  readonly key?: string
  readonly kind?: 'document' | 'result'
  readonly name: string
  /**
   * flowgate.v02.0088 F14/F15: the i18n key `name` was made from, when the line's kind is a UI word. UI only: it is
   * handed to the [작성] / [AI 호출] windows so they show the kind in the current language, and is never sent to an
   * API or stored.
   */
  readonly nameKey?: string | null
  readonly id?: string | null
  readonly bodyState?: string | null
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

/** flowgate.v02.0068 T#1: a line that stands for a document of its own (the upload / download group's lines). */
function hasDocument(line: InspectorSlotView): boolean {
  return (line.kind ?? 'document') === 'document' && !!line.id && !line.empty && !line.nav
}

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
</style>
