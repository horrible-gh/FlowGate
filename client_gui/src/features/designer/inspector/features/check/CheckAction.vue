<template>
  <!-- flowgate.v02.0048 T#12 — the check card's header action (D#1 §10.2 T#12 row): the v0.1 final
       approval of an AC under review. Its dialog asks the Git choice the server offers (merge + push ·
       merge only · wait) and names the merge target; with none offered it is a plain final approval.
       A thin container: useCheck reads and calls, checkModel decides what is shown and usable. A
       disabled final approval shows its one reason; after an answer or a refusal one line says how it
       went and the state is read again. -->
  <div
    v-if="shown"
    class="inspector-check-action"
    role="group"
    :aria-label="t(k('label'))"
    data-test="inspector-check-action"
    :data-busy="busy ? 'approve' : ''"
  >
    <Button
      v-if="availability.approve.visible"
      size="sm"
      variant="primary"
      :disabled="!availability.approve.enabled"
      :loading="busy"
      :title="t(k('approve_hint'))"
      data-test="check-approve"
      @click="openDialog"
    >{{ t(k('approve')) }}</Button>
    <span v-if="line" class="inspector-check-action-line" :title="line.text" data-test="check-action-line" :data-kind="line.kind">
      <InspectorReason :text="line.text" :tone="line.tone" />
    </span>

    <ModalShell :open="dialogOpen" size="sm" :aria-label="t(k('dialog.title'))" @close="closeDialog">
      <template #header>
        <h3 class="inspector-check-dialog-title">{{ t(k('dialog.title')) }}</h3>
      </template>
      <div class="inspector-check-dialog" data-test="check-dialog">
        <p class="inspector-check-dialog-text">{{ t(k('dialog.message')) }}</p>
        <fieldset v-if="offer.choices.length" class="inspector-check-dialog-choices" data-test="check-dialog-choices">
          <legend class="inspector-check-dialog-label">{{ t(k('dialog.git_heading')) }}</legend>
          <label
            v-for="item in offer.choices"
            :key="item"
            class="inspector-check-dialog-choice"
            :data-test="`check-choice-${item}`"
            :data-selected="choice === item"
          >
            <input v-model="choice" type="radio" name="inspector-check-choice" :value="item" :disabled="busy" />
            <span>
              <strong>{{ t(k(`choice.${item}`)) }}</strong>
              <small>{{ t(k(`choice_desc.${item}`)) }}</small>
            </span>
          </label>
          <p v-if="offer.target" class="inspector-check-dialog-label" data-test="check-dialog-target">{{ t(k('dialog.target'), { branch: offer.target }) }}</p>
        </fieldset>
        <p v-else class="inspector-check-dialog-label" data-test="check-dialog-plain">{{ t(k('dialog.plain')) }}</p>
        <InspectorReason v-if="failureKey" tone="danger" :text="t(k(failureKey))" data-test="check-dialog-failure" />
      </div>
      <template #footer>
        <div class="inspector-check-dialog-footer">
          <Button size="sm" variant="ghost" data-test="check-dialog-cancel" @click="closeDialog">{{ t(k('dialog.cancel')) }}</Button>
          <Button
            size="sm"
            variant="primary"
            :disabled="!availability.approve.enabled || (offer.choices.length > 0 && choice === null)"
            :loading="busy"
            data-test="check-dialog-submit"
            @click="submit"
          >{{ t(k('dialog.submit')) }}</Button>
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
import InspectorReason from '../../parts/InspectorReason.vue'
import type { InspectorTarget } from '../../inspectorTarget'
import type { CheckChoice } from './checkModel'
import { useCheck } from './useCheck'

const props = defineProps<{
  target: InspectorTarget
}>()

const { t } = useI18n()

const k = (key: string) => `gui.inspector.check.${key}`

const { availability, offer, busy, failureKey, outcome, approve, clearFailure } = useCheck(() => props.target)

/** One line beside the button: a refusal, else the disabled reason, else how the last final approval went. */
const line = computed(() => {
  if (failureKey.value) return { kind: 'failure', tone: 'danger' as const, text: t(k(failureKey.value)) }
  const item = availability.value.approve
  // `busy` is told by the button's own spinner.
  if (item.visible && item.reason && item.reason !== 'reason.busy') return { kind: 'reason', tone: 'muted' as const, text: t(k(item.reason)) }
  if (outcome.value) {
    const tone = outcome.value.tone === 'success' ? ('muted' as const) : outcome.value.tone
    return { kind: 'outcome', tone, text: t(k(outcome.value.key), { ...outcome.value.params }) }
  }
  return null
})

const shown = computed(() => availability.value.approve.visible || line.value !== null)

const dialogOpen = ref(false)
const choice = ref<CheckChoice | null>(null)

function openDialog() {
  clearFailure()
  choice.value = offer.value.initial
  dialogOpen.value = true
}

function closeDialog() {
  if (busy.value) return
  dialogOpen.value = false
}

async function submit() {
  if (await approve(choice.value)) dialogOpen.value = false
}

// The offer moved (the slot was read again): start on what is offered now.
watch(
  () => offer.value.choices.join(','),
  () => {
    if (choice.value !== null && !offer.value.choices.includes(choice.value)) choice.value = offer.value.initial
  },
)

// Another Node: its dialog does not follow.
watch(
  () => props.target.docId,
  () => {
    dialogOpen.value = false
    choice.value = null
  },
)
</script>

<style scoped>
.inspector-check-action {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.inspector-check-action-line {
  display: block;
  max-width: 320px;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
}

.inspector-check-action-line :deep(.inspector-reason) {
  margin: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.inspector-check-dialog-title {
  margin: 0;
  padding: 16px 20px 0;
  font-size: 14px;
  font-weight: 700;
}

.inspector-check-dialog {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.inspector-check-dialog-text {
  margin: 0;
  font-size: 13px;
}

.inspector-check-dialog-choices {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0;
  padding: 0;
  border: 0;
}

.inspector-check-dialog-label {
  margin: 0;
  color: var(--fg2-muted);
  font-size: 12px;
}

.inspector-check-dialog-choice {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 6px 8px;
  border: 1px solid var(--fg2-border, transparent);
  border-radius: 6px;
  cursor: pointer;
  font-size: 12.5px;
}

.inspector-check-dialog-choice[data-selected='true'] {
  border-color: var(--fg2-accent, currentColor);
}

.inspector-check-dialog-choice span {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.inspector-check-dialog-choice small {
  color: var(--fg2-muted);
}

.inspector-check-dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 0 20px 16px;
}
</style>
