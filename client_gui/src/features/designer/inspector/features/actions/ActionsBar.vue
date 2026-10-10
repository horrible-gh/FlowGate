<template>
  <!-- flowgate.v02.0048 T#6 — header actions (D#1 §10.2 T#6 row): approve, reject with a reason, and
       on v0.1 Flows the single run and the rework of a rejected document. A thin container:
       useActions reads and calls, actionsModel decides what is shown and usable. A disabled
       operation shows its one reason; a refused call shows why and the state is read again. -->
  <div
    v-if="shown"
    class="inspector-actions"
    role="group"
    :aria-label="t(k('label'))"
    data-test="inspector-actions"
    :data-busy="busy ?? ''"
  >
    <Button
      v-if="availability.approve.visible"
      size="sm"
      variant="primary"
      :disabled="!availability.approve.enabled"
      :loading="busy === 'approve'"
      data-test="actions-approve"
      @click="approve"
    >{{ t(k('approve')) }}</Button>
    <Button
      v-if="availability.reject.visible"
      size="sm"
      variant="danger"
      :disabled="!availability.reject.enabled"
      :loading="busy === 'reject'"
      data-test="actions-reject"
      @click="openReject"
    >{{ t(k('reject')) }}</Button>
    <Button
      v-if="availability.run.visible"
      size="sm"
      variant="secondary"
      :disabled="!availability.run.enabled"
      :loading="busy === 'run'"
      :title="t(k('run_hint'))"
      data-test="actions-run"
      @click="run"
    >{{ t(k('run')) }}</Button>
    <Button
      v-if="availability.rework.visible"
      size="sm"
      variant="secondary"
      :disabled="!availability.rework.enabled"
      :loading="busy === 'rework'"
      :title="t(k('rework_hint'))"
      data-test="actions-rework"
      @click="rework"
    >{{ t(k('rework')) }}</Button>
    <span v-if="line" class="inspector-actions-line" :title="line.text" data-test="actions-line" :data-kind="line.kind">
      <InspectorReason :text="line.text" :tone="line.tone" />
    </span>

    <ModalShell :open="rejectOpen" size="sm" :aria-label="t(k('reject_dialog.title'))" @close="closeReject">
      <template #header>
        <h3 class="inspector-actions-dialog-title">{{ t(k('reject_dialog.title')) }}</h3>
      </template>
      <div class="inspector-actions-dialog" data-test="actions-reject-dialog">
        <label class="inspector-actions-dialog-label" for="inspector-actions-reason">{{ t(k('reject_dialog.label')) }}</label>
        <Textarea
          id="inspector-actions-reason"
          v-model="rejectReason"
          :rows="4"
          :placeholder="t(k('reject_dialog.placeholder'))"
          :disabled="busy === 'reject'"
          data-test="actions-reject-reason"
        />
        <InspectorReason v-if="failureKey" tone="danger" :text="t(k(failureKey))" data-test="actions-reject-failure" />
      </div>
      <template #footer>
        <div class="inspector-actions-dialog-footer">
          <Button size="sm" variant="ghost" data-test="actions-reject-cancel" @click="closeReject">{{ t(k('reject_dialog.cancel')) }}</Button>
          <Button
            size="sm"
            variant="danger"
            :disabled="!rejectReason.trim() || !availability.reject.enabled"
            :loading="busy === 'reject'"
            data-test="actions-reject-submit"
            @click="submitReject"
          >{{ t(k('reject_dialog.submit')) }}</Button>
        </div>
      </template>
    </ModalShell>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '../../../../../components/ui/Button.vue'
import ModalShell from '../../../../../components/ui/ModalShell.vue'
import Textarea from '../../../../../components/ui/Textarea.vue'
import InspectorReason from '../../parts/InspectorReason.vue'
import type { InspectorTarget } from '../../inspectorTarget'
import { ACTIONS_OPERATIONS } from './actionsModel'
import { useActions } from './useActions'

const props = defineProps<{
  target: InspectorTarget
}>()

const { t } = useI18n()

const k = (key: string) => `gui.inspector.actions.${key}`

const { availability, busy, failureKey, notice, approve, reject, run, rework, clearFailure } = useActions(() => props.target)

const anyVisible = computed(() => ACTIONS_OPERATIONS.some((operation) => availability.value[operation].visible))

/** One line beside the buttons: a refusal, else the disabled reason, else the TR commit line. */
const line = computed(() => {
  if (failureKey.value) return { kind: 'failure', tone: 'danger' as const, text: t(k(failureKey.value)) }
  const disabled = ACTIONS_OPERATIONS.map((operation) => availability.value[operation]).find((item) => item.visible && !item.enabled)
  // `busy` is told by the button's own spinner.
  if (disabled?.reason && disabled.reason !== 'reason.busy') return { kind: 'reason', tone: 'muted' as const, text: t(k(disabled.reason)) }
  if (notice.value) return { kind: 'notice', tone: 'muted' as const, text: t(k(notice.value.key), notice.value.params) }
  return null
})

const shown = computed(() => anyVisible.value || line.value !== null)

const rejectOpen = ref(false)
const rejectReason = ref('')

function openReject() {
  clearFailure()
  rejectReason.value = ''
  rejectOpen.value = true
}

function closeReject() {
  if (busy.value === 'reject') return
  rejectOpen.value = false
}

async function submitReject() {
  if (await reject(rejectReason.value)) rejectOpen.value = false
}

// Another Node: its dialog does not follow.
watch(
  () => props.target.docId,
  () => {
    rejectOpen.value = false
    rejectReason.value = ''
  },
)
</script>

<style scoped>
.inspector-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.inspector-actions-line {
  display: block;
  max-width: 280px;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
}

.inspector-actions-line :deep(.inspector-reason) {
  margin: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.inspector-actions-dialog-title {
  margin: 0;
  padding: 16px 20px 0;
  font-size: 14px;
  font-weight: 700;
}

.inspector-actions-dialog {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.inspector-actions-dialog-label {
  color: var(--fg2-muted);
  font-size: 12px;
}

.inspector-actions-dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 0 20px 16px;
}
</style>
