<template>
  <div class="wpe" data-test="wp-edit-body" :data-phase="phase">
    <p v-if="phase === 'loading'" class="wpe-line" data-test="wp-edit-loading">{{ t('loading') }}</p>

    <div v-else-if="phase === 'error'" class="wpe-state" data-test="wp-edit-error" :data-kind="loadError">
      <p class="wpe-line is-danger">{{ t(`error.${loadError ?? 'other'}`) }}</p>
      <Button size="sm" variant="secondary" data-test="wp-edit-retry" @click="editor.load()">{{ t('retry') }}</Button>
    </div>

    <div v-else-if="phase === 'unreadable'" class="wpe-state" data-test="wp-edit-unreadable">
      <p class="wpe-line is-warning">{{ t('unreadable') }}</p>
      <pre v-if="unreadableRaw" class="wpe-raw" data-test="wp-edit-raw">{{ unreadableRaw }}</pre>
      <Button size="sm" variant="secondary" data-test="wp-edit-retry" @click="editor.load()">{{ t('retry') }}</Button>
    </div>

    <template v-else-if="draft && view">
      <p v-if="lockReason" class="wpe-line is-locked" data-test="wp-edit-lock" :data-reason="lockReason">
        <span aria-hidden="true">🔒</span>{{ t(`lock.${lockReason}`) }}
      </p>
      <p v-if="reloading" class="wpe-line" role="status" data-test="wp-edit-reloading">{{ t('reloading') }}</p>

      <div v-if="failure?.kind === 'conflict'" class="wpe-banner is-danger" role="alert" data-test="wp-edit-conflict">
        <span>{{ t('conflict', { who: failure.updatedBy ?? '?', when: failure.updatedAt ?? '' }) }}</span>
        <Button size="sm" variant="secondary" data-test="wp-edit-conflict-reload" @click="emit('reload')">{{ t('retry') }}</Button>
      </div>
      <div v-else-if="externalRevision !== null" class="wpe-banner is-warning" role="status" data-test="wp-edit-external">
        <span>{{ t('external', { n: externalRevision }) }}</span>
        <Button size="sm" variant="secondary" data-test="wp-edit-external-reload" @click="emit('reload')">{{ t('retry') }}</Button>
      </div>
      <div v-if="capability" class="wpe-banner is-warning" role="alert" data-test="wp-edit-capability">
        <div class="wpe-banner-text">
          <span>{{ t('capability.title') }}</span>
          <ul class="wpe-findings">
            <li v-for="finding in capability" :key="finding.stepKey" data-test="wp-edit-capability-item">
              {{ t('capability.item', { key: findingLabel(finding), name: finding.providerName ?? finding.providerId ?? '?', missing: finding.missing.join(', ') }) }}
            </li>
          </ul>
        </div>
        <span class="wpe-banner-actions">
          <Button size="sm" variant="secondary" :disabled="saving" data-test="wp-edit-capability-cancel" @click="editor.cancelCapability()">{{ t('capability.cancel') }}</Button>
          <Button size="sm" variant="primary" :loading="saving" data-test="wp-edit-capability-confirm" @click="editor.confirmCapability()">{{ t('capability.confirm') }}</Button>
        </span>
      </div>
      <div v-if="failure && failure.kind !== 'conflict'" class="wpe-banner is-danger" role="alert" data-test="wp-edit-failure" :data-kind="failure.kind">
        <div class="wpe-banner-text">
          <span>{{ t(`failure.${failure.kind}`) }}<template v-if="failure.message && failure.kind !== 'validation'"> — {{ failure.message }}</template></span>
          <ul v-if="topErrors.length || hiddenErrors.length" class="wpe-findings">
            <li v-for="(error, index) in topErrors" :key="index" data-test="wp-edit-top-error">{{ renderError(error) }}</li>
            <li v-for="(error, index) in hiddenErrors" :key="`hidden-${index}`" data-test="wp-edit-hidden-error">{{ t('hidden_step') }} — {{ renderError(error) }}</li>
          </ul>
        </div>
      </div>

      <!-- ① quantities -->
      <section class="wpe-section" data-test="wp-edit-quantities">
        <header class="wpe-heading">
          <h4>{{ t('quantities') }}</h4>
          <span class="wpe-muted" data-test="wp-edit-totals">{{ t('totals', { design: totals.designSheets, work: totals.workSets, steps: totals.steps }) }}</span>
        </header>
        <ul class="wpe-boxes">
          <li
            v-for="box in boxes"
            :key="box.type"
            class="wpe-box"
            :class="{ 'is-zero': box.count === 0 }"
            data-test="wp-edit-quantity"
            :data-type="box.type"
            :data-count="box.count"
          >
            <WorkPlanTypeIcon :type="box.type" />
            <span class="wpe-box-name">{{ quantityName(box) }}</span>
            <span class="wpe-stepper">
              <IconButton
                size="sm"
                variant="secondary"
                :label="t('decrease', { name: quantityName(box) })"
                :disabled="inputsLocked || box.count <= COUNT_MIN"
                data-test="wp-edit-quantity-dec"
                @click="editor.setQuantity(box.type, box.count - 1)"
              ><span aria-hidden="true">−</span></IconButton>
              <span class="wpe-box-count" data-test="wp-edit-quantity-count">{{ box.count }}</span>
              <IconButton
                size="sm"
                variant="secondary"
                :label="t('increase', { name: quantityName(box) })"
                :disabled="inputsLocked || box.count >= COUNT_MAX"
                data-test="wp-edit-quantity-inc"
                @click="editor.setQuantity(box.type, box.count + 1)"
              ><span aria-hidden="true">+</span></IconButton>
            </span>
            <span class="wpe-muted">{{ gt(`gui.document_window.work_plan.unit.${box.unit}`) }}</span>
          </li>
        </ul>
      </section>

      <!-- ② defaults -->
      <section class="wpe-section wpe-defaults" data-test="wp-edit-defaults">
        <span class="wpe-label">{{ t('defaults.label') }}</span>
        <Select
          class="wpe-provider"
          :model-value="draft.body.defaults.provider_id ?? ''"
          :options="aiOptions"
          :disabled="inputsLocked"
          :aria-label="t('defaults.provider')"
          data-test="wp-edit-default-provider"
          @update:model-value="(value: string) => editor.setDefaultProvider(value || null)"
        />
        <span class="wpe-note">
          <Input
            :model-value="draft.body.defaults.note"
            :placeholder="t('defaults.note')"
            :disabled="inputsLocked"
            :invalid="draft.body.defaults.note.length > view.noteMaxChars"
            :aria-label="t('defaults.note')"
            data-test="wp-edit-default-note"
            @update:model-value="(value: string) => editor.setDefaultNote(value)"
          />
          <small class="wpe-count" :class="{ 'is-over': draft.body.defaults.note.length > view.noteMaxChars }">{{ t('note.count', { current: draft.body.defaults.note.length, max: view.noteMaxChars }) }}</small>
        </span>
        <Button size="sm" variant="secondary" :disabled="inputsLocked" :title="t('defaults.apply_hint')" data-test="wp-edit-apply-defaults" @click="editor.applyDefaults()">
          {{ t('defaults.apply') }}
        </Button>
      </section>

      <!-- ③ steps by card: one section per shown card, folded by default -->
      <section class="wpe-section" data-test="wp-edit-steps">
        <header class="wpe-heading">
          <h4>{{ t('steps') }}</h4>
          <span v-if="unassignedSplit.shown > 0" class="wpe-warning" data-test="wp-edit-unassigned">{{ t('unassigned', { n: unassignedSplit.shown }) }}</span>
          <span v-if="unassignedSplit.hidden > 0" class="wpe-warning" data-test="wp-edit-unassigned-hidden">{{ t('unassigned_hidden', { n: unassignedSplit.hidden }) }}</span>
          <span class="wpe-muted">{{ t('steps_total', { n: totals.steps }) }}</span>
        </header>
        <p v-if="shownCards.length === 0" class="wpe-line" data-test="wp-edit-empty">{{ t('empty') }}</p>
        <ol v-else class="wpe-cards" :class="{ 'is-dragging': dragged !== null }">
          <li
            v-for="shown in shownCards"
            :key="shown.card.id"
            class="wpe-card"
            :class="{ 'is-open': isOpen(shown.card.id), 'is-started': editor.cardStarted(shown.card.id), 'is-drag-over': dragged !== null && dragOver === shown.card.id && dragged !== shown.card.id }"
            data-test="wp-edit-card"
            :data-card="shown.card.id"
            :data-started="editor.cardStarted(shown.card.id) ? 'true' : 'false'"
            :data-expanded="isOpen(shown.card.id) ? 'true' : 'false'"
            @dragover.prevent="onDragOver(shown.card.id)"
            @drop.prevent="onDrop(shown.position)"
          >
            <div class="wpe-card-header" data-test="wp-edit-card-header" @click="toggleCard(shown.card.id)">
              <span class="wpe-card-controls" @click.stop>
                <span
                  class="wpe-handle"
                  :class="{ 'is-disabled': !canDrag(shown.card.id) }"
                  :draggable="canDrag(shown.card.id)"
                  :title="editor.cardStarted(shown.card.id) ? t('card.started') : t('card.drag')"
                  data-test="wp-edit-card-handle"
                  @dragstart="onDragStart($event, shown.card.id)"
                  @dragend="onDragEnd"
                ><span aria-hidden="true">{{ editor.cardStarted(shown.card.id) ? '🔒' : '⠿' }}</span></span>
              </span>
              <button
                type="button"
                class="wpe-card-toggle"
                :aria-expanded="isOpen(shown.card.id) ? 'true' : 'false'"
                :aria-controls="cardBodyId(shown.card.id)"
                :title="isOpen(shown.card.id) ? t('card.collapse') : t('card.expand')"
                data-test="wp-edit-card-toggle"
              >
                <span class="wpe-caret" aria-hidden="true">{{ isOpen(shown.card.id) ? '▾' : '▸' }}</span>
                <WorkPlanTypeIcon :type="shown.card.steps[0].type" />
                <span class="wpe-card-name" data-test="wp-edit-card-name">{{ cardName(shown.card) }}</span>
                <span v-if="!isOpen(shown.card.id)" class="wpe-card-summary" data-test="wp-edit-card-summary">
                  <span v-for="row in shown.rows" :key="row.rowId" class="wpe-card-doc" data-test="wp-edit-card-doc">
                    {{ typeName(row.step.type) }} <span class="wpe-muted">{{ stepAiLabel(row.step) }}</span>
                  </span>
                </span>
              </button>
              <span v-if="cardHasErrors(shown)" class="wpe-card-error" role="img" :aria-label="t('card.has_errors')" :title="t('card.has_errors')" data-test="wp-edit-card-error">!</span>
              <span class="wpe-card-controls" @click.stop>
                <IconButton size="sm" :label="t('card.up')" :title="t('card.up')" :disabled="!editor.canMoveCardBy(shown.position, -1)" data-test="wp-edit-card-up" @click="editor.moveCardBy(shown.position, -1)">
                  <span aria-hidden="true">▲</span>
                </IconButton>
                <IconButton size="sm" :label="t('card.down')" :title="t('card.down')" :disabled="!editor.canMoveCardBy(shown.position, 1)" data-test="wp-edit-card-down" @click="editor.moveCardBy(shown.position, 1)">
                  <span aria-hidden="true">▼</span>
                </IconButton>
              </span>
            </div>
            <div v-show="isOpen(shown.card.id)" :id="cardBodyId(shown.card.id)" class="wpe-rows" data-test="wp-edit-card-body">
              <div
                v-for="row in shown.rows"
                :key="row.rowId"
                class="wpe-row"
                :class="{ 'is-result': row.step.pair_role === 'result', 'is-locked': row.step.locked, 'has-error': !!rowErrors[row.rowId]?.length }"
                data-test="wp-edit-step"
                :data-row="row.rowId"
                :data-step="row.step.key"
              >
                <span class="wpe-no">{{ row.no }}</span>
                <span class="wpe-type" data-test="wp-edit-step-type">
                  {{ typeName(row.step.type) }}
                  <small class="wpe-muted">{{ placeOf(row.step) }}</small>
                </span>
                <span v-if="row.step.locked" class="wpe-provider wpe-muted" data-test="wp-edit-step-server">{{ t('provider.server') }}</span>
                <Select
                  v-else
                  class="wpe-provider"
                  :model-value="row.step.provider_id ?? ''"
                  :options="aiOptions"
                  :disabled="inputsLocked"
                  :aria-label="t('provider.label', { n: row.no })"
                  data-test="wp-edit-step-provider"
                  @update:model-value="(value: string) => editor.setStepProvider(row.rowId, value || null)"
                />
                <span v-if="row.step.locked" class="wpe-review wpe-muted" data-test="wp-edit-step-review">—</span>
                <Button
                  v-else
                  size="sm"
                  variant="secondary"
                  class="wpe-review"
                  :class="{ 'is-active': (row.step.review_count ?? 0) !== 0, 'is-open': reviewOpenRow === row.rowId }"
                  :disabled="inputsLocked"
                  :title="t('review.open', { n: row.no })"
                  aria-haspopup="dialog"
                  :aria-expanded="reviewOpenRow === row.rowId ? 'true' : 'false'"
                  :aria-controls="reviewOpenRow === row.rowId ? reviewPopover?.popoverId : undefined"
                  data-test="wp-edit-step-review"
                  @pointerdown="reviewPopover?.press(row.rowId)"
                  @click="openReview($event, row.rowId)"
                >
                  <span class="wpe-review-text" data-test="wp-edit-step-review-summary">{{ reviewSummary(row.step) }}</span>
                  <span class="wpe-caret" aria-hidden="true">▾</span>
                </Button>
                <span class="wpe-note">
                  <Input
                    v-if="!row.step.locked"
                    :model-value="row.step.note ?? ''"
                    :placeholder="t('note.placeholder')"
                    :disabled="inputsLocked"
                    :invalid="(row.step.note ?? '').length > view.noteMaxChars"
                    :aria-label="t('note.label', { n: row.no })"
                    data-test="wp-edit-step-note"
                    @update:model-value="(value: string) => editor.setStepNote(row.rowId, value)"
                  />
                  <small v-if="!row.step.locked && (row.step.note ?? '').length > view.noteMaxChars * 0.8" class="wpe-count" :class="{ 'is-over': (row.step.note ?? '').length > view.noteMaxChars }">
                    {{ t('note.count', { current: (row.step.note ?? '').length, max: view.noteMaxChars }) }}
                  </small>
                </span>
                <span v-if="stepHasInstruction(row.step)" class="wpe-mark" :title="t('instruction')" data-test="wp-edit-step-instruction">≡</span>
                <IconButton
                  size="sm"
                  :label="pencilTitle(row)"
                  :title="pencilTitle(row)"
                  :disabled="row.step.locked || !stepEditAvailable"
                  data-test="wp-edit-step-edit"
                  @click="emit('edit-step', row.rowId)"
                ><span aria-hidden="true">✎</span></IconButton>
                <span v-if="rowErrors[row.rowId]?.length" class="wpe-row-errors" role="alert" data-test="wp-edit-step-errors">
                  <span v-for="(error, index) in rowErrors[row.rowId]" :key="index">{{ renderError(error) }}</span>
                </span>
              </div>
            </div>
          </li>
          <li
            v-if="dragged !== null"
            class="wpe-drop-end"
            :class="{ 'is-drag-over': dragOver === DROP_END }"
            data-test="wp-edit-card-drop-end"
            @dragover.prevent="dragOver = DROP_END"
            @drop.prevent="onDrop(shownCards.length)"
          >{{ t('card.drop_end') }}</li>
        </ol>
      </section>
      <WorkPlanReviewPopover ref="reviewPopover" :editor="editor" />
    </template>
  </div>
</template>

<script setup lang="ts">
// flowgate.v02.0072 T#1 — the editing body of the work plan edit window (WorkPlanEditWindow): ① the quantity boxes
// with −/+ (0..20), ② the plan defaults — default AI, default note, [모두 적용], ③ the Steps by card: per row the AI
// picker (v0.2 Select over the project's AIs read with the plan), the review button, the one-line note with its
// server limit, the pre-instruction mark, and ✎. Cards move with ▲▼ or by dragging the grip; a started card is
// pinned. Everything changes the editor's draft only — the server save is the window's [저장].
// ✎ never opens anything here (no inline drawer): it emits `edit-step` with the row's identity and the window hands
// it to the Step window (T#2). Pre-instruction and attachment are edited there, so this body only marks them.
// flowgate.v02.0073 T#2 — the review is edited here, in one place: a row's review button shows its value (검수 없음,
// 2회 · Opus) and opens the window's review popover (WorkPlanReviewPopover: count, reviewer, [완료]) for that row;
// folding that row's card or minimizing the window (`minimized`) closes it.
// The body has no colors of its own: v0.2 Button / IconButton / Select / Input and the theme tokens; a type's icon is
// the Designer Palette's (WorkPlanTypeIcon).
//
// flowgate.v02.0073 T#1 — DS and T2 are not drawn (no quantity box, no card; the summary counts what is drawn) while
// the body keeps them whole. Each shown card is a section: its header has the drag grip, the type icon, the card's
// name (its quantity name and ordinal, e.g. 작업 1 — never a Step key) and ▲▼; it starts folded, and a click on the
// header folds / unfolds it (screen state only, allowed while locked). Folded, it lists its documents and their AIs.
// ▲▼ and drops use the positions among the shown cards (the editor's moveCard), so hidden cards keep their place.
// Assumptions (no contract in R0001 / the mockup, NR §7(b)): hidden Steps are never named by their key — their
// unassigned count is a separate line, a capability finding or a field error of one says "표시하지 않는 단계".
import { computed, ref, shallowRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '@/components/ui/Button.vue'
import IconButton from '@/components/ui/IconButton.vue'
import Input from '@/components/ui/Input.vue'
import Select from '@/components/ui/Select.vue'
import { renderWpFieldError, type WpFieldError } from '@shared/workPlanErrors'
import { documentTypeNameKey, workPlanQuantityNameKey } from '@/features/documentWindow/specialDocumentModel'
import type { WorkPlanCapabilityFinding } from '@/features/document'
import WorkPlanReviewPopover from './WorkPlanReviewPopover.vue'
import WorkPlanTypeIcon from './WorkPlanTypeIcon.vue'
import { workPlanWindowMessages } from './messages'
import type { WorkPlanEditor } from './useWorkPlanEditor'
import {
  WORK_PLAN_COUNT_MAX as COUNT_MAX,
  WORK_PLAN_COUNT_MIN as COUNT_MIN,
  hiddenRowIds,
  quantityBoxes,
  shownCardsOf,
  stepHasInstruction,
  stepKeyShown,
  stepPlace,
  totalsOf,
  unassignedByShown,
  unitOf,
  type WorkPlanCard,
  type WorkPlanEditStep,
  type WorkPlanQuantityBox,
  type WorkPlanRowRef,
  type WorkPlanShownCard,
} from './workPlanEditModel'

const props = defineProps<{
  editor: WorkPlanEditor
  /** A Step window is registered (T#2): ✎ is enabled. */
  stepEditAvailable: boolean
  /** The window is minimized (its body is not shown): the review popover closes. */
  minimized?: boolean
}>()

const emit = defineEmits<{
  /** ✎ of a row: the row identity (card id | role) for the Step window. */
  'edit-step': [rowId: string]
  /** Read the plan again (the window asks about unsaved edits first). */
  reload: []
}>()

const { t } = useI18n({ useScope: 'local', messages: workPlanWindowMessages, inheritLocale: true })
const { t: gt } = useI18n({ useScope: 'global' })

const { phase, loadError, unreadableRaw, view, draft, saving, reloading, lockReason, inputsLocked, failure, rowErrors, topErrors, capability, externalRevision } =
  props.editor

const boxes = computed(() => (draft.value ? quantityBoxes(draft.value.body) : []))
const totals = computed(() => (draft.value ? totalsOf(draft.value) : { designSheets: 0, workSets: 0, steps: 0 }))
const shownCards = computed(() => (draft.value ? shownCardsOf(draft.value) : []))
const unassignedSplit = computed(() => (draft.value ? unassignedByShown(draft.value) : { shown: 0, hidden: 0 }))
/** Field errors of Steps that are not drawn: listed on top without their key. */
const hiddenErrors = computed(() => {
  if (!draft.value) return []
  const hidden = hiddenRowIds(draft.value)
  return Object.entries(rowErrors.value).flatMap(([rowId, errors]) => (hidden.has(rowId) ? errors : []))
})

const usedProviders = computed(() => (draft.value ? [draft.value.body.defaults.provider_id, ...draft.value.cards.flatMap((card) => card.steps.map((step) => step.provider_id))] : []))
const unavailable = (name: string) => t('provider.unavailable', { name })
const aiOptions = computed(() => props.editor.providerOptions(usedProviders.value, t('provider.unassigned'), unavailable))

function quantityName(box: WorkPlanQuantityBox): string {
  return gt(`gui.document_window.${workPlanQuantityNameKey(box)}`)
}

function placeOf(step: WorkPlanEditStep): string {
  if (!draft.value) return ''
  const place = stepPlace(draft.value.body, step)
  return gt(`gui.document_window.work_plan.${place.key}`, { n: place.n, total: place.total })
}

function typeName(type: string): string {
  return gt(`gui.document_window.${documentTypeNameKey(type)}`)
}

/** A card's name: its quantity name and ordinal (조사 1, 작업 1, 기본 설계 1). */
function cardName(card: WorkPlanCard): string {
  const head = card.steps[0]
  const unit = draft.value ? unitOf(draft.value.body, head.type) : 'sheet'
  return t('card.name', { name: gt(`gui.document_window.${workPlanQuantityNameKey({ type: head.type, unit })}`), n: head.ordinal })
}

function stepAiLabel(step: WorkPlanEditStep): string {
  if (step.locked) return t('provider.server')
  return step.provider_id ? props.editor.providerName(step.provider_id, step.provider_display_name) : t('provider.unassigned')
}

function findingLabel(finding: WorkPlanCapabilityFinding): string {
  return draft.value && !stepKeyShown(draft.value, finding.stepKey, finding.stepType) ? t('hidden_step') : finding.stepKey
}

// --- folding the card sections (screen state only) -------------------------------------------------------------

/** Per window: two windows may show the same card ids. */
const bodyPrefix = `wpe-card-body-${Math.random().toString(36).slice(2, 10)}`
const opened = shallowRef<ReadonlySet<string>>(new Set())

const isOpen = (cardId: string) => opened.value.has(cardId)
const cardBodyId = (cardId: string) => `${bodyPrefix}-${cardId.replace(/[^\w-]/g, '_')}`

function toggleCard(cardId: string): void {
  const next = new Set(opened.value)
  if (next.delete(cardId)) closeReviewIn(cardId)
  else next.add(cardId)
  opened.value = next
}

function cardHasErrors(shown: WorkPlanShownCard): boolean {
  return shown.rows.some((row) => !!rowErrors.value[row.rowId]?.length)
}

// A save's field errors open the cards they are on, so the marked rows can be seen.
watch(rowErrors, () => {
  const withErrors = shownCards.value.filter(cardHasErrors).map((shown) => shown.card.id)
  if (withErrors.some((id) => !opened.value.has(id))) opened.value = new Set([...opened.value, ...withErrors])
})

function reviewCountLabel(count: number): string {
  if (count === 0) return t('review.none')
  if (count < 0) return t('review.until')
  return t('review.times', { n: count })
}

function reviewSummary(step: WorkPlanEditStep): string {
  const count = step.review_count ?? 0
  if (count === 0) return reviewCountLabel(0)
  const reviewer = step.reviewer_provider_id ? props.editor.providerName(step.reviewer_provider_id, step.reviewer_provider_display_name) : t('review.project_default')
  return t('review.summary', { count: reviewCountLabel(count), reviewer })
}

const reviewPopover = ref<InstanceType<typeof WorkPlanReviewPopover> | null>(null)
const reviewOpenRow = computed(() => reviewPopover.value?.openRowId ?? null)

function openReview(event: MouseEvent, rowId: string): void {
  reviewPopover.value?.toggle(rowId, event.currentTarget as HTMLElement)
}

// The popover goes with its button: the button's card folding (v-show hides the row, the popover is on <body>), from
// the keyboard too, where no press outside closes it.
function closeReviewIn(cardId: string): void {
  const open = reviewOpenRow.value
  if (open && shownCards.value.find((shown) => shown.card.id === cardId)?.rows.some((row) => row.rowId === open)) reviewPopover.value?.close()
}

// ... and the window minimizing (its body hidden).
watch(
  () => props.minimized,
  (minimized) => {
    if (minimized) reviewPopover.value?.close()
  },
)

function pencilTitle(row: WorkPlanRowRef): string {
  if (row.step.locked) return t('pencil.locked')
  if (!props.stepEditAvailable) return t('pencil.unavailable')
  return t(row.step.pair_role === 'result' ? 'pencil.open_report' : 'pencil.open', { n: row.no })
}

function renderError(error: WpFieldError): string {
  return renderWpFieldError(error, (key, params) => gt(key, params ?? {}))
}

// --- dragging a card by its grip (v0.1 0649 T#3) ---------------------------------------------------------------

const DROP_END = '__end__'
const dragged = ref<string | null>(null)
const dragOver = ref<string | null>(null)

function canDrag(cardId: string): boolean {
  return !inputsLocked.value && !props.editor.cardStarted(cardId)
}

function onDragStart(event: DragEvent, cardId: string): void {
  if (!canDrag(cardId)) {
    event.preventDefault()
    return
  }
  dragged.value = cardId
  dragOver.value = null
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = 'move'
    event.dataTransfer.setData('text/plain', cardId)
  }
}

function onDragOver(cardId: string): void {
  if (dragged.value !== null) dragOver.value = cardId
}

/** Drop = the dragged card goes in front of the shown card at shown position `target` (the shown count = the end). */
function onDrop(target: number): void {
  const id = dragged.value
  dragged.value = null
  dragOver.value = null
  if (id === null) return
  const from = shownCards.value.findIndex((shown) => shown.card.id === id)
  if (from < 0) return
  props.editor.moveCard(from, target > from ? target - 1 : target)
}


function onDragEnd(): void {
  dragged.value = null
  dragOver.value = null
}
</script>

<style scoped>
.wpe {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: 0;
  color: var(--fg2-text);
  font-size: 12.5px;
}

.wpe-state {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
}

.wpe-line {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  color: var(--fg2-muted);
  font-size: 12px;
}

.wpe-line.is-danger,
.wpe-row-errors {
  color: var(--fg2-status-danger-fg);
}

.wpe-line.is-warning,
.wpe-warning {
  color: var(--fg2-status-warning-fg);
}

.wpe-line.is-locked {
  color: var(--fg2-status-warning-fg);
}

.wpe-raw {
  width: 100%;
  max-height: 320px;
  margin: 0;
  overflow: auto;
  padding: 10px 12px;
  border: 1px solid var(--fg2-line);
  border-radius: var(--fg2-control-radius-sm);
  background: var(--fg2-bg);
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
}

.wpe-banner {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 10px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius-sm);
  font-size: 12px;
}

.wpe-banner.is-danger {
  border-color: var(--fg2-status-danger-border, var(--fg2-line-strong));
  color: var(--fg2-status-danger-fg);
}

.wpe-banner.is-warning {
  border-color: var(--fg2-status-warning-border, var(--fg2-line-strong));
  color: var(--fg2-status-warning-fg);
}

.wpe-banner-text {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.wpe-banner-actions {
  display: inline-flex;
  flex: none;
  gap: 6px;
}

.wpe-findings {
  margin: 0;
  padding-left: 16px;
}

.wpe-section {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.wpe-heading {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.wpe-heading h4 {
  margin: 0;
  color: var(--fg2-muted);
  font-size: 11.5px;
  font-weight: 600;
}

.wpe-heading > :last-child {
  margin-left: auto;
}

.wpe-muted {
  color: var(--fg2-muted);
  font-size: 11px;
}

.wpe-boxes {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.wpe-box {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 6px;
  border: 1px solid var(--fg2-line);
  border-radius: var(--fg2-control-radius-sm);
}

.wpe-box.is-zero .wpe-box-name,
.wpe-box.is-zero .wpe-box-count {
  color: var(--fg2-muted);
}

.wpe-stepper {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.wpe-box-count {
  min-width: 18px;
  font-weight: 600;
  text-align: center;
}

.wpe-defaults {
  flex-direction: row;
  align-items: center;
  gap: 8px;
}

.wpe-label {
  flex: none;
  color: var(--fg2-muted);
  font-size: 12px;
  font-weight: 650;
}

.wpe-provider {
  flex: 0 0 180px;
  width: 180px;
}

.wpe-note {
  display: flex;
  flex: 1 1 160px;
  flex-direction: column;
  gap: 2px;
  min-width: 120px;
}

.wpe-count {
  align-self: flex-end;
  color: var(--fg2-muted);
  font-size: 10.5px;
}

.wpe-count.is-over {
  color: var(--fg2-status-danger-fg);
}

.wpe-cards {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.wpe-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 4px 6px;
  border: 1px solid var(--fg2-line);
  border-radius: var(--fg2-control-radius-sm);
}

.wpe-card.is-started {
  border-style: dashed;
}

.wpe-card.is-drag-over,
.wpe-drop-end.is-drag-over {
  border-color: var(--fg2-focus);
}

.wpe-card-header {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  cursor: pointer;
}

.wpe-card-controls {
  display: inline-flex;
  flex: none;
  align-items: center;
  gap: 2px;
  cursor: default;
}

.wpe-card-toggle {
  display: flex;
  flex: 1;
  align-items: center;
  gap: 8px;
  min-width: 0;
  padding: 0;
  border: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.wpe-card-toggle:focus-visible {
  border-radius: var(--fg2-control-radius-sm);
  outline: 2px solid var(--fg2-focus);
  outline-offset: 1px;
}

.wpe-caret {
  flex: none;
  width: 10px;
  color: var(--fg2-muted);
}

.wpe-card-name {
  flex: none;
  font-weight: 650;
}

.wpe-card-summary {
  display: flex;
  flex: 1;
  gap: 10px;
  min-width: 0;
  overflow: hidden;
  color: var(--fg2-muted);
  font-size: 11.5px;
  white-space: nowrap;
}

.wpe-card-doc {
  flex: none;
}

.wpe-card-error {
  flex: none;
  color: var(--fg2-status-danger-fg);
  font-weight: 700;
}

.wpe-handle {
  color: var(--fg2-muted);
  cursor: grab;
  user-select: none;
}

.wpe-handle.is-disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.wpe-rows {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  padding: 2px 0 2px 4px;
}


.wpe-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.wpe-row.is-result {
  padding-left: 14px;
}

.wpe-row.is-locked {
  color: var(--fg2-muted);
}

.wpe-row.has-error .wpe-no {
  color: var(--fg2-status-danger-fg);
}

.wpe-no {
  flex: none;
  min-width: 18px;
  color: var(--fg2-muted);
  font-size: 11px;
  text-align: right;
}

.wpe-type {
  flex: 0 0 140px;
  font-weight: 600;
}

.wpe-review {
  flex: 0 0 auto;
  max-width: 200px;
  overflow: hidden;
  color: var(--fg2-muted);
  font-size: 11.5px;
}

.wpe-review.is-active,
.wpe-review.is-open {
  color: var(--fg2-text);
}

.wpe-review :deep(.ui-btn-content) {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.wpe-review-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wpe-mark {
  flex: none;
  color: var(--fg2-muted);
}

.wpe-row-errors {
  display: flex;
  flex-basis: 100%;
  flex-direction: column;
  padding-left: 26px;
  font-size: 11.5px;
}

.wpe-drop-end {
  padding: 6px;
  border: 1px dashed var(--fg2-line);
  border-radius: var(--fg2-control-radius-sm);
  color: var(--fg2-muted);
  text-align: center;
}
</style>
