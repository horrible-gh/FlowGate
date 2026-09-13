<template>
  <!--
    Merge review reject prompt — flowgate.default.0560 T0020 (4.5순위).

    NR0005 §13 asked for "메인 dialog와 reject sub-dialog의 관계 재설계", and D0008 §4 names this
    pair as its example of a nested dialog. This is the child: the sub-dialog that used to be a
    hand-rolled `position: fixed; inset: 0; z-index: 1500` overlay INSIDE
    `GitMergeReviewDialog.vue`'s own `<template v-else>`. D0008 §6 maps it to `form-actions`.

    The parent dialog is deliberately NOT migrated here — its shell belongs to NR0005 §13's
    5순위 (Git review/conflict 계열), which moves it together with `GroupChangesDialog`. So this
    child joins the common stack while its parent is still a legacy `.modal-bg`:

    - z-order still works. The common host's base is `dialogZOrder.base = 1600`, the parent's
      `.modal-bg` is `z-index: 1000` (app.css), and the old overlay sat at 1500 — so this box
      keeps rendering above the parent exactly as it did.
    - What does NOT come for free is L0009 §2's "child active 중 parent: interaction 비활성",
      because that is defined between two STACK members and the parent is not one. The parent
      approximates it by hand: while this dialog is open its [승인]/[반려]/header X are
      disabled. That is an approximation, not the contract; the contract arrives when the
      parent itself joins the stack in 5순위.

    `closeOnBackdrop` is not overridden — `form-actions` already defaults to `false`
    (dialogTypes.ts), and the old `.gmr-reject-overlay` had no backdrop handler either.

    T0020 §2.2 "reject 사유 손실 방지" — option (a). ESC did not exist here before (nothing in
    this box held focus and there was no handler), so the common stack's focus-independent ESC
    creates a loss path for up to 4000 characters of typed instruction that did not exist
    yesterday. Every close path (ESC, header X, [취소]) funnels through `requestClose()` and a
    non-empty reason is confirmed first — the same answer from every control, which is the
    R0001 symptom this whole layer exists to remove. An empty box closes without a question.
  -->
  <DialogShell
    :open="open"
    variant="form-actions"
    surface-class="gmr-reject-dialog"
    :busy="busy"
    :return-focus-to="returnFocusTo"
    @request-close="requestClose"
  >
    <template #header>
      <DialogHeader :title="t('main.git_review.reject')" icon="prohibit" @close="requestClose" />
    </template>

    <template #default>
      <label class="gmr-reject-field">
        {{ t('main.git_review.reject_reason_label') }}
        <textarea v-model="reason" rows="3" maxlength="4000" data-dialog-autofocus></textarea>
      </label>
      <label class="gmr-reject-field">
        {{ t('main.git_review.next_provider_label') }}
        <AiProviderSelect
          :providers="providers"
          :model-value="selectedProvider"
          :loading="providerLoading"
          :errored="providerErrored"
          hide-label
          @update:model-value="(v: string) => emit('update:provider', v)"
        />
      </label>
    </template>

    <template #footer>
      <DialogFooter :actions="actions" />
    </template>
  </DialogShell>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import AiProviderSelect from './AiProviderSelect.vue'
import DialogFooter from './dialogs/DialogFooter.vue'
import DialogHeader from './dialogs/DialogHeader.vue'
import DialogShell from './dialogs/DialogShell.vue'
import type { DialogAction } from './dialogs/dialogTypes'
import { confirm as dialogConfirm } from '../composables/useDialogStack'

const props = withDefaults(defineProps<{
  open: boolean
  busy?: boolean
  providers?: { id: string; name: string }[]
  selectedProvider?: string
  providerLoading?: boolean
  providerErrored?: boolean
  returnFocusTo?: HTMLElement | null
}>(), {
  busy: false,
  providers: () => [],
  selectedProvider: '',
  providerLoading: false,
  providerErrored: false,
  returnFocusTo: null,
})

/**
 * The POST and its toast stay with `GitMergeReviewDialog` (D0008 §1: a feature API call is not
 * the common layer's, and the group/merge ids and the `resolved`/`close` follow-up all live
 * there). What this component owns is the draft the user types — it is this dialog's own form
 * state, born when the dialog opens and gone when it closes — so the reason travels out with
 * the event instead of being held by the parent for a box it no longer renders.
 */
const emit = defineEmits<{
  'update:provider': [value: string]
  close: []
  reject: [reason: string]
}>()

const { t } = useI18n()

const reason = ref('')

// Opening always starts a blank reason, which is what `openRejectPrompt()` did before the
// split. Kept on the open edge rather than on close so a reject that fails server-side still
// has its text on screen.
watch(() => props.open, (isOpen) => {
  if (isOpen) reason.value = ''
})

const canReject = computed(() => !props.busy && !!reason.value.trim() && !!props.selectedProvider)

/** T0020 §2.2 (a) — one answer for ESC, the header X and [취소]. */
async function requestClose() {
  if (props.busy) return
  if (reason.value.trim()) {
    const discard = await dialogConfirm({
      title: t('main.git_review.discard_confirm_title'),
      message: t('main.git_review.discard_confirm_message'),
      confirmLabel: t('main.git_review.discard_confirm_ok'),
      danger: true,
    })
    if (!discard) return
  }
  emit('close')
}

/**
 * `[취소] [반려 확정]` — D0008 §6's Danger Confirm shape. 반려 확정 is `primary` with
 * `tone="danger"` (the destructive action IS the one that completes this dialog, so it takes
 * the primary position and the tone paints it), and the disabled condition is the same one the
 * inline button carried: busy, an empty reason, or no provider chosen.
 */
const actions = computed<DialogAction[]>(() => [
  {
    id: 'cancel',
    label: t('common.cancel'),
    role: 'cancel',
    disabled: props.busy,
    onSelect: requestClose,
  },
  {
    id: 'reject-confirm',
    label: t('main.git_review.reject_confirm'),
    role: 'primary',
    tone: 'danger',
    disabled: !canReject.value,
    onSelect: () => emit('reject', reason.value.trim()),
  },
])
</script>

<style scoped>
/* The overlay, the box, the title row and the button row are the common layer's now; what is
   left is the two labelled fields (`.gmr-reject-box label` before the split). */
.gmr-reject-field { display: flex; flex-direction: column; gap: 4px; font-size: 0.78rem; }
.gmr-reject-field + .gmr-reject-field { margin-top: 10px; }
.gmr-reject-field textarea {
  padding: 8px; border: 1px solid var(--border, #cbd5e1); border-radius: 6px; font: inherit; font-size: 0.8rem;
}
</style>

<!--
  Unscoped: `surface-class` lands on the dialog surface, which DialogShell renders and
  teleports out of this component's subtree, so a scoped rule could never reach it. This is the
  width `.gmr-reject-box` measured; `form-actions` sits on the `md` 520px track, and a reject
  prompt has no reason to grow by 40px on its way onto the common layer.
-->
<style>
.fg-dialog-surface.gmr-reject-dialog {
  width: min(480px, calc(100vw - 48px));
}
</style>
