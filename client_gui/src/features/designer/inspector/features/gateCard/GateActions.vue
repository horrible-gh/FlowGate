<template>
  <div v-if="actions.confirm.visible" class="inspector-gate-actions" :class="{ 'is-failed': failureKey !== null }"
    role="group" :aria-label="t(k('label'))" data-test="inspector-gate-actions" :data-busy="busy ?? ''">
    <Button v-if="actions.confirm.visible" size="sm" variant="primary" :disabled="!actions.confirm.enabled" :loading="busy === 'confirm'"
      :title="tipOf(actions.confirm.reason)" data-test="gate-confirm" @click="decide('confirm')">{{ t(k('confirm')) }}</Button>
    <!-- flowgate.v02test.0052 0008 할 일 6: the failed approval is cancelled in the Git tab — take the person there. -->
    <Button v-if="oldApprovalHeld" size="sm" variant="ghost" data-test="gate-open-git" @click="host?.request({ kind: 'show-tool', tool: 'git' })">{{ t(k('old_approval.open')) }}</Button>
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
const { actions, busy, failureKey, decide } = props.state ?? useGateDecision(() => props.target)
const host = inject(INSPECTOR_HOST, null)
const oldApprovalHeld = computed(() => !actions.value.confirm.enabled && actions.value.confirm.reason === 'reason.old_approval_failed')
function tipOf(reason: string | null): string | undefined {
  if (failureKey.value) return t(failureKey.value.startsWith('settings:') ? `gui.inspector.checkSettings.${failureKey.value.slice(9)}` : k(failureKey.value))
  return reason ? t(k(reason)) : undefined
}
</script>
<style scoped>
.inspector-gate-actions { display: flex; align-items: center; gap: 7px; margin-left: auto; }
.inspector-gate-actions.is-failed :deep(button:not(:disabled)) { box-shadow: 0 0 0 1px #b4474f; }
</style>
