<template>
  <div v-if="actions.confirm.visible" class="inspector-gate-actions" :class="{ 'is-failed': failureKey !== null }"
    role="group" :aria-label="t(k('label'))" data-test="inspector-gate-actions" :data-busy="busy ?? ''">
    <Button v-if="actions.confirm.visible" size="sm" variant="primary" :disabled="!actions.confirm.enabled" :loading="busy === 'confirm'"
      :title="tipOf(actions.confirm.reason)" data-test="gate-confirm" @click="decide('confirm')">{{ t(k('confirm')) }}</Button>
    <span v-if="shownReason" class="inspector-gate-reason" :title="shownReason" data-test="gate-confirm-reason">{{ shownReason }}</span>
    <!-- flowgate.v02test.0052 0008 할 일 6: the failed approval is cancelled in the Git tab — take the person there. -->
    <Button v-if="oldApprovalHeld" size="sm" variant="ghost" data-test="gate-open-git" @click="host?.request({ kind: 'show-tool', tool: 'git' })">{{ t(k('old_approval.open')) }}</Button>
    <span v-else-if="cancelledHint" class="inspector-gate-reason" :title="cancelledHint" data-test="gate-reapprove-hint">{{ cancelledHint }}</span>
  </div>
</template>
<script setup lang="ts">
import { computed, inject } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '../../../../../components/ui/Button.vue'
import { INSPECTOR_HOST } from '../../inspectorHost'
import type { InspectorTarget } from '../../inspectorTarget'
import { useGateDecision, type GateDecisionState } from './useGateDecision'
const props = defineProps<{ target: InspectorTarget; state?: GateDecisionState }>()
const { t } = useI18n()
const k = (key: string) => `gui.inspector.gateCard.${key}`
const { actions, busy, failureKey, decide, receipt } = props.state ?? useGateDecision(() => props.target)
const host = inject(INSPECTOR_HOST, null)
// flowgate.v02gui.0004 (0001-B 관찰 5/6): a blocked [승인] says why beside it, not only in a tooltip. `busy` is the
// button's own spinner, "not your turn yet" needs no line, and a conflict is already the result row's pill.
// reason.read_only_archived is not listed: the archive state is re-read after every approve/reject, and the line flashed
// for that moment on Flows that are not archived. The disabled button keeps it as a tooltip.
const SHOWN_REASONS = ['reason.decision_in_progress', 'reason.read_only_conflict', 'reason.old_approval_failed']
const oldApprovalHeld = computed(() => !actions.value.confirm.enabled && actions.value.confirm.reason === 'reason.old_approval_failed')
// The earlier approval was cancelled (its receipt reads canceled) and [승인] is open again: say it approves the current commit.
const cancelledHint = computed(() => {
  const status = receipt?.value?.status
  return actions.value.confirm.enabled && (status === 'canceled' || status === 'cancelled') ? t(k('old_approval.cancelled')) : null
})
const shownReason = computed(() => {
  const reason = actions.value.confirm.reason
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
.inspector-gate-reason { max-width: 260px; overflow: hidden; color: var(--fg2-status-warning-fg); font-size: 11.5px; text-overflow: ellipsis; white-space: nowrap; }
</style>
