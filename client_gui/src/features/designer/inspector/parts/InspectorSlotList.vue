<template>
  <!-- flowgate.v02.0051 — the v7 documents tab body of a card (0035 insp.js docPane): the card's slot
       lines, one InspectorSlotRow each, with the v7 buttons on the right — 🛡 n (review rounds),
       "{nav} ↗" (go somewhere else), ↗ (open) and ＋ (write). Display only: the card feature builds
       the lines (which slots the card has, what each shows, which buttons are usable) and hears the
       presses as events; a button whose operation is not connected is shown disabled with its reason
       (0051-R §2.3), never left out. A feature may hand its own test ids per line (`testIds`) and may
       render a line's summary itself through the `summary` scoped slot. -->
  <div class="inspector-slot-list" data-test="inspector-slot-list">
    <InspectorSlotRow
      v-for="line in slots"
      :key="line.key ?? line.name"
      :kind="line.kind ?? 'document'"
      :name="line.name"
      :id="line.id ?? null"
      :form="line.form ?? false"
      :empty="line.empty ?? false"
      :empty-text="line.emptyText ?? null"
      :state="line.state ?? null"
      :summary="line.summary ?? null"
      :progress="line.progress ?? null"
      :focus="line.focus ?? false"
      :danger="line.danger ?? false"
      :test-ids="line.testIds ?? null"
      :data-slot="line.key ?? line.name"
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
        :title="line.nav.title ?? undefined"
        :data-test="line.testIds?.nav ?? 'inspector-slot-nav'"
        @click="emit('nav', line)"
      >{{ line.nav.label }} ↗</Button>
      <IconButton
        v-if="line.open && line.open !== 'none'"
        :label="t('gui.designer.inspector.slot.open')"
        :title="line.open === 'disabled' ? (line.openReason ?? t('gui.designer.inspector.unconnected')) : t('gui.designer.inspector.slot.open')"
        variant="secondary"
        size="sm"
        :disabled="line.open === 'disabled'"
        :data-test="line.testIds?.open ?? 'inspector-slot-open'"
        @click="emit('open', line)"
      >↗</IconButton>
      <IconButton
        v-if="line.write && line.write !== 'none'"
        :label="t('gui.designer.inspector.slot.write')"
        :title="line.write === 'disabled' ? (line.writeReason ?? t('gui.designer.inspector.unconnected')) : t('gui.designer.inspector.slot.write')"
        variant="secondary"
        size="sm"
        :disabled="line.write === 'disabled'"
        data-test="inspector-slot-write"
        @click="emit('write', line)"
      >＋</IconButton>
    </InspectorSlotRow>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import Button from '../../../../components/ui/Button.vue'
import IconButton from '../../../../components/ui/IconButton.vue'
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
  readonly form?: boolean
  readonly empty?: boolean
  readonly emptyText?: string | null
  readonly state?: InspectorSlotState | null
  readonly summary?: string | null
  readonly progress?: number | null
  readonly focus?: boolean
  readonly danger?: boolean
  readonly review?: { readonly count: number; readonly state: InspectorReviewState; readonly remain?: number | null; readonly disabled?: boolean } | null
  readonly nav?: { readonly label: string; readonly disabled?: boolean; readonly title?: string | null } | null
  readonly open?: InspectorSlotButton
  readonly openReason?: string | null
  readonly write?: InspectorSlotButton
  readonly writeReason?: string | null
  /** The feature's own test ids for the line's state, summary, nav and open (its specs read them). */
  readonly testIds?: (InspectorSlotTestIds & { readonly nav?: string; readonly open?: string }) | null
}

defineProps<{
  slots: readonly InspectorSlotView[]
}>()

const emit = defineEmits<{
  open: [line: InspectorSlotView]
  write: [line: InspectorSlotView]
  review: [line: InspectorSlotView]
  nav: [line: InspectorSlotView]
}>()

const { t } = useI18n()
</script>

<style scoped>
.inspector-slot-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}
</style>
