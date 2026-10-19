<template>
  <Teleport to="body">
    <div
      v-if="open && row"
      :id="popoverId"
      ref="root"
      class="wpr"
      role="dialog"
      :aria-label="t('review.title')"
      data-test="wp-edit-review-popover"
      :data-row="row.rowId"
      :style="style"
    >
      <div class="wpr-head">
        <svg class="wpr-icon" viewBox="0 0 24 24" aria-hidden="true"><path :d="paletteIconPath('shield')" /></svg>
        <strong class="wpr-title">{{ t('review.title') }}</strong>
        <span class="wpr-context" data-test="wp-edit-review-context">{{ context }}</span>
      </div>
      <label class="wpr-field">
        <span class="wpr-label">{{ t('review.count') }}</span>
        <Select
          :model-value="String(count)"
          :options="countOptions"
          :disabled="locked"
          data-test="wp-edit-review-count"
          @update:model-value="(value: string) => setCount(value)"
        />
      </label>
      <label class="wpr-field">
        <span class="wpr-label">{{ t('review.reviewer') }}</span>
        <Select
          :model-value="row.step.reviewer_provider_id ?? ''"
          :options="aiOptions"
          :disabled="locked || count === 0"
          data-test="wp-edit-review-ai"
          @update:model-value="(value: string) => setReviewer(value)"
        />
      </label>
      <div class="wpr-actions">
        <Button size="sm" variant="primary" data-test="wp-edit-review-done" @click="finish(true)">{{ t('review.done') }}</Button>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
// flowgate.v02.0073 T#2 — the review popover of the work plan edit window (one per window; WorkPlanEditBody opens it
// from a row's review button): the review count (a Select of the server's review_count_choices, -1 / 0 by their
// existing words), the reviewer (a Select of the reviewer picker's options; off while the count is 0) and [완료].
// No other review step. A change goes straight to the editor's draft (setStepReview — that row only; the server save
// stays the window's [저장]); closing never drops it. While the window's inputs are locked nothing can be changed.
// - Open / close: conversation/popover.ts usePopover (a press outside it closes it; Esc). The review button is
//   outside the popover (the popover is teleported to <body> so the window's scroll area does not clip it), so the
//   body reports a press on it first (press): the same button closes the popover, another row's button moves it.
// - Esc closes the popover before the window sees it (capture on document): the ModelessShell closes on Esc, and an
//   Esc meant for the popover must not close the plan. [완료] and Esc give the focus back to the review button.
//   Only an Esc in what the popover owns is taken — the popover itself and the window its button is in. An Esc in
//   another window (a Step window, another plan) or a dialog stays theirs (their own close / unsaved question, their
//   focus); and the focus moving out of what it owns (Tab to another window, a dialog taking the focus) closes the
//   popover without moving the focus.
// - It lives as long as its button is shown: the body closes it when the button's card folds or the window minimizes
//   (WorkPlanEditBody — the press outside does not cover the keyboard).
// - It is fixed on the page above every overlay opened before it (allocateOverlayZIndex) and placed from the button's
//   rect (reviewPopoverPlace), again on resize and on any scroll; it closes when its row or button is gone.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '@/components/ui/Button.vue'
import Select from '@/components/ui/Select.vue'
import { allocateOverlayZIndex } from '@/core/overlay/zIndex'
import { usePopover } from '@/features/conversation/popover'
import { workPlanQuantityNameKey } from '@/features/documentWindow/specialDocumentModel'
import { workPlanWindowMessages } from './messages'
import type { WorkPlanEditor } from './useWorkPlanEditor'
import { reviewCountOptions, rowOf, unitOf } from './workPlanEditModel'
import { reviewPopoverPlace } from './workPlanReviewPopover'
import { paletteIconPath } from '@/features/designer/palette/paletteIcons'
import { workPlanDocumentNameKey } from './workPlanPresentation'

const props = defineProps<{ editor: WorkPlanEditor }>()

const { t } = useI18n({ useScope: 'local', messages: workPlanWindowMessages, inheritLocale: true })
const { t: gt } = useI18n({ useScope: 'global' })

const root = ref<HTMLElement | null>(null)
const { open, close } = usePopover(root)
const rowId = ref<string | null>(null)
const style = ref<Record<string, string>>({})
const popoverId = `wpe-review-${Math.random().toString(36).slice(2, 10)}`
let trigger: HTMLElement | null = null
let pressed: string | null = null
let zIndex = 0

const { draft, inputsLocked } = props.editor
const row = computed(() => (draft.value && rowId.value ? rowOf(draft.value, rowId.value) : null))
const count = computed(() => row.value?.step.review_count ?? 0)
const locked = computed(() => inputsLocked.value)

function countLabel(value: number): string {
  if (value === 0) return t('review.none')
  if (value < 0) return t('review.until')
  return t('review.times', { n: value })
}

const countOptions = computed(() => reviewCountOptions(props.editor.reviewCountChoices(), count.value, countLabel))
const aiOptions = computed(() => props.editor.reviewerOptions(t('review.project_default'), (name) => t('provider.unavailable', { name })))

/** 작업 1 · 작업 지시서: the row's card and document, as the window names them. */
const context = computed(() => {
  const current = row.value
  if (!current || !draft.value) return ''
  const head = draft.value.cards[current.cardIndex].steps[0]
  const unit = unitOf(draft.value.body, head.type)
  const card = t('card.name', { name: gt(`gui.document_window.${workPlanQuantityNameKey({ type: head.type, unit })}`), n: head.ordinal })
  return t('review.context', { card, doc: t(workPlanDocumentNameKey(current.step.type, current.step.pair_role)) })
})

function setCount(value: string): void {
  const current = row.value
  if (!current || locked.value) return
  props.editor.setStepReview(current.rowId, Number(value), current.step.reviewer_provider_id ?? null)
}

function setReviewer(value: string): void {
  const current = row.value
  if (!current || locked.value || count.value === 0) return
  props.editor.setStepReview(current.rowId, count.value, value || null)
}

function place(): void {
  if (!open.value) return
  if (!trigger || !trigger.isConnected) {
    close()
    return
  }
  const bounds = trigger.closest('.ui-modeless-panel')?.getBoundingClientRect() ?? null
  const size = { width: root.value?.offsetWidth || 286, height: root.value?.offsetHeight || 220 }
  const at = reviewPopoverPlace(trigger.getBoundingClientRect(), size, { width: window.innerWidth, height: window.innerHeight }, bounds)
  style.value = { left: `${at.left}px`, top: `${at.top}px`, zIndex: String(zIndex) }
}

/** Closes it; `focus`: the focus goes back to the review button ([완료], Esc). */
function finish(focus: boolean): void {
  if (!open.value) return
  close()
  if (focus && trigger?.isConnected) trigger.focus()
}

/** Inside the popover or the window its button is in (the plan window's ModelessShell panel). */
function owns(target: EventTarget | null): boolean {
  if (!(target instanceof Node)) return false
  if (root.value?.contains(target)) return true
  return !!trigger?.closest('.ui-modeless-panel')?.contains(target)
}

/** A press on a row's review button, before the press outside closes the popover: was it open for this row? */
function press(id: string): void {
  pressed = open.value && rowId.value === id ? id : null
}

/** The review button's click: closes the popover open for this row, else opens it for this row. */
function toggle(id: string, button: HTMLElement): void {
  const wasOpen = pressed === id || (open.value && rowId.value === id)
  pressed = null
  if (wasOpen) {
    finish(false)
    return
  }
  rowId.value = id
  trigger = button
  zIndex = allocateOverlayZIndex()
  open.value = true
  place()
  void nextTick(() => {
    place()
    const first = root.value?.querySelector<HTMLElement>('select:not(:disabled), button')
    first?.focus({ preventScroll: true })
  })
}

function onEscape(event: KeyboardEvent): void {
  if (!open.value || event.key !== 'Escape' || !owns(event.target)) return
  event.stopPropagation()
  event.preventDefault()
  finish(true)
}

function onFocusIn(event: FocusEvent): void {
  if (open.value && !owns(event.target)) close()
}

// The row gone (its card removed, the plan read again) or locked by the server takes the popover with it.
watch(row, (current) => {
  if (open.value && (!current || current.step.locked)) close()
})

onMounted(() => {
  document.addEventListener('keydown', onEscape, true)
  document.addEventListener('focusin', onFocusIn)
  window.addEventListener('resize', place)
  window.addEventListener('scroll', place, true)
})

onBeforeUnmount(() => {
  document.removeEventListener('keydown', onEscape, true)
  document.removeEventListener('focusin', onFocusIn)
  window.removeEventListener('resize', place)
  window.removeEventListener('scroll', place, true)
})

defineExpose({
  /** The row the popover is open for, or null. */
  openRowId: computed(() => (open.value ? rowId.value : null)),
  popoverId,
  press,
  toggle,
  close: () => finish(false),
})
</script>

<style scoped>
.wpr {
  position: fixed;
  display: flex;
  flex-direction: column;
  gap: 10px;
  box-sizing: border-box;
  width: 286px;
  max-width: calc(100vw - 16px);
  max-height: calc(100vh - 16px);
  overflow-y: auto;
  padding: 13px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-overlay-radius);
  background: var(--fg2-panel);
  box-shadow: var(--fg2-overlay-shadow);
  color: var(--fg2-text);
  font-size: 12px;
}

.wpr-head {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.wpr-icon { flex: none; width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 1.7; stroke-linecap: round; stroke-linejoin: round; color: var(--fg2-cyan); }

.wpr-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  font-size: 12.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wpr-context {
  flex: none;
  max-width: 60%;
  overflow: hidden;
  color: var(--fg2-muted);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wpr-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.wpr-label {
  color: var(--fg2-muted);
  font-size: 11.5px;
  font-weight: 600;
}

.wpr-actions {
  display: flex;
  justify-content: flex-end;
}
</style>
