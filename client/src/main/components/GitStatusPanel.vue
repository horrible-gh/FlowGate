<template>
  <!-- flowgate.default.0162 §2·§3 — project Git control panel ("관제소"). Renders
       only for git-integrated projects; hidden entirely otherwise. -->
  <div v-if="status && status.enabled" class="card git-status-card">
    <div class="card-hd">
      <span class="card-title">
        <i class="fa-solid fa-diagram-project" style="color:var(--text-m);"></i>
        {{ t('main.git_status.title') }}
      </span>
      <span class="git-branch-badge">
        <i class="fa-solid fa-code-commit"></i>
        {{ t('main.git_status.base_label') }}: {{ status.base_branch }}
      </span>
      <span class="git-ab-meta">{{ aheadBehindText }}</span>
      <button
        class="git-refresh-btn"
        :disabled="busy"
        :title="t('main.git_status.fetch')"
        @click="doFetch"
      >
        <i class="fa-solid fa-cloud-arrow-down"></i>
      </button>
    </div>
    <div class="card-bd pad">
      <!-- Finalize-pending list (each item executes or opens) -->
      <div class="git-status-sect">
        <p class="git-status-sub">
          {{ t('main.git_status.pending_header') }} ({{ status.pending_count }})
        </p>
        <p v-if="!status.pending.length" class="git-status-empty">
          {{ t('main.git_status.no_pending') }}
        </p>
        <div v-for="p in status.pending" :key="p.group_id" class="git-status-row">
          <span class="git-status-gid">{{ p.group_id }}</span>
          <span class="badge" :class="statusBadgeClass(p.status)">{{ statusLabel(p.status) }}</span>
          <span class="git-status-spacer"></span>
          <button
            class="btn btn-sm btn-primary"
            :disabled="busy || p.status === 'conflict'"
            :title="p.status === 'conflict' ? t('main.git_status.conflict_open_hint') : ''"
            @click="execute(p)"
          >
            <i class="fa-solid fa-play"></i> {{ t('main.git_finalize.execute') }}
          </button>
          <button class="btn btn-sm btn-secondary" @click="emit('open-group', p.group_id)">
            <i class="fa-solid fa-arrow-up-right-from-square"></i> {{ t('main.git_status.open') }}
          </button>
        </div>
      </div>

      <!-- Active branch slots (informational) -->
      <div class="git-status-sect">
        <p class="git-status-sub">
          {{ t('main.git_status.slots_header') }} ({{ status.slots.length }})
        </p>
        <p v-if="!status.slots.length" class="git-status-empty">
          {{ t('main.git_status.empty_slots') }}
        </p>
        <div v-for="s in status.slots" :key="s.group_id" class="git-status-slot">
          <i class="fa-solid fa-code-branch"></i>
          <span class="git-status-branch">{{ s.branch }}</span>
          <span class="git-status-slot-gid">{{ s.group_id }}</span>
          <span class="badge" :class="statusBadgeClass(s.status)">{{ statusLabel(s.status) }}</span>
        </div>
      </div>

      <!-- Recovery (manual): re-push the base branch when automation stalled -->
      <div class="git-status-sect git-status-recovery">
        <p class="git-status-sub">{{ t('main.git_status.recovery_header') }}</p>
        <button class="btn btn-sm btn-secondary" :disabled="busy" @click="doPush(status.base_branch)">
          <i class="fa-solid fa-cloud-arrow-up"></i>
          {{ t('main.git_status.push') }} ({{ status.base_branch }})
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getRequest, postRequest } from '@shared/api'
import { useToast } from './common/useToast'

const props = defineProps<{ projectId: string }>()
const emit = defineEmits<{ 'open-group': [groupId: string] }>()

const { t } = useI18n()
const { showToast } = useToast()

interface Slot {
  group_id: string
  branch: string | null
  status: string
  merge_id: number | null
}
interface Pending {
  group_id: string
  branch: string | null
  status: string
  default_action: string
}
interface GitStatus {
  enabled: boolean
  base_branch: string | null
  base_path_state: string
  ahead_count: number | null
  behind_count: number | null
  slots: Slot[]
  pending: Pending[]
  pending_count: number
}

const status = ref<GitStatus | null>(null)
const busy = ref(false)

const aheadBehindText = computed(() => {
  const s = status.value
  if (!s || s.ahead_count == null || s.behind_count == null) {
    return t('main.git_status.unmeasured')
  }
  return t('main.git_finalize.ahead_behind', { ahead: s.ahead_count, behind: s.behind_count })
})

function statusLabel(s: string): string {
  return t(`main.git_finalize.status.${s}`)
}
function statusBadgeClass(s: string): string {
  switch (s) {
    case 'merged':
    case 'pushed':
      return 'badge-blue'
    case 'conflict':
      return 'badge-red'
    default:
      return 'badge-yellow'
  }
}

async function fetchStatus() {
  if (!props.projectId) {
    status.value = null
    return
  }
  try {
    const { data } = await getRequest<{ ok: boolean; status: GitStatus }>(
      `/api/v1/projects/${props.projectId}/git/status`,
    )
    status.value = data.status
  } catch {
    status.value = null // 403/404 — panel stays hidden
  }
}

async function execute(item: Pending) {
  if (busy.value) return
  busy.value = true
  try {
    const { data } = await postRequest<{ ok: boolean; result?: any; error?: any }>(
      `/api/v1/groups/${item.group_id}/git/finalize`,
      { action: item.default_action },
    )
    if (data.ok === false) {
      showToast(data.error?.message || t('main.git_finalize.failed'), 'danger')
    } else {
      const r = data.result
      if (r?.status === 'conflict') {
        showToast(t('main.git_finalize.conflict_toast', { n: (r.conflict_files || []).length }), 'warning')
        emit('open-group', item.group_id) // conflicts are resolved in the finalize panel
      } else if (r?.status === 'merged') {
        showToast(t('main.git_finalize.merged_toast', { commit: r.merge_commit || '' }), 'success')
      } else if (r?.status === 'pushed') {
        showToast(t('main.git_finalize.pushed_toast'), 'success')
      } else if (r?.status === 'waiting') {
        showToast(t('main.git_finalize.waiting_toast'), 'success')
      }
    }
  } catch (e: any) {
    showToast(e?.response?.data?.error?.message || t('main.git_finalize.failed'), 'danger')
  } finally {
    busy.value = false
    await fetchStatus()
  }
}

async function doFetch() {
  if (busy.value || !props.projectId) return
  busy.value = true
  try {
    const { data } = await postRequest<{ ok: boolean; result?: any; error?: any }>(
      `/api/v1/projects/${props.projectId}/git/fetch`,
      {},
    )
    if (data.ok === false) {
      showToast(data.error?.message || t('main.git_status.failed'), 'danger')
    } else {
      showToast(t('main.git_status.fetch_done', { behind: data.result?.behind_count ?? 0 }), 'success')
    }
  } catch (e: any) {
    showToast(e?.response?.data?.error?.message || t('main.git_status.failed'), 'danger')
  } finally {
    busy.value = false
    await fetchStatus()
  }
}

async function doPush(branch: string | null) {
  if (busy.value || !props.projectId || !branch) return
  busy.value = true
  try {
    const { data } = await postRequest<{ ok: boolean; result?: any; error?: any }>(
      `/api/v1/projects/${props.projectId}/git/push`,
      { branch },
    )
    if (data.ok === false) {
      showToast(data.error?.message || t('main.git_status.failed'), 'danger')
    } else {
      showToast(t('main.git_status.push_done', { branch: data.result?.branch || branch }), 'success')
    }
  } catch (e: any) {
    showToast(e?.response?.data?.error?.message || t('main.git_status.failed'), 'danger')
  } finally {
    busy.value = false
    await fetchStatus()
  }
}

// Live badge/list sync: the SSE bridge re-broadcasts git_pending_changed as a
// window event carrying the server-recomputed pending_count (L §2.3). We refetch
// the whole panel (absolute-value convergence, no local increment).
function onPendingChanged(e: Event) {
  const detail = (e as CustomEvent).detail || {}
  if (!detail.project || detail.project === props.projectId) {
    fetchStatus()
  }
}

onMounted(() => {
  fetchStatus()
  if (typeof window !== 'undefined') {
    window.addEventListener('fg:git_pending_changed', onPendingChanged)
  }
})
onUnmounted(() => {
  if (typeof window !== 'undefined') {
    window.removeEventListener('fg:git_pending_changed', onPendingChanged)
  }
})

watch(() => props.projectId, fetchStatus)

defineExpose({ fetchStatus })
</script>

<style scoped>
.git-status-card {
  margin-bottom: 12px;
}
.git-status-card .card-hd {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.git-branch-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 8px;
  font-size: 0.72rem;
  font-family: var(--mono, ui-monospace, monospace);
  color: #0369a1;
  background: #e0f2fe;
  border: 1px solid #bae6fd;
  border-radius: 999px;
}
.git-ab-meta {
  font-size: 0.74rem;
  color: var(--text-m);
}
.git-refresh-btn {
  margin-left: auto;
  border: none;
  background: none;
  color: var(--text-m);
  cursor: pointer;
  padding: 4px 6px;
}
.git-refresh-btn:hover {
  color: var(--primary);
}
.git-status-sect {
  margin-bottom: 14px;
}
.git-status-sub {
  font-size: 0.72rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  color: var(--text-m);
  margin: 0 0 6px;
}
.git-status-empty {
  font-size: 0.78rem;
  color: var(--text-m);
  margin: 0;
}
.git-status-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 0;
  border-bottom: 1px solid var(--border, #eef2f6);
}
.git-status-gid {
  font-size: 0.8rem;
  font-family: var(--mono, ui-monospace, monospace);
}
.git-status-spacer {
  flex: 1 1 auto;
}
.git-status-slot {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
  font-size: 0.78rem;
}
.git-status-branch {
  font-family: var(--mono, ui-monospace, monospace);
  color: #0369a1;
}
.git-status-slot-gid {
  color: var(--text-m);
  flex: 1 1 auto;
}
.git-status-recovery {
  border-top: 1px dashed var(--border, #e2e8f0);
  padding-top: 10px;
}
.btn-sm {
  padding: 3px 9px;
  font-size: 0.75rem;
}
.badge-red {
  background: #fef2f2;
  color: #b91c1c;
}
</style>
