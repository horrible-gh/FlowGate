<template>
  <!-- flowgate.v02.0051 stage 2 — a Graph gate's header actions (0035 v7 확인 cards: ✓ 확인 · ✕ 반려 ▾).
       flowgate.v02.0053 T#9 — they take the gate decision of the Flow's run (P0008 S6-1): usable while the run
       is running and this gate is open in it, otherwise disabled with the reason in the tooltip. ✕ 반려 ▾ asks
       for an optional reason first. A refused decision shows in the tooltip only. A thin container over
       useGateDecision / gateCardModel. -->
  <div
    v-if="actions.confirm.visible || actions.reject.visible"
    class="inspector-gate-actions"
    :class="{ 'is-failed': failureKey !== null }"
    role="group"
    :aria-label="t(k('label'))"
    data-test="inspector-gate-actions"
    :data-busy="busy ?? ''"
  >
    <Button
      v-if="actions.confirm.visible"
      size="sm"
      variant="primary"
      :disabled="!actions.confirm.enabled"
      :loading="busy === 'confirm'"
      :title="tipOf(actions.confirm.reason)"
      data-test="gate-confirm"
      @click="confirm"
    >{{ t(k('confirm')) }}</Button>
    <Button
      v-if="actions.reject.visible"
      size="sm"
      variant="danger"
      :disabled="!actions.reject.enabled"
      :loading="busy === 'reject'"
      :title="tipOf(actions.reject.reason)"
      data-test="gate-reject"
      @click="openReject"
    >{{ t(k('reject')) }} ▾</Button>

    <ModalShell :open="rejectOpen" size="sm" :aria-label="t(k('reject_dialog.title'))" @close="closeReject">
      <template #header>
        <h3 class="inspector-gate-dialog-title">{{ t(k('reject_dialog.title')) }}</h3>
      </template>
      <div class="inspector-gate-dialog" data-test="gate-reject-dialog">
        <Textarea
          v-model="rejectReason"
          :rows="3"
          :placeholder="t(k('reject_dialog.placeholder'))"
          :disabled="busy === 'reject'"
          data-test="gate-reject-reason"
        />
      </div>
      <template #footer>
        <div class="inspector-gate-dialog-footer">
          <Button size="sm" variant="ghost" data-test="gate-reject-cancel" @click="closeReject">{{ t(k('reject_dialog.cancel')) }}</Button>
          <Button
            size="sm"
            variant="danger"
            :disabled="!actions.reject.enabled"
            :loading="busy === 'reject'"
            data-test="gate-reject-submit"
            @click="submitReject"
          >{{ t(k('reject_dialog.submit')) }}</Button>
        </div>
      </template>
    </ModalShell>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '../../../../../components/ui/Button.vue'
import ModalShell from '../../../../../components/ui/ModalShell.vue'
import Textarea from '../../../../../components/ui/Textarea.vue'
import type { InspectorTarget } from '../../inspectorTarget'
import { useGateDecision } from './useGateDecision'

const props = defineProps<{
  target: InspectorTarget
}>()

const { t } = useI18n()

const k = (key: string) => `gui.inspector.gateCard.${key}`

const { actions, busy, failureKey, decide } = useGateDecision(() => props.target)

/** The tooltip: the last refusal, else why it is disabled, else nothing. */
function tipOf(reason: string | null): string | undefined {
  if (failureKey.value) return t(k(failureKey.value))
  return reason && reason !== 'reason.busy' ? t(k(reason)) : undefined
}

function confirm() {
  void decide('confirm')
}

const rejectOpen = ref(false)
const rejectReason = ref('')

function openReject() {
  rejectReason.value = ''
  rejectOpen.value = true
}

function closeReject() {
  if (busy.value === 'reject') return
  rejectOpen.value = false
}

async function submitReject() {
  if (await decide('reject', rejectReason.value)) rejectOpen.value = false
}

// Another Node: its dialog does not follow.
watch(
  () => props.target.nodeId,
  () => {
    rejectOpen.value = false
    rejectReason.value = ''
  },
)
</script>

<style scoped>
.inspector-gate-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.inspector-gate-actions.is-failed :deep(button:not(:disabled)) {
  box-shadow: 0 0 0 1px #b4474f;
}

.inspector-gate-dialog-title {
  margin: 0;
  padding: 16px 20px 0;
  font-size: 14px;
  font-weight: 700;
}

.inspector-gate-dialog {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.inspector-gate-dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 0 20px 16px;
}
</style>
