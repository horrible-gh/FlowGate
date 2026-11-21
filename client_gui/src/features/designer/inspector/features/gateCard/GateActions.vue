<template>
  <div v-if="actions.confirm.visible || result?.key === 'confirmed'" class="inspector-gate-actions" :class="{ 'is-failed': failureKey !== null }"
    role="group" :aria-label="t(k('label'))" data-test="inspector-gate-actions" :data-busy="busy ?? ''">
    <Button v-if="actions.confirm.visible" size="sm" variant="primary" :disabled="!actions.confirm.enabled" :loading="busy === 'confirm'"
      :title="tipOf(actions.confirm.reason)" data-test="gate-confirm" @click="decide('confirm')">{{ t(k('confirm')) }}</Button>
    <Button v-if="cancelAction?.visible" size="sm" variant="secondary" class="inspector-gate-cancel" :disabled="!cancelAction.enabled" :loading="busy === 'cancel'"
      data-test="gate-cancel" @click="cancelAttempt = attemptId">{{ t(k('cancel')) }}</Button>
    <ConfirmDialog :open="cancelAttempt !== null" :title="t(k('cancel_dialog.title'))" :message="t(k('cancel_dialog.message'))"
      :confirm-label="t(k('cancel'))" :cancel-label="t(k('cancel_dialog.keep'))" :busy="busy !== null"
      @cancel="cancelAttempt = null" @confirm="confirmCancel" />
    <span v-if="shownReason" class="inspector-gate-reason" :title="shownReason" data-test="gate-confirm-reason">{{ shownReason }}</span>
    <!-- task 6: the failed approval is cancelled in the Git tab — take the person there. -->
    <Button v-if="oldApprovalHeld" size="sm" variant="ghost" data-test="gate-open-git" @click="host?.request({ kind: 'show-tool', tool: 'git' })">{{ t(k('old_approval.open')) }}</Button>
  </div>
</template>
<script setup lang="ts">
import { computed, inject, ref, watch } from 'vue'
import ConfirmDialog from '@/components/ui/ConfirmDialog.vue'
import { useI18n } from 'vue-i18n'
import Button from '../../../../../components/ui/Button.vue'
import { INSPECTOR_HOST } from '../../inspectorHost'
import type { InspectorTarget } from '../../inspectorTarget'
import { useGateDecision, type GateDecisionState } from './useGateDecision'
const props = defineProps<{ target: InspectorTarget; state?: GateDecisionState }>()
const { t } = useI18n()
const k = (key: string) => `gui.inspector.gateCard.${key}`
const { actions, cancelAction, attemptId, decisionHeld, result, busy, failureKey, decide } = props.state ?? useGateDecision(() => props.target)
const cancelAttempt = ref<string | null>(null)
watch(() => `${props.target.flowId}/${props.target.nodeId}/${attemptId?.value}`, () => { cancelAttempt.value = null })
async function confirmCancel() {
  const attempt = cancelAttempt.value
  cancelAttempt.value = null
  if (attempt && attempt === attemptId.value) await decide('cancel')
}
const host = inject(INSPECTOR_HOST, null)
// flowgate.v02gui.0004 (0001-B observation 5/6): a blocked [승인] says why beside it, not only in a tooltip. `busy` is the
// button's own spinner, "not your turn yet" needs no line, and a conflict is already the result row's pill.
// reason.read_only_archived is not listed: the archive state is re-read after every approve/reject, and the line flashed
// for that moment on Flows that are not archived. The disabled button keeps it as a tooltip.
const SHOWN_REASONS = ['reason.decision_in_progress', 'reason.read_only_conflict', 'reason.old_approval_failed']
const oldApprovalHeld = computed(() => !actions.value.confirm.enabled && actions.value.confirm.reason === 'reason.old_approval_failed')
const shownReason = computed(() => {
  if (decisionHeld?.value || result?.value.key === 'confirmed') return t(k('cancel_dialog.after_approval'))
  const reason = actions.value.confirm.reason
  if (reason === 'reason.decision_in_progress' || reason === 'reason.old_approval_failed') return t(k('cancel_dialog.after_approval'))
  if (actions.value.confirm.enabled || !reason || !SHOWN_REASONS.includes(reason)) return null
  return t(k(reason))
})
function tipOf(reason: string | null): string | undefined {
  if (failureKey.value) return t(failureKey.value.startsWith('settings:') ? `gui.inspector.checkSettings.${failureKey.value.slice(9)}` : k(failureKey.value))
  return reason ? t(k(reason)) : undefined
}
</script>
<style scoped>
.inspector-gate-actions { display: flex; align-items: center; gap: 7px; margin-left: auto; }
.inspector-gate-actions.is-failed :deep(button:not(:disabled)) { box-shadow: 0 0 0 1px #b4474f; }
/* flowgate.v02test.0064 #29: [취소] carries a soft red outline so it reads as "stop this run" next to the blue [승인]; it
   stays an outline (no fill) so it never outweighs the approve button, and fills lightly only on hover. */
.inspector-gate-actions .inspector-gate-cancel { border-color: var(--fg2-status-danger-border); background: transparent; color: var(--fg2-status-danger-fg); }
.inspector-gate-actions .inspector-gate-cancel:hover:not(:disabled) { border-color: var(--fg2-status-danger-fg); background: var(--fg2-status-danger-bg); color: var(--fg2-status-danger-fg); }
.inspector-gate-reason { max-width: 260px; overflow: hidden; color: var(--fg2-status-warning-fg); font-size: 11.5px; text-overflow: ellipsis; white-space: nowrap; }
</style>
