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
       (`history: false`) or hand the number of entries it knows (`historyCount`). -->
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
      :data-slot="line.key ?? line.name"
      @open="emit('open', line)"
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
      <InspectorHistoryButton
        v-if="line.open === 'enabled' && line.id && line.history !== false"
        :count="line.historyCount ?? null"
        data-test="inspector-slot-history"
        @click="emit('history', line)"
      />
      <InspectorOpenButton
        v-if="line.open && line.open !== 'none'"
        :title="line.open === 'disabled' ? (line.openReason ?? t('gui.designer.inspector.unconnected')) : null"
        :disabled="line.open === 'disabled'"
        :data-test="line.testIds?.open ?? 'inspector-slot-open'"
        @click="emit('open', line)"
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
}>()

const { t } = useI18n()
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
</style>
