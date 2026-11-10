<template>
  <!-- flowgate.chat.0017 CH0002 — approve all / reject all for the AI attachment requests of one list (a chat run's block,
       a card's attachments tab). Shown from two waiting requests up. With rows ticked the buttons act on those rows only.
       A bulk approval is always "keep both" and leaves what needs a person's own look on its row: a same-name attachment
       on the target (counted next to the buttons) or a target that changed. Rejecting asks once (it cannot be undone).
       One server call, one result per request; the outcome is one line.
       The reject confirm acts on the requests it named when it opened (ticking rows meanwhile changes nothing), and while
       the confirm is open or a call runs the list's rows are locked (`update:locked`) so nothing is decided twice. -->
  <div v-if="visible" class="ar-bulk" data-test="ar-bulk">
    <div v-if="showControls" class="ar-bulk-bar">
      <label class="ar-bulk-all">
        <input
          type="checkbox"
          data-test="ar-bulk-all"
          :checked="allSelected"
          :indeterminate.prop="someSelected && !allSelected"
          :disabled="locked"
          @change="onToggleAll"
        />
        {{ t('gui.attachment_request.bulk.select_all') }}
      </label>
      <span v-if="excluded" class="ar-bulk-note" data-test="ar-bulk-excluded">{{ t('gui.attachment_request.bulk.excluded', { n: excluded }) }}</span>
      <span class="ar-bulk-spacer" />
      <template v-if="!confirming">
        <Button size="sm" variant="danger" data-test="ar-bulk-reject" :disabled="busy || !rejectTargets.length" @click="startReject">
          {{ t(`gui.attachment_request.bulk.${selectedPending.length ? 'reject_selected' : 'reject_all'}`, { n: rejectTargets.length }) }}
        </Button>
        <Button
          size="sm"
          variant="primary"
          data-test="ar-bulk-approve"
          :loading="busy && running === 'approve'"
          :disabled="busy || !approveTargets.length"
          @click="run('approve')"
        >
          {{ t(`gui.attachment_request.bulk.${selectedPending.length ? 'approve_selected' : 'approve_all'}`, { n: approveTargets.length }) }}
        </Button>
      </template>
    </div>
    <div v-if="confirming" class="ar-bulk-confirm" data-test="ar-bulk-confirm" role="alertdialog">
      <span>{{ t('gui.attachment_request.bulk.reject_confirm', { n: confirmTargets.length }) }}</span>
      <span class="ar-bulk-spacer" />
      <Button size="sm" variant="secondary" data-test="ar-bulk-reject-no" :disabled="busy" @click="cancelReject">
        {{ t('gui.attachment_request.actions.cancel') }}
      </Button>
      <Button size="sm" variant="danger" data-test="ar-bulk-reject-yes" :loading="busy" @click="run('reject')">
        {{ t('gui.attachment_request.actions.reject') }}
      </Button>
    </div>
    <p v-if="resultText" class="ar-bulk-result" :class="{ 'is-error': resultError }" role="status" data-test="ar-bulk-result">{{ resultText }}</p>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '@/components/ui/Button.vue'
import {
  attachmentRequestDecideMany,
  type AttachmentRequest,
  type AttachmentRequestBulkSummary,
} from '@/features/document/api/attachmentRequests'

type Row = Pick<AttachmentRequest, 'id' | 'status' | 'targetDocId' | 'conflict'>

const props = defineProps<{
  /** Every request of the list (decided ones are ignored). */
  requests: readonly Row[]
  /** The ticked request ids (v-model:selected). */
  selected: readonly string[]
  /** The document the screen shows as the target (the card tab: the Node's live document). Default: each request's. */
  expectedDocId?: string | null
}>()

const emit = defineEmits<{
  'update:selected': [ids: string[]]
  /** After a bulk call (also a failed one): the list should be read again. */
  decided: [summary: AttachmentRequestBulkSummary | null]
  /** The rows' own decisions and tick boxes should be locked (a reject confirm is open or a bulk call runs). */
  'update:locked': [locked: boolean]
  /** The bar has something to show (controls, the confirm or a result): keep it drawn even when the list empties. */
  'update:visible': [visible: boolean]
}>()

const { t } = useI18n()

const pending = computed(() => props.requests.filter((r) => r.status === 'pending'))
const selectedPending = computed(() => pending.value.filter((r) => props.selected.includes(r.id)))
const scope = computed(() => (selectedPending.value.length ? selectedPending.value : pending.value))
const approveTargets = computed(() => scope.value.filter((r) => !r.conflict && (props.expectedDocId ?? r.targetDocId)))
const rejectTargets = computed(() => scope.value)
const excluded = computed(() => scope.value.length - approveTargets.value.length)
const allSelected = computed(() => pending.value.length > 0 && selectedPending.value.length === pending.value.length)
const someSelected = computed(() => selectedPending.value.length > 0)
const showControls = computed(() => pending.value.length >= 2)

const busy = ref(false)
const running = ref<'approve' | 'reject' | null>(null)
const confirming = ref(false)
/** The requests the open reject confirm named; the confirm and the call use these, not the live ticks. */
const confirmTargets = ref<Row[]>([])
const resultText = ref('')
const resultError = ref(false)
const visible = computed(() => showControls.value || confirming.value || !!resultText.value)
const locked = computed(() => busy.value || confirming.value)

// Sync: a container drops this bar's list as soon as it empties, so it must know first that a result is shown.
watch(visible, (now) => emit('update:visible', now), { immediate: true, flush: 'sync' })
watch(locked, (now) => emit('update:locked', now), { immediate: true, flush: 'sync' })

watch(showControls, (shown) => {
  if (!shown && !busy.value) cancelReject()
})

function startReject(): void {
  if (busy.value || !rejectTargets.value.length) return
  confirmTargets.value = rejectTargets.value.slice()
  confirming.value = true
}

function cancelReject(): void {
  confirming.value = false
  confirmTargets.value = []
}

function onToggleAll(event: Event): void {
  const on = (event.target as HTMLInputElement).checked
  emit('update:selected', on ? pending.value.map((r) => r.id) : [])
}

function summaryText(summary: AttachmentRequestBulkSummary): string {
  const parts: string[] = []
  if (summary.done) {
    const key = summary.decision === 'approve' ? 'result_approved' : 'result_rejected'
    parts.push(t(`gui.attachment_request.bulk.${key}`, { n: summary.done }))
  }
  if (summary.skipped) parts.push(t('gui.attachment_request.bulk.result_skipped', { n: summary.skipped }))
  if (summary.failed) parts.push(t('gui.attachment_request.bulk.result_failed', { n: summary.failed }))
  return parts.join(' · ')
}

async function run(decision: 'approve' | 'reject'): Promise<void> {
  if (busy.value) return
  const targets = decision === 'approve' ? approveTargets.value : confirmTargets.value
  if (!targets.length) return
  busy.value = true
  running.value = decision
  resultText.value = ''
  resultError.value = false
  try {
    const summary = await attachmentRequestDecideMany(
      decision,
      targets.map((r) => ({ id: r.id, expectedDocId: props.expectedDocId ?? r.targetDocId })),
    )
    resultText.value = summaryText(summary)
    resultError.value = summary.failed > 0
    emit('update:selected', [])
    emit('decided', summary)
  } catch {
    // The call may have been processed in part: never claim either way, read the list again.
    resultText.value = t('gui.attachment_request.bulk.unconfirmed')
    resultError.value = true
    emit('decided', null)
  } finally {
    busy.value = false
    running.value = null
    cancelReject()
  }
}
</script>

<style scoped>
.ar-bulk {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.ar-bulk-bar,
.ar-bulk-confirm {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}
.ar-bulk-confirm {
  padding: 6px 8px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius-sm);
}
.ar-bulk-all {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
}
.ar-bulk-spacer {
  flex: 1 1 auto;
}
.ar-bulk-note,
.ar-bulk-result {
  color: var(--fg2-muted);
}
.ar-bulk-result {
  margin: 0;
  font-size: 12px;
}
.ar-bulk-result.is-error {
  color: var(--fg2-status-warning-fg, var(--fg2-muted));
}
</style>
