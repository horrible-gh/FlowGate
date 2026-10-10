<template>
  <!-- flowgate.v02.0048 T#10 — the R↔B kind switch in the header (D#1 §10.2 종류 전환 row, D#2 §3.4):
       on an unassigned Flow's requirement / bug root, one button turns it into the other kind after a
       confirmation (the doc_id changes). A thin container: useKindSwitch reads and calls,
       kindSwitchModel decides what is shown and usable. A disabled switch shows its one reason; a
       refused one shows why and the state is read again. -->
  <div
    v-if="shown"
    class="inspector-kind-switch"
    role="group"
    :aria-label="t(k('label'))"
    data-test="inspector-kind-switch"
    :data-busy="busy ? 'true' : 'false'"
  >
    <Button
      v-if="availability.switch.visible && to"
      size="sm"
      variant="secondary"
      :disabled="!availability.switch.enabled"
      :loading="busy"
      :title="t(k('switch_hint'), names)"
      data-test="kind-switch-open"
      :data-to="to"
      @click="openConfirm"
    >{{ t(k('switch'), names) }}</Button>
    <span v-if="line" class="inspector-kind-switch-line" :title="line.text" data-test="kind-switch-line" :data-kind="line.kind">
      <InspectorReason :text="line.text" :tone="line.tone" />
    </span>

    <ConfirmDialog
      :open="confirmOpen"
      :title="t(k('confirm.title'))"
      :message="confirmMessage"
      :confirm-label="t(k('confirm.submit'))"
      :cancel-label="t(k('confirm.cancel'))"
      @confirm="submit"
      @cancel="closeConfirm"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '../../../../../components/ui/Button.vue'
import ConfirmDialog from '../../../../../components/ui/ConfirmDialog.vue'
import InspectorReason from '../../parts/InspectorReason.vue'
import type { InspectorTarget } from '../../inspectorTarget'
import { useKindSwitch } from './useKindSwitch'

const props = defineProps<{
  target: InspectorTarget
}>()

const { t } = useI18n()

const k = (key: string) => `gui.inspector.kindSwitch.${key}`

const { availability, to, busy, failureKey, done, run, clearFailure } = useKindSwitch(() => props.target)

const from = computed(() => (to.value === 'B' ? 'R' : to.value === 'R' ? 'B' : null))

/** The kind names the strings use ({from} / {to}). */
const names = computed(() => ({
  from: from.value ? t(k(`kinds.${from.value}`)) : '',
  to: to.value ? t(k(`kinds.${to.value}`)) : '',
}))

/** `…0001-R` -> `0001-R`: the document number the switch changes. */
const codeOf = (docId: string | null) => (docId ?? '').split('.').pop() ?? ''

const confirmMessage = computed(() => {
  const fromCode = codeOf(props.target.docId)
  const toCode = to.value ? fromCode.replace(/-[RB]$/i, `-${to.value}`) : fromCode
  return t(k('confirm.message'), { ...names.value, from_code: fromCode, to_code: toCode })
})

/** One line beside the button: a refusal, else the finished switch, else the disabled reason. */
const line = computed(() => {
  if (failureKey.value) return { kind: 'failure', tone: 'danger' as const, text: t(k(failureKey.value)) }
  if (done.value) {
    const kind = done.value.document.typeCode === 'B' ? 'B' : 'R'
    return { kind: 'done', tone: 'muted' as const, text: t(k('done'), { to: t(k(`kinds.${kind}`)), doc: codeOf(done.value.document.docId) }) }
  }
  const item = availability.value.switch
  // `busy` is told by the button's own spinner.
  if (item.visible && !item.enabled && item.reason && item.reason !== 'reason.busy') {
    return { kind: 'reason', tone: 'muted' as const, text: t(k(item.reason)) }
  }
  return null
})

const shown = computed(() => availability.value.switch.visible || line.value !== null)

const confirmOpen = ref(false)

function openConfirm() {
  if (!availability.value.switch.enabled) return
  clearFailure()
  confirmOpen.value = true
}

function closeConfirm() {
  confirmOpen.value = false
}

async function submit() {
  confirmOpen.value = false
  await run()
}

// Another Node: its confirmation does not follow.
watch(
  () => props.target.nodeId,
  () => {
    confirmOpen.value = false
  },
)
</script>

<style scoped>
.inspector-kind-switch {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.inspector-kind-switch-line {
  display: block;
  max-width: 280px;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
}

.inspector-kind-switch-line :deep(.inspector-reason) {
  margin: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
