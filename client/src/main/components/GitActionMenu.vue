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
      <i class="fa-solid fa-code-branch"></i>
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
          <i class="fa-solid fa-triangle-exclamation"></i> {{ t('main.git_status.resolve_inline') }}
        </button>
        <button
          v-else
          class="btn btn-sm btn-primary"
          :disabled="busy"
          @click="execute(p)"
        >
          <i class="fa-solid fa-play"></i> {{ t('main.git_finalize.execute') }}
        </button>
        <button class="btn btn-sm btn-secondary" @click="openGroup(p.group_id)">
          <i class="fa-solid fa-arrow-up-right-from-square"></i> {{ t('main.git_status.open') }}
        </button>
      </div>
      <button class="git-menu-status-link" type="button" @click="openPanel">
        <i class="fa-solid fa-diagram-project"></i> {{ t('main.git_menu.open_status') }}
        <i class="fa-solid fa-arrow-right"></i>
      </button>
    </div>

    <!-- "관제소" — the project Git status panel, reached from the safety-net menu. -->
    <teleport to="body">
      <div v-if="panelOpen" class="modal-bg" @click.self="panelOpen = false">
        <div class="modal-box git-panel-modal">
          <div class="modal-hd">
            <span class="modal-title">
              <i class="fa-solid fa-diagram-project" style="color:var(--text-m);"></i>
              {{ t('main.git_status.title') }}
            </span>
            <button class="modal-close" type="button" @click="panelOpen = false">
              <i class="fa-solid fa-xmark"></i>
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
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getRequest, postRequest } from '@shared/api'
import { useExplorerStore } from '../stores/explorer'
import { useProjectStore } from '../stores/project'
import { useTabsStore } from '../stores/tabs'
import { useToast } from './common/useToast'
import GitStatusPanel from './GitStatusPanel.vue'

const { t } = useI18n()
const { showToast } = useToast()
const explorerStore = useExplorerStore()
const projectStore = useProjectStore()
const tabsStore = useTabsStore()

interface Pending {
  group_id: string
  branch: string | null
  status: string
  default_action: string
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
    const { data } = await getRequest<{ ok: boolean; status: GitStatus }>(
      `/api/v1/projects/${projectId.value}/git/status`,
    )
    status.value = data.status
  } catch {
    status.value = null // 403/404 — button stays hidden for non-git projects
  }
}

// Open a group by its workflow-root (R) document. R is always 0001 in a group,
// so this reaches the group from any screen without extra plumbing; opening the
// tab through the shared store makes MainPanel render it (§3.3 "jump to group").
function openGroup(groupId: string) {
  dropdownOpen.value = false
  panelOpen.value = false
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
  if (busy.value) return
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
      if (!retried && (await autoCommitBaseDirty(data.error))) return runFinalize(item, true)
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
    if (!retried && (await autoCommitBaseDirty(err))) return runFinalize(item, true)
    showToast(err?.message || t('main.git_finalize.failed'), 'danger')
  }
}

// 0177 follow-up (0007-CH): [execute] must clear the E3 base_dirty 409 itself
// instead of dead-ending in a toast — auto-commit the base checkout (blank
// message → the server derives the §2.2 default) and retry the finalize once.
// Returns true only when the base came out clean; on any failure the caller
// falls through to the ordinary error toast.
async function autoCommitBaseDirty(err: any): Promise<boolean> {
  if (err?.code !== 'base_dirty' || !projectId.value) return false
  showToast(t('main.git_finalize.base_dirty_auto'), 'info')
  try {
    const { data } = await postRequest<{ ok: boolean; result?: any; error?: any }>(
      `/api/v1/projects/${projectId.value}/git/base-commit`,
      {},
    )
    if (data.ok === false) return false
    const remaining: string[] = Array.isArray(data.result?.remaining) ? data.result.remaining : []
    explorerStore.setBaseDirtyFiles(projectId.value, remaining)
    return remaining.length === 0
  } catch {
    return false
  }
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
  color: var(--danger, #dc2626);
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
  background: var(--danger, #dc2626);
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
.git-menu-status-link .fa-arrow-right {
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
