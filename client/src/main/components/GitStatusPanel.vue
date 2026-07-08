<template>
  <!-- flowgate.default.0162 §2·§3 — project Git control panel ("관제소"). Renders
       only for git-integrated projects; hidden entirely otherwise.
       0165 T0004: the pending list now finalizes any group without opening its R
       document — per-row action selection (merge / push / wait) and inline
       conflict resolution live here, so the header panel is self-sufficient. -->
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
    <!-- flowgate.default.0176 T0010 §b — base_dirty finalize block, surfaced as a
         persistent actionable banner instead of a bare 500 in the console. -->
    <div v-if="baseDirtyError" class="git-base-dirty-alert" role="alert">
      <i class="fa-solid fa-triangle-exclamation"></i>
      <div class="git-base-dirty-alert__body">
        <div class="git-base-dirty-alert__msg">{{ t('main.git_finalize.base_dirty_alert') }}</div>
        <div v-if="baseDirtyError.files.length" class="git-base-dirty-alert__files">
          {{ t('main.git_finalize.base_dirty_files', { files: baseDirtyError.files.join(', ') }) }}
        </div>
      </div>
      <button
        class="git-base-dirty-alert__close"
        type="button"
        :title="t('common.close')"
        @click="baseDirtyError = null"
      >
        <i class="fa-solid fa-xmark"></i>
      </button>
    </div>
    <div class="card-bd pad">
      <!-- Finalize-pending list (each item finalizes inline, or opens the group) -->
      <div class="git-status-sect">
        <p class="git-status-sub">
          {{ t('main.git_status.pending_header') }} ({{ status.pending_count }})
        </p>
        <p v-if="!status.pending.length" class="git-status-empty">
          {{ t('main.git_status.no_pending') }}
        </p>
        <div v-for="p in status.pending" :key="p.group_id" class="git-status-row">
          <div class="git-status-row-main">
            <span class="git-status-gid">{{ p.group_id }}</span>
            <span class="badge" :class="statusBadgeClass(p.status)">{{ statusLabel(p.status) }}</span>
            <span class="git-status-spacer"></span>

            <!-- conflict: toggle the inline resolution editor (no R document) -->
            <button
              v-if="p.status === 'conflict'"
              class="btn btn-sm btn-danger-ol"
              :disabled="busy"
              @click="toggleResolve(p)"
            >
              <i class="fa-solid fa-triangle-exclamation"></i>
              {{ t('main.git_status.resolve_inline') }}
            </button>

            <!-- actionable: pick merge / push / wait, then run -->
            <template v-else>
              <label class="git-action-lbl">{{ t('main.git_status.action_label') }}</label>
              <select
                class="git-action-sel"
                :value="actionOf(p)"
                :disabled="busy"
                @change="setAction(p.group_id, ($event.target as HTMLSelectElement).value)"
              >
                <option v-for="c in ACTIONS" :key="c" :value="c">{{ actionLabel(c) }}</option>
              </select>
              <button class="btn btn-sm btn-primary" :disabled="busy" @click="execute(p)">
                <i class="fa-solid fa-play"></i> {{ t('main.git_finalize.execute') }}
              </button>
            </template>

            <button class="btn btn-sm btn-secondary" @click="emit('open-group', p.group_id)">
              <i class="fa-solid fa-arrow-up-right-from-square"></i> {{ t('main.git_status.open') }}
            </button>
          </div>

          <!-- Commit-subject confirmation for merge/push (0173 parity, B0001 F1): the
               header control panel now lets the user review/edit the absorb-commit
               subject without opening the R document. Blank = server auto-resolves. -->
          <div v-if="p.status !== 'conflict' && actionOf(p) !== 'wait'" class="git-status-commit">
            <div class="git-commit-msg-hd">
              <label class="git-commit-msg-label" :for="`gsc-${p.group_id}`">
                {{ t('main.git_finalize.commit_message_label') }}
              </label>
              <span v-if="commitSourceLabel(p.group_id)" class="badge git-commit-src-badge">
                {{ commitSourceLabel(p.group_id) }}
              </span>
            </div>
            <input
              :id="`gsc-${p.group_id}`"
              class="form-ctrl git-commit-msg-input"
              type="text"
              :value="commitDrafts[p.group_id]?.message || ''"
              :placeholder="commitDrafts[p.group_id]?.suggested || ''"
              maxlength="200"
              @input="setCommitMsg(p.group_id, ($event.target as HTMLInputElement).value)"
            />
            <p class="git-commit-msg-hint">
              {{ t('main.git_finalize.commit_message_hint') }}
              <a
                v-if="commitBlank(p.group_id) && commitDrafts[p.group_id]?.suggested"
                href="#"
                @click.prevent="restoreCommit(p.group_id)"
              >{{ t('main.git_finalize.commit_message_restore') }}</a>
            </p>
          </div>

          <!-- Inline conflict resolution editor (ported from GitFinalizePanel, P0005 §6).
               Opens only for the expanded conflict row; edits the merged files and
               submits resolve/abort against the same backend endpoints. -->
          <div v-if="expanded === p.group_id && p.status === 'conflict'" class="git-status-conflict">
            <p class="git-fin-conflict-msg">
              <i class="fa-solid fa-triangle-exclamation"></i>
              {{ t('main.git_finalize.conflict_msg', { n: conflictFiles.length }) }}
            </p>
            <p v-if="!conflictFiles.length" class="git-status-empty">
              {{ t('main.git_finalize.conflict_count', { n: 0 }) }}
            </p>
            <div v-for="f in conflictFiles" :key="f.path" class="git-conflict-file">
              <div class="git-conflict-path">
                <i class="fa-solid fa-file-code"></i> {{ f.path }}
                <span class="git-conflict-count">{{ t('main.git_finalize.conflict_count', { n: f.conflict_count }) }}</span>
              </div>
              <textarea
                v-model="f.edited"
                class="git-conflict-editor"
                spellcheck="false"
                rows="12"
              ></textarea>
            </div>
            <p v-if="conflictError" class="git-fin-conflict-msg">{{ conflictError }}</p>
            <!-- Residual-marker guard (B0001 F2): the document-path panel gates submit
                 on markers being gone; mirror it here so the header resolver does not
                 post <<<<<<< />>>>>>>> content only to bounce off the backend 422. -->
            <p v-if="conflictFiles.length && !inlineResolved" class="git-status-marker-hint">
              <i class="fa-solid fa-triangle-exclamation"></i> {{ t('main.git_finalize.submit_disabled_hint') }}
            </p>
            <div class="flex" style="justify-content:flex-end; gap:10px; margin-top:8px;">
              <button class="btn btn-sm btn-secondary" :disabled="busy" @click="abortInline(p)">
                <i class="fa-solid fa-ban"></i> {{ t('main.git_finalize.abort') }}
              </button>
              <button
                class="btn btn-sm btn-primary"
                :disabled="busy || !conflictFiles.length || !inlineResolved"
                @click="submitResolveInline(p)"
              >
                <i class="fa-solid fa-check"></i> {{ t('main.git_finalize.resolve_submit') }}
              </button>
            </div>
          </div>
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

// Fixed finalize actions (git_service.ACTION_VALUES). Kept as an array literal so
// the i18n static-reference scanner sees the backtick keys, not a computed one.
const ACTIONS = ['merge', 'push', 'wait'] as const

// Conflict-marker detection for the inline resolve guard (B0001 F2), mirroring
// GitFinalizePanel: a bare "=======" is not counted (doubles as a Markdown rule).
const MARKER_OPEN_RE = /^<{7}( |$)/
const MARKER_CLOSE_RE = /^>{7}( |$)/
function hasConflictMarkers(text: string): boolean {
  return text
    .split(/\r\n|\n|\r/)
    .some((l) => MARKER_OPEN_RE.test(l) || MARKER_CLOSE_RE.test(l))
}

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
  merge_id: number | null
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
// flowgate.default.0176 T0010 §b: set when a merge finalize is blocked by the E3
// base_dirty guard. Rendered as a persistent, actionable banner (which files are
// dirty + what to do) instead of a 3s toast that reads as an "unknown error".
const baseDirtyError = ref<{ files: string[] } | null>(null)

// Per-row chosen action (overrides default_action); keyed by group_id.
const chosen = ref<Record<string, string>>({})
// Currently expanded conflict row + its fetched files.
const expanded = ref<string | null>(null)
const conflictFiles = ref<Array<{ path: string; conflict_count: number; edited: string }>>([])
const conflictError = ref('')

// Per-group commit-subject draft (B0001 F1). Lazily hydrated from the group's
// finalize state (state.commit_message) the first time its row shows merge/push;
// `message` is what we POST (blank → omitted so the server auto-resolves).
interface CommitDraft {
  message: string
  suggested: string
  source: string | null
  loading: boolean
  loaded: boolean
}
const commitDrafts = ref<Record<string, CommitDraft>>({})

const inlineResolved = computed(
  () => conflictFiles.value.length > 0 && conflictFiles.value.every((f) => !hasConflictMarkers(f.edited)),
)

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
function actionLabel(c: string): string {
  return t(`main.git_finalize.action.${c}`)
}
function actionOf(p: Pending): string {
  return chosen.value[p.group_id] || p.default_action || 'wait'
}
function setAction(groupId: string, value: string) {
  chosen.value = { ...chosen.value, [groupId]: value }
  if (value !== 'wait') ensureCommitDraft(groupId)
}

// ── Commit-subject draft (B0001 F1) ───────────────────────────────────────────

function setDraft(groupId: string, patch: Partial<CommitDraft>) {
  const cur = commitDrafts.value[groupId] || {
    message: '', suggested: '', source: null, loading: false, loaded: false,
  }
  commitDrafts.value = { ...commitDrafts.value, [groupId]: { ...cur, ...patch } }
}
function setCommitMsg(groupId: string, value: string) {
  setDraft(groupId, { message: value })
}
function restoreCommit(groupId: string) {
  setDraft(groupId, { message: commitDrafts.value[groupId]?.suggested || '' })
}
function commitBlank(groupId: string): boolean {
  return !(commitDrafts.value[groupId]?.message || '').trim()
}
function commitSourceLabel(groupId: string): string {
  const s = commitDrafts.value[groupId]?.source
  return s ? t(`main.git_finalize.commit_source.${s}`) : ''
}
async function ensureCommitDraft(groupId: string) {
  const cur = commitDrafts.value[groupId]
  if (cur && (cur.loaded || cur.loading)) return
  setDraft(groupId, { loading: true })
  try {
    const { data } = await getRequest<{
      ok: boolean
      state: { commit_message?: { suggested: string; source: string } | null }
    }>(`/api/v1/groups/${groupId}/git/finalize`)
    const cm = data.state?.commit_message
    setDraft(groupId, {
      message: cm?.suggested || '',
      suggested: cm?.suggested || '',
      source: cm?.source || null,
      loading: false,
      loaded: true,
    })
  } catch {
    // Suggestion unavailable — keep an empty draft; blank simply omits the field
    // on submit and the server resolves the subject itself.
    setDraft(groupId, { loading: false, loaded: true })
  }
}
// Hydrate drafts for every actionable merge/push row (once each; guarded by flags).
function syncCommitDrafts() {
  for (const p of status.value?.pending || []) {
    if (p.status !== 'conflict' && actionOf(p) !== 'wait') ensureCommitDraft(p.group_id)
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
    // Drop an expanded conflict editor whose row no longer reports a conflict.
    if (expanded.value) {
      const still = data.status.pending.find(
        (p) => p.group_id === expanded.value && p.status === 'conflict',
      )
      if (!still) collapseResolve()
    }
    syncCommitDrafts()
  } catch {
    status.value = null // 403/404 — panel stays hidden
  }
}

async function execute(item: Pending) {
  if (busy.value) return
  busy.value = true
  try {
    const action = actionOf(item)
    // Attach the confirmed commit subject for merge/push (B0001 F1). Blank →
    // omit the field so git_service resolves the subject on the unmanned path.
    const payload: { action: string; commit_message?: string } = { action }
    if (action !== 'wait') {
      const msg = (commitDrafts.value[item.group_id]?.message || '').trim()
      if (msg) payload.commit_message = msg
    }
    const { data } = await postRequest<{ ok: boolean; result?: any; error?: any }>(
      `/api/v1/groups/${item.group_id}/git/finalize`,
      payload,
    )
    if (data.ok === false) {
      if (!handleBaseDirty(data.error)) {
        showToast(data.error?.message || t('main.git_finalize.failed'), 'danger')
      }
    } else {
      baseDirtyError.value = null // a finalize got through — base is clean now
      const r = data.result
      if (r?.status === 'conflict') {
        showToast(t('main.git_finalize.conflict_toast', { n: (r.conflict_files || []).length }), 'warning')
        // Resolve right here instead of routing to the R document.
        await fetchStatus()
        await openResolve(item.group_id)
        return
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
    if (!handleBaseDirty(err)) {
      showToast(err?.message || t('main.git_finalize.failed'), 'danger')
    }
  } finally {
    busy.value = false
    await fetchStatus()
  }
}

// flowgate.default.0176 T0010 §b: the E3 base_dirty guard (HTTP 500 + structured
// body {code:'base_dirty', details:{files:[...]}}) is not a crash — it means the
// project's base checkout has uncommitted edits (typically from the file editor)
// that block merge finalize for EVERY group. Surface it as a persistent,
// actionable banner naming the files. Returns true when it handled the error.
function handleBaseDirty(err: any): boolean {
  if (err?.code !== 'base_dirty') return false
  const files = err.details?.files
  baseDirtyError.value = { files: Array.isArray(files) ? files : [] }
  showToast(t('main.git_finalize.base_dirty_toast'), 'danger')
  return true
}

// ── Inline conflict resolution (endpoints already exist, P0005 §6) ────────────

function collapseResolve() {
  expanded.value = null
  conflictFiles.value = []
  conflictError.value = ''
}

async function toggleResolve(p: Pending) {
  if (expanded.value === p.group_id) {
    collapseResolve()
    return
  }
  await openResolve(p.group_id)
}

async function openResolve(groupId: string) {
  const p = status.value?.pending.find((x) => x.group_id === groupId)
  if (!p || p.merge_id == null) {
    // No merge session id — fall back to the full finalize panel.
    emit('open-group', groupId)
    return
  }
  expanded.value = groupId
  conflictError.value = ''
  conflictFiles.value = []
  try {
    const { data } = await getRequest<{
      ok: boolean
      files: Array<{ path: string; content: string; conflict_count: number }>
    }>(`/api/v1/groups/${groupId}/git/merge/${p.merge_id}/conflicts`)
    conflictFiles.value = (data.files || []).map((f) => ({
      path: f.path,
      conflict_count: f.conflict_count,
      edited: f.content,
    }))
  } catch (e: any) {
    conflictError.value = e?.response?.data?.error?.message || t('main.git_finalize.failed')
  }
}

async function submitResolveInline(p: Pending) {
  if (p.merge_id == null || busy.value) return
  busy.value = true
  conflictError.value = ''
  try {
    const { data } = await postRequest<{ ok: boolean; result?: any; error?: any }>(
      `/api/v1/groups/${p.group_id}/git/merge/${p.merge_id}/resolve`,
      {
        files: conflictFiles.value.map((f) => ({ path: f.path, content: f.edited })),
        complete: true,
      },
    )
    if (data.ok === false) {
      conflictError.value = data.error?.message || t('main.git_finalize.failed')
    } else if (data.result?.status === 'merged') {
      showToast(t('main.git_finalize.merged_toast', { commit: data.result.merge_commit || '' }), 'success')
      collapseResolve()
    }
  } catch (e: any) {
    conflictError.value = e?.response?.data?.error?.message || t('main.git_finalize.failed')
  } finally {
    busy.value = false
    await fetchStatus()
  }
}

async function abortInline(p: Pending) {
  if (p.merge_id == null || busy.value) return
  busy.value = true
  try {
    await postRequest(`/api/v1/groups/${p.group_id}/git/merge/${p.merge_id}/abort`, {})
    showToast(t('main.git_finalize.aborted_toast'), 'success')
    collapseResolve()
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

function matchesProject(e: Event): boolean {
  const detail = (e as CustomEvent).detail || {}
  const eventProject = detail.project || detail.project_id
  return !eventProject || eventProject === props.projectId
}

// Live badge/list sync: the SSE bridge re-broadcasts git_pending_changed as a
// window event carrying the server-recomputed pending_count (L §2.3). Local approval
// flows also dispatch deterministic refresh/open events.
function onPendingChanged(e: Event) {
  if (matchesProject(e)) fetchStatus()
}

function onStatusRefresh(e: Event) {
  if (matchesProject(e)) fetchStatus()
}

onMounted(() => {
  fetchStatus()
  if (typeof window !== 'undefined') {
    window.addEventListener('fg:git_pending_changed', onPendingChanged)
    window.addEventListener('fg:git_status_refresh', onStatusRefresh)
    window.addEventListener('fg:git_status_open', onStatusRefresh)
  }
})
onUnmounted(() => {
  if (typeof window !== 'undefined') {
    window.removeEventListener('fg:git_pending_changed', onPendingChanged)
    window.removeEventListener('fg:git_status_refresh', onStatusRefresh)
    window.removeEventListener('fg:git_status_open', onStatusRefresh)
  }
})

watch(() => props.projectId, () => {
  collapseResolve()
  fetchStatus()
})

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
  padding: 6px 0;
  border-bottom: 1px solid var(--border, #eef2f6);
}
.git-status-row-main {
  display: flex;
  align-items: center;
  gap: 8px;
}
.git-status-gid {
  font-size: 0.8rem;
  font-family: var(--mono, ui-monospace, monospace);
}
.git-status-spacer {
  flex: 1 1 auto;
}
.git-action-lbl {
  font-size: 0.72rem;
  color: var(--text-m);
}
.git-action-sel {
  font-size: 0.75rem;
  padding: 3px 6px;
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 6px;
  background: var(--bg, #fff);
  color: var(--text, #0f172a);
}
.git-status-conflict {
  margin: 8px 0 4px;
  padding: 10px;
  border: 1px solid #fecaca;
  border-radius: var(--r, 8px);
  background: #fef2f2;
}
.git-fin-conflict-msg {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 0.8rem;
  color: #b91c1c;
  margin: 0 0 8px;
}
.git-conflict-file {
  margin-bottom: 10px;
}
.git-conflict-path {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.78rem;
  font-family: var(--mono, ui-monospace, monospace);
  margin-bottom: 4px;
}
.git-conflict-count {
  font-size: 0.7rem;
  color: var(--text-m);
}
.git-conflict-editor {
  width: 100%;
  font-family: var(--mono, ui-monospace, monospace);
  font-size: 0.75rem;
  line-height: 1.45;
  border: 1px solid var(--border, #e2e8f0);
  border-radius: var(--r, 8px);
  padding: 8px;
  background: var(--bg, #fff);
  color: var(--text, #0f172a);
  resize: vertical;
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
.git-status-commit {
  margin: 6px 0 2px;
  padding-left: 2px;
}
.git-commit-msg-hd {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}
.git-commit-msg-label {
  font-size: 0.72rem;
  font-weight: 700;
  color: var(--text-m);
}
.git-commit-src-badge {
  background: #eff6ff;
  color: #1d4ed8;
  border: 1px solid #bfdbfe;
  font-size: 0.66rem;
}
.git-commit-msg-input {
  width: 100%;
  font-family: var(--mono, ui-monospace, monospace);
  font-size: 0.76rem;
  padding: 4px 7px;
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 6px;
  background: var(--bg, #fff);
  color: var(--text, #0f172a);
}
.git-commit-msg-hint {
  font-size: 0.7rem;
  color: var(--text-m);
  margin: 4px 0 0;
}
.git-status-marker-hint {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.74rem;
  color: #b45309;
  margin: 4px 0 0;
}

/* flowgate.default.0176 T0010 §b — base_dirty finalize-block banner. */
.git-base-dirty-alert {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin: 0 16px 4px;
  padding: 10px 12px;
  border: 1px solid var(--danger, #dc2626);
  border-radius: 8px;
  background: color-mix(in srgb, var(--danger, #dc2626) 10%, transparent);
  font-size: 0.8rem;
  line-height: 1.45;
}
.git-base-dirty-alert > i {
  color: var(--danger, #dc2626);
  margin-top: 2px;
  flex: none;
}
.git-base-dirty-alert__body {
  flex: 1 1 auto;
  min-width: 0;
}
.git-base-dirty-alert__msg {
  color: var(--text, inherit);
}
.git-base-dirty-alert__files {
  margin-top: 4px;
  font-family: var(--font-mono, monospace);
  font-size: 0.74rem;
  color: var(--text-m);
  word-break: break-all;
}
.git-base-dirty-alert__close {
  flex: none;
  background: none;
  border: none;
  cursor: pointer;
  color: var(--text-m);
  padding: 0 2px;
  line-height: 1;
}
.git-base-dirty-alert__close:hover {
  color: var(--text);
}
</style>
