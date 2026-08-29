<template>
  <!-- flowgate.default.0162 §3.3 — "안전망" action-bar Git button + badge.
       Renders only for a git-integrated current project; a quiet icon when there
       is no finalize backlog, a labelled attention button + pending-count badge
       when there is (0165 T0004: discoverability). The badge is a work counter
       that only clears by real processing (no read-to-dismiss). -->
  <div v-if="status && status.enabled" class="git-menu-wrap">
    <button
      class="hdr-btn git-menu-btn"
      :class="{ 'git-menu-attn': status.pending_count > 0 }"
      type="button"
      :title="t('main.git_menu.tooltip')"
      @click.stop="toggleDropdown"
    >
      <AppIcon name="git-branch" />
      <span v-if="status.pending_count > 0" class="git-menu-label">
        {{ t('main.git_menu.label') }}
      </span>
      <span
        v-if="status.pending_count > 0"
        class="git-menu-badge"
      >{{ status.pending_count }}</span>
    </button>

    <div v-if="dropdownOpen" class="git-menu-dd" @click.stop>
      <div class="git-menu-dd-hd">
        {{ t('main.git_menu.title') }} ({{ status.pending_count }})
      </div>
      <p v-if="!status.pending.length" class="git-menu-empty">
        {{ t('main.git_menu.empty') }}
      </p>
      <div v-for="p in status.pending" :key="p.group_id" class="git-menu-row">
        <span class="git-menu-gid">{{ p.group_id }}</span>
        <span class="badge" :class="statusBadgeClass(p.status)">{{ statusLabel(p.status) }}</span>
        <span class="git-menu-spacer"></span>
        <!-- conflict: send to the status panel, which now resolves inline -->
        <button
          v-if="p.status === 'conflict'"
          class="btn btn-sm btn-danger-ol"
          @click="openPanel"
        >
          <AppIcon name="warning" /> {{ t('main.git_status.resolve_inline') }}
        </button>
        <button
          v-else
          class="btn btn-sm btn-primary"
          :disabled="busy || groupBusy(p.group_id)"
          :title="groupBusy(p.group_id) ? busyHint : undefined"
          @click="execute(p)"
        >
          <AppIcon name="play" /> {{ t('main.git_finalize.execute') }}
        </button>
        <button class="btn btn-sm btn-secondary" @click="openGroup(p.group_id)">
          <AppIcon name="arrow-square-out" /> {{ t('main.git_status.open') }}
        </button>
      </div>
      <button class="git-menu-status-link" type="button" @click="openPanel">
        <AppIcon name="tree-structure" /> {{ t('main.git_menu.open_status') }}
        <AppIcon name="arrow-right" />
      </button>
    </div>

    <!-- "관제소" — the project Git status panel, reached from the safety-net menu. -->
    <teleport to="body">
      <div v-if="panelOpen" class="modal-bg">
        <div class="modal-box git-panel-modal">
          <div class="modal-hd">
            <span class="modal-title">
              <AppIcon name="tree-structure" style="color:var(--text-m);" />
              {{ t('main.git_status.title') }}
            </span>
            <button class="modal-close" type="button" @click="panelOpen = false">
              <AppIcon name="x" />
            </button>
          </div>
          <div class="modal-bd git-panel-modal-bd">
            <GitStatusPanel
              v-if="projectId"
              :project-id="projectId || ''"
              @open-group="openGroup"
            />
          </div>
        </div>
      </div>
    </teleport>

    <!-- 0177 0007-CH: base_dirty 409 → operator chooses commit / revert / cancel
         (no silent auto-commit) before the finalize retries. -->
    <GitBaseDirtyDialog ref="baseDirtyDialog" />
    <!-- 0350 T0004: base_untracked_conflict's sibling — commit / delete / cancel
         before the finalize retries. -->
    <GitUntrackedConflictDialog ref="untrackedConflictDialog" />
  </div>
</template>

<script setup lang="ts">
import AppIcon from '@shared/AppIcon.vue'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { postRequest } from '@shared/api'
import { useExplorerStore } from '../stores/explorer'
import { useProjectStore } from '../stores/project'
import { useTabsStore } from '../stores/tabs'
import { useAiInvokeRunsStore } from '../stores/aiInvokeRuns'
import { useToast } from './common/useToast'
import GitStatusPanel from './GitStatusPanel.vue'
import GitBaseDirtyDialog from './GitBaseDirtyDialog.vue'
import GitUntrackedConflictDialog from './GitUntrackedConflictDialog.vue'

const { t } = useI18n()
const { showToast } = useToast()
const explorerStore = useExplorerStore()
const projectStore = useProjectStore()
const tabsStore = useTabsStore()
const aiInvokeRunsStore = useAiInvokeRunsStore()
const busyHint = computed(() => t('main.review_action_bar.ai_running_hint'))
const groupBusy = (groupId: string) =>
  aiInvokeRunsStore.isGroupRunning(groupId)
  || aiInvokeRunsStore.isGroupInlineVisible(groupId)

interface Pending {
  group_id: string
  branch: string | null
  status: string
  default_action: string
  // 0182 NR0003 §4: the group's final-approval doc (pending implies wf_done) —
  // [open] targets it instead of the R root, which the git flow no longer needs.
  ac_doc_id?: string | null
}
interface GitStatus {
  enabled: boolean
  base_branch: string | null
  pending: Pending[]
  pending_count: number
}

const projectId = computed(() => projectStore.currentProjectId)
const status = ref<GitStatus | null>(null)
const busy = ref(false)
const dropdownOpen = ref(false)
const panelOpen = ref(false)
const baseDirtyDialog = ref<InstanceType<typeof GitBaseDirtyDialog> | null>(null)
const untrackedConflictDialog = ref<InstanceType<typeof GitUntrackedConflictDialog> | null>(null)

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
  if (!projectId.value) {
    status.value = null
    return
  }
  try {
    // 0282 NR0003 finding 3: shared store fetch — concurrent callers (explorer,
    // status panel, SSE listeners) coalesce onto one git/status request.
    status.value = (await explorerStore.fetchGitStatus(
      projectId.value,
    )) as unknown as GitStatus | null
  } catch {
    status.value = null // 403/404 — button stays hidden for non-git projects
  }
}

// Open a group from a pending row. 0182 NR0003 §4: every pending item's
// workflow is already final-approved, so [open] goes to the AC document —
// which hosts the git finalize panel since §3 — mirroring MainPanel's
// openFinalApprovalTab tab shape. Groups without a resolvable AC doc fall
// back to the R root (always 0001, reachable without extra plumbing).
function openGroup(groupId: string) {
  dropdownOpen.value = false
  panelOpen.value = false
  const acDocId = status.value?.pending.find((p) => p.group_id === groupId)?.ac_doc_id
  if (acDocId) {
    tabsStore.openTab({
      id: acDocId,
      title: `${acDocId} — ${t('main.review_action_bar.final_approval')}`,
      path: '',
      type: 'md',
      typeCode: 'AC',
      projectId: projectId.value,
    })
    return
  }
  tabsStore.openTab({
    id: `${groupId}.0001-R`,
    title: `${groupId}.0001-R`,
    path: '',
    type: 'md',
    typeCode: 'R',
    projectId: projectId.value,
  })
}

async function execute(item: Pending) {
  if (busy.value || groupBusy(item.group_id)) {
    if (groupBusy(item.group_id)) showToast(busyHint.value, 'danger')
    return
  }
  busy.value = true
  try {
    await runFinalize(item, false)
  } finally {
    busy.value = false
    await fetchStatus()
  }
}

async function runFinalize(item: Pending, retried: boolean): Promise<void> {
  try {
    const { data } = await postRequest<{ ok: boolean; result?: any; error?: any }>(
      `/api/v1/groups/${item.group_id}/git/finalize`,
      { action: item.default_action },
    )
    if (data.ok === false) {
      if (!retried && (await handleFinalizeConflict(data.error))) return runFinalize(item, true)
      showToast(data.error?.message || t('main.git_finalize.failed'), 'danger')
    } else {
      const r = data.result
      if (r?.status === 'conflict') {
        // Conflicts are now resolved inline in the status panel (0165 T0004).
        showToast(t('main.git_finalize.conflict_toast', { n: (r.conflict_files || []).length }), 'warning')
        openPanel()
      } else if (r?.status === 'merged') {
        showToast(t('main.git_finalize.merged_toast', { commit: r.merge_commit || '' }), 'success')
      } else if (r?.status === 'pushed') {
        showToast(t('main.git_finalize.pushed_toast'), 'success')
      } else if (r?.status === 'waiting') {
        showToast(t('main.git_finalize.waiting_toast'), 'success')
      }
    }
  } catch (e: any) {
    const err = e?.response?.data?.error
    if (!retried && (await handleFinalizeConflict(err))) return runFinalize(item, true)
    showToast(err?.message || t('main.git_finalize.failed'), 'danger')
  }
}

// 0177 0007-CH: the E3 base_dirty 409 is NOT auto-resolved — commit (keep) and
// revert (discard) are opposite outcomes only the operator may pick. Open the
// choice dialog; it commits or reverts the base checkout (and syncs the tree
// badges), then returns 'proceed' once the base is clean so [execute] retries
// the original finalize, or 'cancel' with no error toast.
async function handleBaseDirty(err: any): Promise<boolean> {
  if (err?.code !== 'base_dirty' || !projectId.value || !baseDirtyDialog.value) return false
  const files = Array.isArray(err.details?.files) ? err.details.files : []
  const outcome = await baseDirtyDialog.value.resolve(projectId.value, files)
  return outcome === 'proceed'
}

// 0350 T0004: base_untracked_conflict's sibling — same non-auto-resolve rule,
// commit or delete is the operator's call before [execute] retries.
async function handleUntrackedConflict(err: any): Promise<boolean> {
  if (err?.code !== 'base_untracked_conflict' || !projectId.value || !untrackedConflictDialog.value) return false
  const files = Array.isArray(err.details?.files) ? err.details.files : []
  const outcome = await untrackedConflictDialog.value.resolve(projectId.value, files)
  return outcome === 'proceed'
}

async function handleFinalizeConflict(err: any): Promise<boolean> {
  return (await handleBaseDirty(err)) || (await handleUntrackedConflict(err))
}

function toggleDropdown() {
  dropdownOpen.value = !dropdownOpen.value
}

function openPanel() {
  dropdownOpen.value = false
  panelOpen.value = true
}

function matchesProject(e: Event): boolean {
  const detail = (e as CustomEvent).detail || {}
  const eventProject = detail.project || detail.project_id
  return !eventProject || eventProject === projectId.value
}

// Live badge sync: the SSE bridge re-broadcasts git_pending_changed as a window
// event; local approval flows also dispatch deterministic refresh/open events.
function onPendingChanged(e: Event) {
  if (matchesProject(e)) fetchStatus()
}

function onStatusRefresh(e: Event) {
  if (matchesProject(e)) fetchStatus()
}

async function onStatusOpen(e: Event) {
  if (!matchesProject(e)) return
  await fetchStatus()
  openPanel()
}

function onOutsideClick() {
  if (dropdownOpen.value) dropdownOpen.value = false
}

onMounted(() => {
  fetchStatus()
  if (typeof window !== 'undefined') {
    window.addEventListener('fg:git_pending_changed', onPendingChanged)
    window.addEventListener('fg:git_status_refresh', onStatusRefresh)
    window.addEventListener('fg:git_status_open', onStatusOpen)
    window.addEventListener('click', onOutsideClick)
  }
})
onUnmounted(() => {
  if (typeof window !== 'undefined') {
    window.removeEventListener('fg:git_pending_changed', onPendingChanged)
    window.removeEventListener('fg:git_status_refresh', onStatusRefresh)
    window.removeEventListener('fg:git_status_open', onStatusOpen)
    window.removeEventListener('click', onOutsideClick)
  }
})

watch(projectId, fetchStatus)
</script>

<style scoped>
.git-menu-wrap {
  position: relative;
  display: inline-flex;
}
.git-menu-btn {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.git-menu-label {
  font-size: 0.78rem;
  font-weight: 600;
}
.git-menu-attn {
  color: var(--warning, #d97706);
}
.git-menu-badge {
  position: absolute;
  top: -4px;
  right: -6px;
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  font-size: 0.65rem;
  font-weight: 700;
  line-height: 16px;
  text-align: center;
  color: #fff;
  background: var(--warning, #d97706);
  border-radius: 999px;
}
.git-menu-dd {
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
  min-width: 320px;
  max-width: 420px;
  background: var(--bg-card, #fff);
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.14);
  z-index: 300;
  overflow: hidden;
}
.git-menu-dd-hd {
  padding: 10px 12px;
  font-size: 0.72rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  color: var(--text-m);
  border-bottom: 1px solid var(--border, #eef2f6);
}
.git-menu-empty {
  margin: 0;
  padding: 14px 12px;
  font-size: 0.8rem;
  color: var(--text-m);
}
.git-menu-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--border, #eef2f6);
}
.git-menu-gid {
  font-size: 0.78rem;
  font-family: var(--mono, ui-monospace, monospace);
}
.git-menu-spacer {
  flex: 1 1 auto;
}
.git-menu-status-link {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 10px 12px;
  background: none;
  border: none;
  border-top: 1px solid var(--border, #e2e8f0);
  font-size: 0.8125rem;
  font-weight: 600;
  color: var(--primary, #2563eb);
  cursor: pointer;
  text-align: left;
}
.git-menu-status-link:hover {
  background: var(--bg-hover, #f1f5f9);
}
.git-menu-status-link .app-icon {
  margin-left: auto;
  font-size: 0.7rem;
}
.btn-sm {
  padding: 3px 9px;
  font-size: 0.75rem;
}
.btn-danger-ol {
  background: #fff;
  color: #b91c1c;
  border: 1px solid #fca5a5;
}
.btn-danger-ol:hover {
  background: #fef2f2;
}
.badge-red {
  background: #fef2f2;
  color: #b91c1c;
}
.git-panel-modal {
  max-width: 620px;
  width: 100%;
}
.git-panel-modal-bd {
  max-height: 70vh;
  overflow-y: auto;
}
</style>
