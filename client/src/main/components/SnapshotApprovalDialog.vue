<template>
  <!--
    flowgate.default.0517 T0012 — AI Scratch Source Snapshot detail approval dialog.
    Mockup: MirageGlass deck yoylzrdu v3 (index.html + whole-source.html — one component,
    the whole_source extras render conditionally per §7).

    `readonly` is the only common-layer variant whose default size is `lg` (720px), the
    width the mockup measured for this field count (D0005-NR §3.2) — its footer is NOT
    actually read-only (GitArchiveCatalogDialog set the same precedent). `close-on-backdrop`
    is left at the variant default `false` (T0012 §3 — no separate backdrop-close action).
  -->
  <DialogShell
    ref="shellRef"
    :open="store.detailOpen"
    variant="readonly"
    surface-class="snap-approval-dialog"
    :busy="busy"
    @request-close="onRequestClose"
  >
    <template #header>
      <DialogHeader
        :title="isWholeSource ? t('main.snapshot_approval.dialog_title_whole_source') : t('main.snapshot_approval.dialog_title')"
        icon="clock"
        @close="shellRef?.requestClose('header')"
      >
        <template #subtitle>{{ request?.group_id || '-' }}</template>
      </DialogHeader>
    </template>

    <template #default>
      <!--
        T0012 §5: the shell's default initial-focus tree is `autofocus ?? primary ?? …`,
        which would land on [승인] since it is this footer's only `primary` action — the
        exact "승인 기본값" R §3.2 forbids. `data-dialog-autofocus` on this inert content
        wrapper (no click handler, `tabindex="-1"`) pre-empts that without needing a
        `cancel`-role action this dialog deliberately does not have (§3 "취소 버튼은 두지
        않는다").
      -->
      <div v-if="request" class="snap-dlg-body" tabindex="-1" data-dialog-autofocus>
        <div class="snap-field-grid">
          <div class="snap-field">
            <label>{{ t('main.snapshot_approval.field_requested_ai') }}</label>
            <span class="snap-provider">
              <AppIcon name="robot" />
              {{ providerLabel }}
            </span>
          </div>
          <div class="snap-field">
            <label>{{ t('main.snapshot_approval.field_group') }}</label>
            <span><AppIcon name="folder" /> {{ request.group_id }}</span>
          </div>
          <div class="snap-field">
            <label>{{ t('main.snapshot_approval.field_requested_at') }}</label>
            <span :title="request.requested_at">{{ formatDashboardTime(request.requested_at) }}</span>
          </div>
          <div class="snap-field">
            <label>{{ t('main.snapshot_approval.field_source') }}</label>
            <!-- D0007 §6.4 intentional deviation from the mockup: `current_worktree` is the
                 only value there is — no selector, no `main`/`base` option (T0012 §6). -->
            <span class="badge badge-blue">{{ t('main.snapshot_approval.source_value') }}</span>
          </div>
          <div class="snap-field">
            <label>{{ t('main.snapshot_approval.field_scope') }}</label>
            <span class="badge" :class="isWholeSource ? 'badge-yellow' : 'badge-gray'">{{ scopeLabel }}</span>
          </div>
        </div>

        <!-- Mockup order: field grid, THEN the whole_source warning banner, then paths/
             reason/purpose (yoylzrdu v3 ③ — the banner sits below the kv rows, not above
             them). -->
        <div v-if="isWholeSource" class="alert alert-warning snap-whole-source-alert" data-test="snap-whole-source-warning">
          <AppIcon name="warning" />
          <div>
            <strong>{{ t('main.snapshot_approval.whole_source_warning_title') }}</strong>
            <p>{{ t('main.snapshot_approval.whole_source_warning_body') }}</p>
          </div>
        </div>

        <div class="snap-field snap-field-block">
          <label>{{ t('main.snapshot_approval.field_paths') }}</label>
          <div v-if="request.requested_paths.length" class="snap-paths">
            <div v-for="path in request.requested_paths" :key="path" class="snap-path-row">{{ path }}</div>
          </div>
          <!-- whole_source carries no paths (server validation forbids it) — the mockup
               still renders the path box with a single explanatory placeholder row. -->
          <div v-else class="snap-paths">
            <div class="snap-path-row">. ({{ t('main.snapshot_approval.scope_whole_source') }})</div>
          </div>
        </div>

        <div class="snap-field snap-field-block">
          <label>
            {{ t('main.snapshot_approval.field_reason') }}
            <span v-if="isWholeSource" class="snap-required-hint">{{ t('main.snapshot_approval.whole_source_reason_required') }}</span>
          </label>
          <p class="snap-text-block">{{ request.reason }}</p>
        </div>

        <!-- D0007 §6.4 intentional deviation #2: purpose is a separate field, never merged
             into reason (T0012 §4 "한 항목으로 합치지 않는다"). -->
        <div class="snap-field snap-field-block">
          <label>{{ t('main.snapshot_approval.field_purpose') }}</label>
          <p class="snap-text-block">{{ request.purpose }}</p>
        </div>

        <!-- Mockup ①/③ "이 외에 대기 중인 snapshot 요청이 N건 더 있습니다" — other pending
             requests are not silently invisible while this one is open. -->
        <p v-if="otherPendingCount > 0" class="snap-more-pending" data-test="snap-more-pending">
          {{ t('main.snapshot_approval.more_pending', { n: otherPendingCount }) }}
          <button type="button" class="snap-more-pending-link" @click="openPendingList">
            {{ t('main.snapshot_approval.more_pending_link') }}
          </button>
        </p>
      </div>
    </template>

    <template #footer>
      <DialogFooter :actions="actions" :busy="busy">
        <template #action-reject><AppIcon name="prohibit" /> {{ t('main.snapshot_approval.reject') }}</template>
        <template #action-approve><AppIcon name="check" /> {{ t('main.snapshot_approval.approve') }}</template>
      </DialogFooter>
    </template>
  </DialogShell>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import AppIcon from '@shared/AppIcon.vue'
import DialogShell from './dialogs/DialogShell.vue'
import DialogHeader from './dialogs/DialogHeader.vue'
import DialogFooter from './dialogs/DialogFooter.vue'
import type { DialogAction, DialogCloseReason } from './dialogs/dialogTypes'
import { useSnapshotRequestsStore } from '../stores/snapshotRequests'
import { useAiProviderStore } from '../stores/aiProvider'
import { useActivityFormat } from '../composables/useActivityFormat'
import { useToast } from './common/useToast'

const { t } = useI18n()
const store = useSnapshotRequestsStore()
const aiProviderStore = useAiProviderStore()
const { formatDashboardTime } = useActivityFormat()
const { showToast } = useToast()

const shellRef = ref<InstanceType<typeof DialogShell> | null>(null)
const busy = ref(false)
const request = computed(() => store.detailTarget)
const isWholeSource = computed(() => request.value?.scope === 'whole_source')

const SCOPE_LABEL_KEYS: Record<string, string> = {
  single_file: 'scope_single_file',
  selected_files: 'scope_selected_files',
  directory: 'scope_directory',
  whole_source: 'scope_whole_source',
}
const scopeLabel = computed(() => {
  const key = SCOPE_LABEL_KEYS[request.value?.scope ?? '']
  return key ? t(`main.snapshot_approval.${key}`) : (request.value?.scope ?? '')
})

const providerLabel = computed(() => {
  const id = request.value?.provider_id
  if (!id) return t('main.snapshot_approval.unknown_provider')
  const found = aiProviderStore.providers.find((p) => p.id === id)
  return found?.name || id
})

const otherPendingCount = computed(() => {
  const current = request.value?.snapshot_id
  if (!current) return 0
  return store.pending.filter((row) => row.snapshot_id !== current).length
})

// Mockup ①/③ footer note's link — opens the Pending list, which is the notification
// panel's [승인 대기] section since T0026 (NotificationCenter owns the panel's own `open`
// state, so this only asks for it via a window signal, mirroring the other `fg:*` bridges
// this feature already uses).
function openPendingList() {
  store.closeDetail()
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent('fg:snapshot_open_pending_panel'))
  }
}

// T0012 §7/§10: X/ESC leaves the request `requested` — no approve/reject call here.
function onRequestClose(_reason: DialogCloseReason) {
  if (busy.value || store.rejectOpen) return
  store.closeDetail()
}

async function approve() {
  const target = request.value
  if (!target) return
  busy.value = true
  try {
    await store.approve(target.snapshot_id)
  } catch {
    showToast(t('main.snapshot_approval.approve_failed'), 'danger')
  } finally {
    busy.value = false
  }
}

// T0026 §2: [거절] never decides on the spot — it opens the reason prompt, which nests on
// the common stack above this dialog. A successful rejection there closes both (the store
// drops this dialog's target when the decision settles); cancelling it returns here.
function reject() {
  const target = request.value
  if (!target) return
  store.openReject(target)
}

// T0012 §3 footer: [거절] danger / [승인] primary — no cancel action at all.
const actions = computed<DialogAction[]>(() => [
  { id: 'reject', label: t('main.snapshot_approval.reject'), role: 'danger', tone: 'danger', onSelect: reject },
  { id: 'approve', label: t('main.snapshot_approval.approve'), role: 'primary', onSelect: approve },
])
</script>

<style scoped>
.snap-dlg-body { display: flex; flex-direction: column; gap: 14px; outline: none; }
.snap-whole-source-alert { align-items: flex-start; }
.snap-whole-source-alert p { margin: 4px 0 0; }
.snap-field-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 10px 18px;
}
.snap-field { display: flex; flex-direction: column; gap: 3px; }
.snap-field-block { grid-column: 1 / -1; }
.snap-field label {
  color: var(--text-m);
  font-size: .64rem;
  font-weight: 700;
  letter-spacing: .04em;
  display: flex;
  align-items: center;
  gap: 6px;
}
.snap-field > span { color: var(--text); font-size: .82rem; display: inline-flex; align-items: center; gap: 6px; width: fit-content; }
.snap-provider { font-weight: 600; }
.snap-required-hint { color: var(--danger); font-weight: 700; letter-spacing: 0; }
.snap-paths {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: var(--r);
  background: var(--bg);
  max-height: 160px;
  overflow-y: auto;
}
.snap-path-row { font-family: 'JetBrains Mono', monospace; font-size: .76rem; color: var(--text-s); word-break: break-all; }
.snap-text-block {
  margin: 0;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: var(--r);
  background: var(--bg);
  color: var(--text);
  font-size: .82rem;
  white-space: pre-wrap;
  word-break: break-word;
}
.snap-more-pending {
  margin: 0;
  padding: 8px 10px;
  border: 1px solid #bae6fd;
  border-radius: var(--r);
  background: var(--info-l, #e0f2fe);
  color: var(--info, #0284c7);
  font-size: .78rem;
}
.snap-more-pending-link {
  margin-left: 4px;
  color: var(--primary);
  font-weight: 700;
  text-decoration: underline;
}

:global(.fg-dialog-surface.snap-approval-dialog) { width: 720px; max-width: 94vw; }
</style>
