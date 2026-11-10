<template>
  <!-- flowgate.chat.0017 CH0002 — the AI attachment upload requests of one run, as one block right below that run's AI
       message (the server's anchor: after the run's last reply). The AI never waited for them; they stay here, pending,
       after the run ended until someone decides. Each row (preview / download / approve / reject) is the container's
       request row component; deciding is open in reading and editing mode alike. -->
  <section class="tl-ar" data-test="tl-attachments" :data-run="activity.runId ?? ''">
    <div class="tl-ar-head">
      <span aria-hidden="true">📎</span>
      <span>{{ t('gui.attachment_request.title') }}</span>
      <span v-if="pendingCount" class="tl-ar-pending" data-test="tl-attachments-pending">
        {{ t('gui.attachment_request.pending_n', { n: pendingCount }) }}
      </span>
    </div>
    <!-- Approve all / reject all (the container's bar, from two waiting requests up); ticked rows narrow it. -->
    <component
      :is="bulk"
      v-if="row && bulk"
      v-model:selected="selected"
      :requests="bulkRows"
      @decided="onBulkDecided"
      @update:locked="bulkLocked = $event"
    />
    <div class="tl-ar-list">
      <template v-if="row">
        <component
          :is="row"
          v-for="request in shown"
          :key="request.id"
          :request="request"
          show-target
          :selectable="!!bulk && pendingCount >= 2"
          :selected="selected.includes(request.id)"
          :disabled="bulkLocked"
          @decided="onDecided"
          @select="onSelect"
        />
      </template>
      <!-- Without the container's row: the requests are still listed (name and state), never hidden. -->
      <template v-else>
        <p v-for="request in activity.requests" :key="request.id" class="tl-ar-line" data-test="tl-attachments-line">
          {{ request.filename }} · {{ t(`gui.attachment_request.status.${statusKey(request.status)}`) }}
        </p>
      </template>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, inject, ref, watch, type Component, type Ref } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  ATTACHMENT_REQUEST_BULK,
  ATTACHMENT_REQUEST_REFRESH,
  ATTACHMENT_REQUEST_ROW,
  type AttachmentRequestActivity,
  type ConversationAttachmentRequest,
} from './types'

const props = defineProps<{ activity: AttachmentRequestActivity }>()

const { t } = useI18n()

const injected = inject<Ref<Component | null> | null>(ATTACHMENT_REQUEST_ROW, null)
const row = computed(() => injected?.value ?? null)
const injectedBulk = inject<Ref<Component | null> | null>(ATTACHMENT_REQUEST_BULK, null)
const bulk = computed(() => injectedBulk?.value ?? null)
const refresh = inject<(() => unknown) | null>(ATTACHMENT_REQUEST_REFRESH, null)
/** The bar's reject confirm is open or its call runs: the rows' own decisions and ticks wait. */
const bulkLocked = ref(false)

// Rows a bulk decision answered, until the next server read of the activity replaces them.
const answered = ref<Record<string, ConversationAttachmentRequest>>({})
const shown = computed(() => props.activity.requests.map((r) => answered.value[r.id] ?? r))
const selected = ref<string[]>([])

const STATUSES = ['pending', 'approving', 'approved', 'rejected', 'expired', 'failed']
const statusKey = (status: string) => (STATUSES.includes(status) ? status : 'failed')

// Decisions made from this block, until the next server read of the activity replaces them.
const decided = ref<Record<string, string>>({})
watch(
  () => props.activity,
  () => {
    decided.value = {}
    answered.value = {}
    const open = new Set(props.activity.requests.filter((r) => r.status === 'pending').map((r) => r.id))
    selected.value = selected.value.filter((id) => open.has(id))
  },
)

const pendingCount = computed(
  () =>
    shown.value.filter((r) => {
      const status = decided.value[r.id] ?? r.status
      return status === 'pending' || status === 'approving'
    }).length,
)

// The bar counts with the rows' own decisions too (a row decided alone is not offered again).
const bulkRows = computed(() => shown.value.map((r) => (decided.value[r.id] ? { ...r, status: decided.value[r.id] } : r)))

function onDecided(request: { id: string; status: string }): void {
  decided.value = { ...decided.value, [request.id]: request.status }
  selected.value = selected.value.filter((id) => id !== request.id)
}

function onSelect(id: string, on: boolean): void {
  const rest = selected.value.filter((value) => value !== id)
  selected.value = on ? [...rest, id] : rest
}

function onBulkDecided(summary: { results?: readonly { id: string; request: ConversationAttachmentRequest | null }[] } | null): void {
  const next = { ...answered.value }
  for (const result of summary?.results ?? []) if (result.request) next[result.id] = result.request
  answered.value = next
  // The server state either way: a failed call may have been processed in part, a done one replaces `answered`.
  void refresh?.()
}
</script>

<style scoped>
.tl-ar {
  margin: 4px 0 8px 32px;
  padding: 8px;
  border-radius: 8px;
  border: 1px solid var(--fg-border, #d0d7de);
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.tl-ar-head {
  display: flex;
  gap: 6px;
  align-items: center;
  font-size: 13px;
  font-weight: 600;
}
.tl-ar-pending {
  margin-left: auto;
  font-size: 12px;
  font-weight: 400;
  color: var(--fg-warning, #9a6700);
}
.tl-ar-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.tl-ar-line {
  margin: 0;
  font-size: 12px;
}
</style>
