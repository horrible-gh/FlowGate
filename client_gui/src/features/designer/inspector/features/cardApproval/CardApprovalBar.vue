<template>
  <!-- The documents tab keeps the approval action and refusal feedback, without a duplicate card-state box.
       A thin container: useCardApproval reads and calls,
       cardApprovalModel decides what is shown. A refusal is one line under the bar — the code's sentence, the
       server's own message, and for a refused document its name (it opens), why, and how many documents were
       approved before it — except a busy refusal, told in a toast while the button stays. -->
  <section
    v-if="bar.shown && state && (bar.approveVisible || (failure && !toasted) || toasts.length)"
    class="inspector-card-approval"
    :class="`is-${state.phase}`"
    role="group"
    :aria-label="t(k('label'))"
    data-test="inspector-card-approval"
    :data-state="state.key"
    :data-phase="state.phase"
  >
    <div v-if="bar.approveVisible" class="inspector-card-approval-line">
      <Button
        class="inspector-card-approval-button"
        size="sm"
        variant="primary"
        :disabled="!bar.enabled"
        :loading="busy"
        :title="bar.reason ? t(k(bar.reason)) : t(c('hint'))"
        data-test="card-approval-approve"
        @click="approve"
      >{{ t(c('approve')) }}</Button>
    </div>
    <div
      v-if="failure && !toasted"
      class="inspector-card-approval-failure"
      data-test="card-approval-failure"
      :data-kind="failure.view.kind"
      :data-code="failure.code ?? ''"
    >
      <InspectorReason :tone="failureTone" :text="sentence" />
      <span v-if="serverMessage" class="inspector-card-approval-note" data-test="card-approval-message">{{ serverMessage }}</span>
      <span v-if="failure.docId" class="inspector-card-approval-doc" data-test="card-approval-refused-doc" :data-doc="failure.docId">
        <span class="inspector-card-approval-doc-name" :title="failure.docId">{{ t(c('refused_document'), { doc: failure.docId }) }}</span>
        <InspectorOpenButton :title="t(k('open_refused'))" data-test="card-approval-refused-open" @click="openDocument(failure.docId)" />
      </span>
      <span v-if="failure.reason" class="inspector-card-approval-note" data-test="card-approval-refused-reason">{{ t(c('refused_reason'), { reason: failure.reason }) }}</span>
      <span v-if="failure.approvedDocIds.length" class="inspector-card-approval-note" data-test="card-approval-approved-before">{{
        t(c('approved_documents'), { n: failure.approvedDocIds.length })
      }}</span>
    </div>
    <ToastHost :toasts="toasts" :dismiss-label="t('gui.common.close')" @dismiss="dismiss" />
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '../../../../../components/ui/Button.vue'
import ToastHost, { type ToastHostItem } from '../../../../../components/ui/ToastHost.vue'
import InspectorOpenButton from '../../parts/InspectorOpenButton.vue'
import InspectorReason from '../../parts/InspectorReason.vue'
import { openDocument } from '../../openDocument'
import type { InspectorTarget } from '../../inspectorTarget'
import { cardApprovalFailureTone, cardApprovalServerMessage, cardApprovalToasted } from './cardApprovalModel'
import { useCardApproval } from './useCardApproval'

const props = defineProps<{
  target: InspectorTarget
}>()

const { t } = useI18n()

const k = (key: string) => `gui.inspector.cardApproval.${key}`
/** The [승인] wording shared with the Canvas card. */
const c = (key: string) => `gui.designer.center.approval.${key}`

const { state, bar, busy, failure, failureSeq, approve } = useCardApproval(() => props.target)

const toasted = computed(() => cardApprovalToasted(failure.value))
const sentence = computed(() => (failure.value ? t(failure.value.view.labelKey) : ''))
const serverMessage = computed(() => (failure.value ? cardApprovalServerMessage(failure.value, sentence.value) : null))
const failureTone = computed(() => (failure.value ? cardApprovalFailureTone(failure.value) : 'danger'))

const toasts = ref<ToastHostItem[]>([])

function dismiss(id: string | number) {
  toasts.value = toasts.value.filter((item) => item.id !== id)
}

// A busy refusal (another AI runs, another card is being approved, the card's AI still works): the button stays,
// the reason is a toast — the code's sentence, then the server's message.
watch(failureSeq, (seq) => {
  const last = failure.value
  if (!last || !cardApprovalToasted(last)) return
  const head = t(last.view.labelKey)
  const more = cardApprovalServerMessage(last, head)
  toasts.value = [...toasts.value.slice(-2), { id: `approval-${seq}`, message: more ? `${head} — ${more}` : head, tone: 'warning', duration: 4000 }]
})
</script>

<style scoped>
.inspector-card-approval {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  margin-top: 4px;
  padding: 8px 10px;
  border: 1px solid var(--fg2-line);
  border-radius: 8px;
  font-size: 12px;
}

.inspector-card-approval.is-awaiting {
  border-color: var(--fg2-status-warning-border);
  background: var(--fg2-status-warning-bg);
}

.inspector-card-approval-line {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.inspector-card-approval-button {
  margin-left: auto;
}

.inspector-card-approval-failure {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.inspector-card-approval-note {
  color: var(--fg2-muted);
  font-size: 11.5px;
  overflow-wrap: anywhere;
}

.inspector-card-approval-doc {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.inspector-card-approval-doc-name {
  min-width: 0;
  overflow: hidden;
  color: var(--fg2-text);
  font-size: 11.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
