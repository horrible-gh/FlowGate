<template>
  <!--
    flowgate.default.0517 T0026 §2 — [거절] no longer flips the request on the spot: it opens
    this prompt, and the human's reason travels with the decision (stored on the request,
    written to the audit event, and read back by the requesting AI's status call).

    Same shape as the merge-review reject prompt (GitMergeRejectDialog.vue): `form-actions`,
    [취소] + a danger-toned primary [거절 확정] that stays disabled until a reason is typed —
    FlowGate's human-reject convention (document reject and merge reject both refuse an empty
    reason; the server does too). Every close path goes through `requestClose()`, which asks
    before throwing typed text away. Opened on top of the approval dialog it nests on the
    common stack; opened from the Pending list it stands alone.
  -->
  <DialogShell
    :open="store.rejectOpen"
    variant="form-actions"
    surface-class="snap-reject-dialog"
    :busy="busy"
    @request-close="requestClose"
  >
    <template #header>
      <DialogHeader :title="t('main.snapshot_approval.reject_dialog_title')" icon="prohibit" @close="requestClose">
        <template #subtitle>{{ target?.group_id || '-' }}</template>
      </DialogHeader>
    </template>

    <template #default>
      <div v-if="target" class="snap-reject-body">
        <p class="snap-reject-target" data-test="snap-reject-target">
          <span class="snap-reject-provider"><AppIcon name="robot" /> {{ providerLabel }}</span>
          <span class="badge" :class="target.scope === 'whole_source' ? 'badge-yellow' : 'badge-gray'">{{ scopeLabel }}</span>
          <span class="snap-reject-request-reason">{{ target.reason }}</span>
        </p>
        <label class="snap-reject-field">
          {{ t('main.snapshot_approval.reject_reason_label') }}
          <textarea
            v-model="reason"
            rows="4"
            :maxlength="REASON_MAX"
            :placeholder="t('main.snapshot_approval.reject_reason_placeholder')"
            :disabled="busy"
            data-dialog-autofocus
            data-test="snap-reject-reason"
          ></textarea>
        </label>
        <p class="snap-reject-hint">{{ t('main.snapshot_approval.reject_reason_hint') }}</p>
      </div>
    </template>

    <template #footer>
      <DialogFooter :actions="actions" :busy="busy">
        <template #action-reject-confirm><AppIcon name="prohibit" /> {{ t('main.snapshot_approval.reject_confirm') }}</template>
      </DialogFooter>
    </template>
  </DialogShell>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import AppIcon from '@shared/AppIcon.vue'
import DialogShell from './dialogs/DialogShell.vue'
import DialogHeader from './dialogs/DialogHeader.vue'
import DialogFooter from './dialogs/DialogFooter.vue'
import type { DialogAction } from './dialogs/dialogTypes'
import { confirm as dialogConfirm } from '../composables/useDialogStack'
import { useSnapshotRequestsStore } from '../stores/snapshotRequests'
import { useAiProviderStore } from '../stores/aiProvider'
import { useToast } from './common/useToast'

// Server cap (snapshot_request_service.REJECTION_REASON_MAX).
const REASON_MAX = 4000

const { t } = useI18n()
const store = useSnapshotRequestsStore()
const aiProviderStore = useAiProviderStore()
const { showToast } = useToast()

const reason = ref('')
const busy = ref(false)
const target = computed(() => store.rejectTarget)

// Each opening starts blank; a failed POST leaves the text on screen (the box stays open).
watch(() => store.rejectTarget?.snapshot_id, (id) => {
  if (id) reason.value = ''
})

const SCOPE_LABEL_KEYS: Record<string, string> = {
  single_file: 'scope_single_file',
  selected_files: 'scope_selected_files',
  directory: 'scope_directory',
  whole_source: 'scope_whole_source',
}
const scopeLabel = computed(() => {
  const key = SCOPE_LABEL_KEYS[target.value?.scope ?? '']
  return key ? t(`main.snapshot_approval.${key}`) : (target.value?.scope ?? '')
})

const providerLabel = computed(() => {
  const id = target.value?.provider_id
  if (!id) return t('main.snapshot_approval.unknown_provider')
  return aiProviderStore.providers.find((p) => p.id === id)?.name || id
})

async function requestClose() {
  if (busy.value) return
  if (reason.value.trim()) {
    const discard = await dialogConfirm({
      title: t('main.snapshot_approval.reject_discard_title'),
      message: t('main.snapshot_approval.reject_discard_message'),
      confirmLabel: t('main.snapshot_approval.reject_discard_ok'),
      danger: true,
    })
    if (!discard) return
  }
  // The request stays `requested` — closing this prompt never decides anything.
  store.closeReject()
}

async function confirmReject() {
  const current = target.value
  const text = reason.value.trim()
  if (!current || !text || busy.value) return
  busy.value = true
  try {
    await store.reject(current.snapshot_id, text)
  } catch {
    showToast(t('main.snapshot_approval.reject_failed'), 'danger')
  } finally {
    busy.value = false
  }
}

const actions = computed<DialogAction[]>(() => [
  { id: 'cancel', label: t('common.cancel'), role: 'cancel', disabled: busy.value, onSelect: requestClose },
  {
    id: 'reject-confirm',
    label: t('main.snapshot_approval.reject_confirm'),
    role: 'primary',
    tone: 'danger',
    disabled: busy.value || !reason.value.trim(),
    onSelect: confirmReject,
  },
])
</script>

<style scoped>
.snap-reject-body { display: flex; flex-direction: column; gap: 10px; }
.snap-reject-target {
  margin: 0;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 8px;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: var(--r);
  background: var(--bg);
  font-size: .76rem;
}
.snap-reject-provider { display: inline-flex; align-items: center; gap: 5px; font-weight: 600; color: var(--text); }
.snap-reject-request-reason { flex-basis: 100%; color: var(--text-s); white-space: pre-wrap; word-break: break-word; }
.snap-reject-field { display: flex; flex-direction: column; gap: 4px; font-size: .78rem; font-weight: 600; color: var(--text); }
.snap-reject-field textarea {
  padding: 8px; border: 1px solid var(--border, #cbd5e1); border-radius: 6px; font: inherit; font-size: .8rem; font-weight: 400; resize: vertical;
}
.snap-reject-hint { margin: 0; font-size: .72rem; color: var(--text-m); }
</style>

<!-- Unscoped: the surface is teleported out of this subtree (same reason as GitMergeRejectDialog). -->
<style>
.fg-dialog-surface.snap-reject-dialog {
  width: min(480px, calc(100vw - 48px));
}
</style>
