<template>
  <div v-if="actions.confirm.visible || actions.reject.visible" class="inspector-gate-actions" :class="{ 'is-failed': failureKey !== null }"
    role="group" :aria-label="t(k('label'))" data-test="inspector-gate-actions" :data-busy="busy ?? ''">
    <Button v-if="actions.confirm.visible" size="sm" variant="primary" :disabled="!actions.confirm.enabled" :loading="busy === 'confirm'"
      :title="tipOf(actions.confirm.reason)" data-test="gate-confirm" @click="decide('confirm')">{{ t(k('confirm')) }}</Button>
    <Button v-if="actions.reject.visible" size="sm" variant="danger" :disabled="!actions.reject.enabled" :loading="busy === 'reject'"
      :title="tipOf(actions.reject.reason)" data-test="gate-reject" @click="decide('reject')">{{ t(k('reject')) }}</Button>
  </div>
</template>
<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import Button from '../../../../../components/ui/Button.vue'
import type { InspectorTarget } from '../../inspectorTarget'
import { useGateDecision, type GateDecisionState } from './useGateDecision'
const props = defineProps<{ target: InspectorTarget; state?: GateDecisionState }>()
const { t } = useI18n()
const k = (key: string) => `gui.inspector.gateCard.${key}`
const { actions, busy, failureKey, decide } = props.state ?? useGateDecision(() => props.target)
function tipOf(reason: string | null): string | undefined {
  if (failureKey.value) return t(k(failureKey.value))
  return reason ? t(k(reason)) : undefined
}
</script>
<style scoped>
.inspector-gate-actions { display: flex; align-items: center; gap: 7px; margin-left: auto; }
.inspector-gate-actions.is-failed :deep(button:not(:disabled)) { box-shadow: 0 0 0 1px #b4474f; }
</style>
