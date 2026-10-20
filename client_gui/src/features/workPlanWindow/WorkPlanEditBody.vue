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
      <section class="wpe-section wpe-quantities" data-test="wp-edit-quantities">
        <header class="wpe-heading">
          <h4>{{ t('quantities') }}</h4>
          <span class="wpe-quantity-summary" data-test="wp-edit-totals">{{ t('totals', { design: totals.designSheets, work: totals.workSets, steps: totals.steps }) }}</span>
        </header>
        <ul class="wpe-boxes">
          <li
            v-for="box in boxes"
            :key="box.type"
            class="wpe-box"
            :class="{ 'is-zero': box.count === 0, 'is-active': box.count > 0 }"
            data-test="wp-edit-quantity"
            :data-type="box.type"
            :data-count="box.count"
            :title="gt(`gui.document_window.work_plan.unit.${box.unit}`)"
          >
            <WorkPlanTypeIcon :type="box.type" />
            <span class="wpe-box-name">{{ quantityName(box) }}</span>
            <span class="wpe-stepper">
              <IconButton
                size="sm"
                variant="ghost"
                :label="t('decrease', { name: quantityName(box) })"
                :disabled="inputsLocked || box.count <= COUNT_MIN"
                data-test="wp-edit-quantity-dec"
                @click="editor.setQuantity(box.type, box.count - 1)"
              ><span aria-hidden="true">−</span></IconButton>
              <span class="wpe-box-count" data-test="wp-edit-quantity-count">{{ box.count }}</span>
              <IconButton
                size="sm"
                variant="ghost"
                :label="t('increase', { name: quantityName(box) })"
                :disabled="inputsLocked || box.count >= COUNT_MAX"
                data-test="wp-edit-quantity-inc"
                @click="editor.setQuantity(box.type, box.count + 1)"
              ><span aria-hidden="true">+</span></IconButton>
            </span>
          </li>
        </ul>
      </section>

      <!-- ② defaults -->
      <section class="wpe-section wpe-defaults" data-test="wp-edit-defaults">
        <span class="wpe-label"><svg class="wpe-icon" viewBox="0 0 24 24" aria-hidden="true"><path :d="REFRESH_PATH" /></svg>{{ t('defaults.label') }}</span>
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
          <span class="wpe-button-content"><svg class="wpe-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 5 5 9-10" /></svg>{{ t('defaults.apply') }}</span>
        </Button>
      </section>

      <!-- ③ steps by card: one section per shown card, folded by default -->
      <section class="wpe-section" data-test="wp-edit-steps">
        <header class="wpe-heading">
          <h4>{{ t('steps') }}</h4>
          <span class="wpe-muted" data-test="wp-edit-step-totals">{{ t('steps_total', { cards: shownCards.length, n: totals.steps }) }}</span>
          <span class="wpe-heading-spacer" />
          <span v-if="unassignedSplit.shown > 0" class="wpe-warning" data-test="wp-edit-unassigned">{{ t('unassigned', { n: unassignedSplit.shown }) }}</span>
          <span v-if="unassignedSplit.hidden > 0" class="wpe-warning" data-test="wp-edit-unassigned-hidden">{{ t('unassigned_hidden', { n: unassignedSplit.hidden }) }}</span>
        </header>
        <div v-if="shownCards.length > 0" class="wpe-table">
          <div v-show="anyOpen" class="wpe-columns" data-test="wp-edit-columns" aria-hidden="true">
            <span>{{ t('columns.document') }}</span><span>{{ t('columns.provider') }}</span><span>{{ t('columns.review') }}</span><span>{{ t('columns.note') }}</span><span>{{ t('columns.detail') }}</span>
          </div>
          <ol class="wpe-cards" :class="{ 'is-dragging': dragged !== null }">
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
                <button
                  type="button"
                  class="wpe-card-toggle"
                  :aria-expanded="isOpen(shown.card.id) ? 'true' : 'false'"
                  :aria-controls="cardBodyId(shown.card.id)"
                  :title="isOpen(shown.card.id) ? t('card.collapse') : t('card.expand')"
                  data-test="wp-edit-card-toggle"
                >
                  <svg class="wpe-icon wpe-caret" :class="{ 'is-open': isOpen(shown.card.id) }" viewBox="0 0 24 24" aria-hidden="true"><path d="m9 5 7 7-7 7" /></svg>
                  <WorkPlanTypeIcon :type="shown.card.steps[0].type" />
                  <span class="wpe-card-name" data-test="wp-edit-card-name">{{ cardName(shown.card) }}</span>
                  <span class="wpe-card-summary" data-test="wp-edit-card-summary">
                    <span v-for="row in shown.rows" :key="row.rowId" class="wpe-card-doc" data-test="wp-edit-card-doc">
                      {{ documentName(row.step) }}
                    </span>
                  </span>
                </button>
                <span v-if="cardHasErrors(shown)" class="wpe-card-error" role="img" :aria-label="t('card.has_errors')" :title="t('card.has_errors')" data-test="wp-edit-card-error">!</span>
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
                  <IconButton size="sm" :label="t('card.up')" :title="t('card.up')" :disabled="!editor.canMoveCardBy(shown.position, -1)" data-test="wp-edit-card-up" @click="editor.moveCardBy(shown.position, -1)">
                    <svg class="wpe-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m6 14 6-6 6 6" /></svg>
                  </IconButton>
                  <IconButton size="sm" :label="t('card.down')" :title="t('card.down')" :disabled="!editor.canMoveCardBy(shown.position, 1)" data-test="wp-edit-card-down" @click="editor.moveCardBy(shown.position, 1)">
                    <svg class="wpe-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m6 10 6 6 6-6" /></svg>
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
                  <span class="wpe-type" data-test="wp-edit-step-type">
                    <span class="wpe-document-icon"><svg class="wpe-icon" viewBox="0 0 24 24" aria-hidden="true"><path :d="paletteIconPath(row.step.pair_role === 'result' ? 'clipboard' : 'file')" /></svg></span>
                    <span class="wpe-document-copy"><strong>{{ documentName(row.step) }}</strong><small class="wpe-muted">{{ t('document.position', { n: row.no }) }}</small></span>
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
                    <svg class="wpe-icon" viewBox="0 0 24 24" aria-hidden="true"><path :d="paletteIconPath('shield')" /></svg>
                    <span class="wpe-review-text" data-test="wp-edit-step-review-summary">{{ reviewSummary(row.step) }}</span>
                    <svg class="wpe-icon wpe-review-chevron" viewBox="0 0 24 24" aria-hidden="true"><path d="m6 10 6 6 6-6" /></svg>
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
                  <span class="wpe-pencil">
                    <IconButton
                      v-if="omitOf(row).visible"
                      size="sm"
                      :label="omitTitle(row)"
                      :title="omitTitle(row)"
                      :disabled="!omitOf(row).enabled"
                      :aria-busy="omitting === row.rowId ? 'true' : undefined"
                      data-test="wp-edit-step-omit"
                      :data-action="omitOf(row).action"
                      @click="editor.omitStep(row.rowId)"
                    ><svg class="wpe-icon" viewBox="0 0 24 24" aria-hidden="true"><path :d="omitOf(row).action === 'retry' ? REFRESH_PATH : OMIT_PATH" /></svg></IconButton>
                    <span v-if="stepHasInstruction(row.step)" class="wpe-mark" :title="t('instruction')" data-test="wp-edit-step-instruction" />
                    <IconButton
                      size="sm"
                      :label="pencilTitle(row)"
                      :title="pencilTitle(row)"
                      :disabled="row.step.locked || !stepEditAvailable"
                      data-test="wp-edit-step-edit"
                      @click="emit('edit-step', row.rowId)"
                    ><svg class="wpe-icon" viewBox="0 0 24 24" aria-hidden="true"><path :d="paletteIconPath('pencil')" /></svg></IconButton>
                  </span>
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
        </div>
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
// flowgate.v02.0074 T#1 (R §2.1): with every quantity at 0 the 작업 순서 section is its header only — no text says so.
//
// flowgate.v02.0073 T#1 — DS and T2 are not drawn (no quantity box, no card; the summary counts what is drawn) while
// the body keeps them whole. Each shown card is a section: its header has the drag grip, the type icon, the card's
// name (its quantity name and ordinal, e.g. 작업 1 — never a Step key) and ▲▼; it starts folded, and a click on the
// header folds / unfolds it (screen state only, allowed while locked). Its compact document summary stays visible when expanded; AI values remain in each document row.
// ▲▼ and drops use the positions among the shown cards (the editor's moveCard), so hidden cards keep their place.
// Assumptions (no contract in R0001 / the mockup, NR §7(b)): hidden Steps are never named by their key — their
// unassigned count is a separate line, a capability finding or a field error of one says "표시하지 않는 단계".
// flowgate.v02.0074 T#5 (D#1 §6.2): an N / T instruction row and a D / P / L / DB row carry one more icon left of ✎,
// 「지시 생략」 (「설계 지시 생략」 on a design row): the editor writes the fixed text, saves once and deploys
// (useWorkPlanEditor.omitStep). Its tooltip names it and, when it is off, why in a word (내용 있음 · 먼저 저장 · 전개 불가 · the
// window's lock); after a saved but failed deploy it is 「전개 재시도」. The row's last column holds the two icons.
import { computed, ref, shallowRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '@/components/ui/Button.vue'
import IconButton from '@/components/ui/IconButton.vue'
import Input from '@/components/ui/Input.vue'
import Select from '@/components/ui/Select.vue'
import { renderWpFieldError, type WpFieldError } from '@shared/workPlanErrors'
import { workPlanQuantityNameKey } from '@/features/documentWindow/specialDocumentModel'
import type { WorkPlanCapabilityFinding } from '@/features/document'
import WorkPlanReviewPopover from './WorkPlanReviewPopover.vue'
import WorkPlanTypeIcon from './WorkPlanTypeIcon.vue'
import { paletteIconPath } from '@/features/designer/palette/paletteIcons'
import { workPlanDocumentNameKey } from './workPlanPresentation'
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

const { phase, loadError, unreadableRaw, view, draft, saving, reloading, lockReason, inputsLocked, failure, rowErrors, topErrors, capability, externalRevision, omitting } =
  props.editor

/** Lucide `rotate-ccw` (ISC) — the defaults row's mark, as in the confirmed mockup; also 「전개 재시도」. */
const REFRESH_PATH = 'M3 12a9 9 0 1 0 3-6.7L3 8 M3 3v5h5'
/** Lucide `check-check` (ISC) — 「지시 생략」. */
const OMIT_PATH = 'M18 6 7 17l-5-5 M22 10l-7.5 7.5L13 16'

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

function documentName(step: WorkPlanEditStep): string {
  return t(workPlanDocumentNameKey(step.type, step.pair_role))
}

/** A card's name: its quantity name and ordinal (조사 1, 작업 1, 기본 설계 1). */
function cardName(card: WorkPlanCard): string {
  const head = card.steps[0]
  const unit = draft.value ? unitOf(draft.value.body, head.type) : 'sheet'
  return t('card.name', { name: gt(`gui.document_window.${workPlanQuantityNameKey({ type: head.type, unit })}`), n: head.ordinal })
}

function findingLabel(finding: WorkPlanCapabilityFinding): string {
  if (!draft.value || !stepKeyShown(draft.value, finding.stepKey, finding.stepType)) return t('hidden_step')
  for (const shown of shownCards.value) {
    const row = shown.rows.find((item) => item.step.key === finding.stepKey)
    if (row) return `${cardName(shown.card)} · ${documentName(row.step)}`
  }
  return t('hidden_step')
}

// --- folding the card sections (screen state only) -------------------------------------------------------------

/** Per window: two windows may show the same card ids. */
const bodyPrefix = `wpe-card-body-${Math.random().toString(36).slice(2, 10)}`
const opened = shallowRef<ReadonlySet<string>>(new Set())

const isOpen = (cardId: string) => opened.value.has(cardId)
/** The column titles are shown while at least one shown card is open (all folded = card headers only). */
const anyOpen = computed(() => shownCards.value.some((shown) => opened.value.has(shown.card.id)))
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

const omitOf = (row: WorkPlanRowRef) => props.editor.omitState(row.rowId)

function omitTitle(row: WorkPlanRowRef): string {
  const state = omitOf(row)
  const name = state.action === 'retry' ? t('omit.retry') : t(row.step.pair_role === 'single' ? 'omit.design' : 'omit.instruction')
  return state.reason ? `${name} — ${t(state.reason)}` : name
}

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
  gap: 15px;
  min-height: 0;
  container-type: inline-size;
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

/* The shared controls keep the Designer theme. Only this window owns the layout. */
.wpe-section { display: flex; flex-direction: column; gap: 9px; min-width: 0; }
.wpe-heading { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.wpe-heading h4 { margin: 0; font-size: 12px; font-weight: 650; }
.wpe-heading-spacer { flex: 1; }
.wpe-muted { color: var(--fg2-muted); font-size: 11px; }
.wpe-quantities, .wpe-defaults { padding: 11px 12px; border: 1px solid var(--fg2-line); border-radius: var(--fg2-control-radius); background: var(--fg2-panel); }
.wpe-quantity-summary { padding: 3px 9px; border: 1px solid var(--fg2-status-info-border, var(--fg2-line-strong)); border-radius: 20px; background: var(--fg2-status-info-bg, transparent); color: var(--fg2-status-info-fg, var(--fg2-muted)); font-size: 11px; font-weight: 600; }
.wpe-boxes { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; margin: 0; padding: 0; list-style: none; }
.wpe-box { display: flex; align-items: center; gap: 8px; min-width: 0; min-height: 44px; padding: 8px; border: 1px solid var(--fg2-line-strong); border-radius: var(--fg2-control-radius); background: var(--fg2-control-surface); }
.wpe-box.is-zero { border-color: var(--fg2-line); }
.wpe-box.is-active { border-color: var(--fg2-status-info-border, var(--fg2-line-strong)); background: var(--fg2-control-surface-strong, var(--fg2-control-surface)); }
.wpe-box-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 11.5px; font-weight: 600; }
.wpe-stepper { display: inline-flex; flex: none; align-items: center; gap: 1px; }
.wpe-stepper :deep(button) { width: 22px; height: 24px; }
.wpe-box-count { min-width: 18px; font-weight: 650; text-align: center; font-variant-numeric: tabular-nums; }
.wpe-defaults { display: grid; grid-template-columns: 140px 184px minmax(140px, 1fr) auto; align-items: center; gap: 8px; }
.wpe-label { display: flex; align-items: center; gap: 7px; color: var(--fg2-muted); font-size: 11px; }
.wpe-button-content { display: inline-flex; align-items: center; gap: 6px; }
.wpe-button-content .wpe-icon { width: 14px; height: 14px; }
.wpe-provider { width: 100%; min-width: 0; }
.wpe-note { position: relative; display: flex; min-width: 0; }
.wpe-note:has(.wpe-count) :deep(input) { padding-right: 64px; }
.wpe-count { position: absolute; right: 9px; top: 50%; transform: translateY(-50%); color: var(--fg2-muted); font-size: 10px; pointer-events: none; }
.wpe-count.is-over { color: var(--fg2-status-danger-fg); }
.wpe-table { overflow-x: auto; }
.wpe-columns, .wpe-row { display: grid; grid-template-columns: minmax(175px, 1.25fr) minmax(140px, .95fr) minmax(160px, .9fr) minmax(130px, 1fr) 66px; align-items: center; gap: 8px; padding: 6px 10px; }
.wpe-columns { min-width: 760px; padding: 6px 11px 8px; border-bottom: 1px solid var(--fg2-line); color: var(--fg2-muted); font-size: 10.5px; }
.wpe-cards { display: flex; flex-direction: column; gap: 9px; min-width: 760px; margin: 0; padding: 7px 0 0; list-style: none; }
.wpe-card { overflow: hidden; border: 1px solid var(--fg2-line-strong); border-radius: var(--fg2-control-radius); transition: border-color .14s ease; }
.wpe-card:hover { border-color: var(--fg2-control-hover-border, var(--fg2-line-strong)); }
.wpe-card.is-started { border-style: dashed; }
.wpe-card.is-drag-over, .wpe-drop-end.is-drag-over { border-color: var(--fg2-focus); }
.wpe-card-header { display: flex; align-items: center; gap: 8px; min-height: 46px; padding: 6px 10px; background: var(--fg2-panel); cursor: pointer; }
.wpe-card.is-open .wpe-card-header { border-bottom: 1px solid var(--fg2-line); }
.wpe-card-controls { display: inline-flex; flex: none; align-items: center; gap: 2px; cursor: default; }
.wpe-card-toggle { display: flex; flex: 1; align-items: center; gap: 8px; min-width: 0; min-height: 32px; padding: 0; border: 0; background: transparent; color: inherit; font: inherit; text-align: left; cursor: pointer; }
.wpe-card-toggle:focus-visible { border-radius: var(--fg2-control-radius-sm); outline: 2px solid var(--fg2-focus); outline-offset: 1px; }
.wpe-icon { flex: none; width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 1.7; stroke-linecap: round; stroke-linejoin: round; }
.wpe-caret { width: 12px; height: 12px; color: var(--fg2-muted); }
.wpe-caret.is-open { transform: rotate(90deg); }
.wpe-card-name { flex: none; font-weight: 650; }
.wpe-card-summary { display: flex; gap: 5px; min-width: 0; padding: 2px 9px; overflow: hidden; border: 1px solid var(--fg2-line-strong); border-radius: 20px; color: var(--fg2-muted); font-size: 10.5px; white-space: nowrap; }
.wpe-card-doc { flex: none; }
.wpe-card-doc + .wpe-card-doc::before { content: '·'; margin-right: 5px; }
.wpe-card-error { flex: none; color: var(--fg2-status-danger-fg); font-weight: 700; }
.wpe-handle { display: grid; place-items: center; width: 26px; height: 28px; color: var(--fg2-muted); cursor: grab; user-select: none; }
.wpe-handle.is-disabled { cursor: not-allowed; opacity: .6; }
.wpe-rows { display: flex; flex-direction: column; }
.wpe-row { min-height: 46px; background: var(--fg2-control-surface); }
.wpe-row + .wpe-row { border-top: 1px solid var(--fg2-line); }
.wpe-row.is-locked { color: var(--fg2-muted); }
.wpe-row.has-error .wpe-type { color: var(--fg2-status-danger-fg); }
.wpe-type { display: flex; align-items: center; gap: 8px; min-width: 0; }
.wpe-document-icon { display: grid; flex: none; place-items: center; width: 25px; height: 25px; border: 1px solid var(--fg2-line-strong); border-radius: var(--fg2-control-radius-sm); color: var(--fg2-muted); }
.wpe-document-copy { display: flex; flex-direction: column; min-width: 0; }
.wpe-document-copy strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; font-weight: 650; }
.wpe-document-copy small { font-size: 10px; }
.wpe-row :deep(select), .wpe-row :deep(input), .wpe-review { height: 31px; font-size: 11.5px; }
.wpe-review { min-width: 0; width: 100%; overflow: hidden; color: var(--fg2-muted); }
.wpe-review.is-active, .wpe-review.is-open { color: var(--fg2-text); }
.wpe-review :deep(.ui-btn-content) { display: flex; align-items: center; gap: 6px; min-width: 0; width: 100%; }
.wpe-review-text { flex: 1; min-width: 0; overflow: hidden; text-align: left; text-overflow: ellipsis; white-space: nowrap; }
.wpe-review-chevron { width: 12px; height: 12px; }
.wpe-pencil { position: relative; display: flex; justify-content: flex-end; gap: 4px; }
.wpe-pencil :deep(button) { width: 31px; height: 31px; border: 1px solid var(--fg2-status-info-border, var(--fg2-line-strong)); border-radius: var(--fg2-control-radius); background: var(--fg2-status-info-bg, transparent); color: var(--fg2-status-info-fg, inherit); }
.wpe-pencil :deep(button:disabled) { border-color: var(--fg2-line); background: transparent; color: var(--fg2-muted); }
.wpe-mark { position: absolute; top: 3px; right: 3px; width: 5px; height: 5px; border-radius: 50%; background: var(--fg2-status-success-fg); pointer-events: none; }
.wpe-row-errors { display: flex; grid-column: 1 / -1; flex-direction: column; padding-left: 33px; font-size: 11.5px; }
.wpe-drop-end { padding: 6px; border: 1px dashed var(--fg2-line); border-radius: var(--fg2-control-radius-sm); color: var(--fg2-muted); text-align: center; }
@container (max-width: 900px) {
  .wpe-boxes { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .wpe-columns, .wpe-row { grid-template-columns: minmax(140px, 1.15fr) 133px 160px minmax(110px, 1fr) 66px; gap: 5px; }
  .wpe-columns, .wpe-cards { min-width: 620px; }
  .wpe-defaults { grid-template-columns: 90px 160px minmax(120px, 1fr); }
  .wpe-defaults > :last-child { grid-column: 3; justify-self: end; }
}
@container (max-width: 560px) {
  .wpe-defaults { grid-template-columns: 80px minmax(0, 1fr); }
  .wpe-defaults > .wpe-note { grid-column: 1 / -1; }
  .wpe-defaults > :last-child { grid-column: 2; }
  .wpe-box { gap: 4px; padding: 6px; }
}
</style>
