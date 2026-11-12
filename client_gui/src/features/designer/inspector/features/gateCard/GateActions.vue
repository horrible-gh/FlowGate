<template>
  <div v-if="actions.confirm.visible" class="inspector-gate-actions" :class="{ 'is-failed': failureKey !== null }"
    role="group" :aria-label="t(k('label'))" data-test="inspector-gate-actions" :data-busy="busy ?? ''">
    <Button v-if="actions.confirm.visible" size="sm" variant="primary" :disabled="!actions.confirm.enabled" :loading="busy === 'confirm'"
      :title="tipOf(actions.confirm.reason)" data-test="gate-confirm" @click="decide('confirm')">{{ t(k('confirm')) }}</Button>
    <span v-if="shownReason" class="inspector-gate-reason" :title="shownReason" data-test="gate-confirm-reason">{{ shownReason }}</span>
  </div>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '../../../../../components/ui/Button.vue'
import type { InspectorTarget } from '../../inspectorTarget'
import { useGateDecision, type GateDecisionState } from './useGateDecision'
const props = defineProps<{ target: InspectorTarget; state?: GateDecisionState }>()
const { t } = useI18n()
const k = (key: string) => `gui.inspector.gateCard.${key}`
const { actions, busy, failureKey, decide } = props.state ?? useGateDecision(() => props.target)
// flowgate.v02gui.0004 (0001-B 관찰 5/6): a blocked [승인] says why beside it, not only in a tooltip. `busy` is the
// button's own spinner and "not your turn yet" needs no line.
const SHOWN_REASONS = ['reason.conflict', 'reason.decision_in_progress', 'reason.read_only_conflict', 'reason.read_only_archived']
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
