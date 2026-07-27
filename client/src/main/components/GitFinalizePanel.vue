<template>
  <!-- 0115 R0001-4: git finalize panel - visible only for git-integrated groups
       whose worktree exists (status !== 'none'); everyone else sees nothing. -->
  <div v-if="state && state.status !== 'none'" class="card git-fin-card">
    <div class="card-hd">
      <span class="card-title">
        <AppIcon name="git-branch" style="color:var(--text-m);" />
        {{ t('main.git_finalize.title') }}
      </span>
      <span class="git-branch-badge">
        <AppIcon name="git-commit" /> {{ state.branch }}
      </span>
      <span class="badge" :class="statusBadgeClass">{{ statusLabel }}</span>
      <button class="git-refresh-btn" :disabled="busy" @click="fetchState" :title="t('main.git_finalize.refresh')">
        <AppIcon name="arrows-clockwise" />
      </button>
    </div>
    <div class="card-bd pad">
      <p v-if="aheadBehindText" class="git-fin-meta">{{ aheadBehindText }}</p>

      <template v-if="state.status === 'awaiting_choice' || state.status === 'waiting'">
        <!-- 0331 T0006: the approved v4 layout — two axes instead of a card list.
             `action_axes` is additive, so a server that predates it still gets
             the original cards below. -->
        <GitFinalizeAxis
          v-if="state.action_axes"
          v-model="chosen"
          :axes="state.action_axes"
          name="git-fin"
          :disabled="busy"
        />
        <template v-else>
          <div class="git-choice-row">
            <label v-for="c in state.choices" :key="c" class="git-choice" :class="{ sel: chosen === c }">
              <input type="radio" name="git-fin-action" :value="c" v-model="chosen" />
              <span class="git-choice-label">{{ actionLabel(c) }}</span>
              <span class="git-choice-desc">{{ actionDesc(c) }}</span>
            </label>
          </div>
          <div v-if="state.aux_choices?.length" class="git-aux">
            <button class="git-aux-toggle" type="button" @click="auxOpen = !auxOpen">
              <AppIcon :name="auxOpen ? 'caret-down' : 'caret-right'" />
              {{ t('main.git_finalize.aux_toggle') }}
            </button>
            <div v-if="auxOpen" class="git-choice-row git-choice-row--aux">
              <label v-for="c in state.aux_choices" :key="c" class="git-choice" :class="{ sel: chosen === c }">
                <input type="radio" name="git-fin-action" :value="c" v-model="chosen" />
                <span class="git-choice-label">{{ actionLabel(c) }}</span>
                <span class="git-choice-desc">{{ actionDesc(c) }}</span>
              </label>
            </div>
          </div>
        </template>
        <div v-if="showCommitInput" class="git-commit-msg">
          <div class="git-commit-msg-hd">
            <label class="git-commit-msg-label" for="git-commit-subject">
              {{ t('main.git_finalize.commit_message_label') }}
            </label>
            <span v-if="commitSourceLabel" class="badge git-commit-src-badge">{{ commitSourceLabel }}</span>
          </div>
          <input
            id="git-commit-subject"
            class="form-ctrl git-commit-msg-input"
            type="text"
            v-model="commitMessage"
            :placeholder="commitSuggested"
            maxlength="200"
          />
          <p class="git-commit-msg-hint">
            {{ t('main.git_finalize.commit_message_hint') }}
            <a v-if="commitMessageBlank && commitSuggested" href="#" @click.prevent="restoreSuggested">
              {{ t('main.git_finalize.commit_message_restore') }}
            </a>
          </p>
        </div>
        <div class="flex" style="justify-content:flex-end; margin-top:10px;">
          <button class="btn btn-primary" :disabled="runDisabled" @click="runFinalize">
            <AppIcon name="play" />
            {{ busy ? t('main.git_finalize.running') : t('main.git_finalize.execute') }}
          </button>
        </div>
      </template>

      <template v-else-if="state.status === 'merged'">
        <p class="git-fin-done">
          <AppIcon name="check-circle" />
          {{ t('main.git_finalize.merged_msg', { base: state.base_branch, commit: mergeCommit || '-' }) }}
        </p>
      </template>
      <template v-else-if="state.status === 'pushed'">
        <p class="git-fin-done">
          <AppIcon name="check-circle" />
          {{ t('main.git_finalize.pushed_msg', { branch: state.branch }) }}
        </p>
      </template>
      <template v-else-if="state.status === 'merging'">
        <p class="git-fin-meta"><AppIcon name="spinner" spin /> {{ t('main.git_finalize.merging_msg') }}</p>
      </template>

      <template v-else-if="state.status === 'conflict'">
        <p class="git-fin-conflict-msg">
          <AppIcon name="warning" />
          {{ t('main.git_finalize.conflict_msg', { n: conflictFiles.length }) }}
        </p>
        <p v-if="conflictError" class="git-fin-conflict-msg">{{ conflictError }}</p>
        <div class="git-conflict-summary">
          <span>
            <AppIcon name="file-code" />
            {{ t('main.git_finalize.conflict_files_summary', { resolved: resolvedFileCount, total: conflictFiles.length }) }}
          </span>
          <span v-if="firstResidualMarker" class="git-marker-warning">{{ firstResidualMarker }}</span>
        </div>
        <div class="flex" style="justify-content:flex-end; gap:10px; margin-top:10px;">
          <button class="btn btn-secondary" :disabled="busy" @click="abortMerge">
            <AppIcon name="prohibit" /> {{ t('main.git_finalize.abort') }}
          </button>
          <button class="btn btn-primary" :disabled="busy" @click="openConflictDialog">
            <AppIcon name="git-diff" /> {{ t('main.git_finalize.open_resolver') }}
          </button>
        </div>
      </template>
    </div>
  </div>

  <!-- 0212 T0009: the resolver dialog is the shared 1180×820 component (0207
       시안 A) also used by the header GitStatusPanel — one resolver everywhere. -->
  <GitConflictResolverDialog
    v-if="conflictDialogOpen"
    :files="conflictFiles"
    :branch="state?.branch || null"
    :base-branch="state?.base_branch || null"
    :busy="busy"
    :load-status="conflictLoadStatus"
    :error-message="conflictError"
    :providers="aiProviderStore.providers"
    :selected-provider="aiProviderStore.selectedProviderId"
    :provider-loading="aiProviderStore.loading"
    :provider-errored="!!aiProviderStore.error"
    @close="closeConflictDialog"
    @abort="abortMerge"
    @submit="submitResolve"
    @retry="retryFetchConflicts"
    @ai-invoke="invokeConflictAi"
    @copy-mention="copyConflictMention"
    @update:provider="aiProviderStore.selectProvider"
  />

  <!-- 0177 0007-CH: base_dirty 409 → operator chooses commit / revert / cancel
       (no silent auto-commit) before the finalize retries. -->
  <GitBaseDirtyDialog ref="baseDirtyDialog" />
</template>

<script setup lang="ts">
import AppIcon from '@shared/AppIcon.vue'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getRequest, postRequest } from '@shared/api'
import { useProjectStore } from '../stores/project'
import { useAiProviderStore } from '../stores/aiProvider'
import { useToast } from './common/useToast'
// 0182 NR0003 §6: the chunk parser/assembler state machine lives in a shared
// composable; 0212 T0009 moved the resolver dialog itself into
// GitConflictResolverDialog so this panel only fetches/submits.
import {
  useConflictChunks,
  currentFileContent,
  isFileResolved,
  residualMarkers,
  type ConflictFileState,
} from '../composables/useConflictChunks'
import GitConflictResolverDialog from './GitConflictResolverDialog.vue'
import GitBaseDirtyDialog from './GitBaseDirtyDialog.vue'
import GitFinalizeAxis from './GitFinalizeAxis.vue'
import { actionNeedsCommitMessage, type FinalizeAxes } from '../composables/finalizeAxis'

const props = defineProps<{ groupId: string }>()

const { t } = useI18n()
const { showToast } = useToast()
const projectStore = useProjectStore()
// 0234 B0001: single source of truth for the runtime provider (shared with AppHeader),
// so the conflict AI run started here honours the selection.
const aiProviderStore = useAiProviderStore()
const { initConflictFile } = useConflictChunks()
const baseDirtyDialog = ref<InstanceType<typeof GitBaseDirtyDialog> | null>(null)

interface GitCommitMessage {
  suggested: string
  source: string
}

interface GitFinState {
  group_id: string
  branch: string | null
  base_branch: string | null
  status: string
  default_action: string | null
  choices: string[]
  aux_choices?: string[]
  // 0331: additive axis contract; absent on a pre-0331 server.
  action_axes?: FinalizeAxes | null
  ahead_count: number | null
  behind_count: number | null
  merge_id: number | null
  merge_commit?: string | null
  commit_message?: GitCommitMessage | null
}

const state = ref<GitFinState | null>(null)
const chosen = ref<string>('')
const busy = ref(false)
const commitMessage = ref('')
const commitSuggested = ref('')
const commitSource = ref<string | null>(null)
const mergeCommit = ref<string | null>(null)
const conflictFiles = ref<ConflictFileState[]>([])
const conflictError = ref('')
const conflictDialogOpen = ref(false)
const conflictLoadStatus = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
const auxOpen = ref(false)

const statusLabel = computed(() =>
  state.value ? t(`main.git_finalize.status.${state.value.status}`) : '',
)
const statusBadgeClass = computed(() => {
  switch (state.value?.status) {
    case 'merged':
    case 'pushed':
      return 'badge-blue'
    case 'conflict':
      return 'badge-red'
    default:
      return 'badge-yellow'
  }
})
const aheadBehindText = computed(() => {
  const s = state.value
  if (!s || s.ahead_count == null || s.behind_count == null) return ''
  return t('main.git_finalize.ahead_behind', { ahead: s.ahead_count, behind: s.behind_count })
})
// 0331: with the axis contract the server tells us which actions commit, and
// `push` is no longer one of them (it now 409s on a dirty worktree instead of
// absorbing it). A pre-0331 server has no action_axes — keep the old list there,
// where `push` really did absorb.
const LEGACY_COMMIT_ACTIONS = ['merge', 'merge_only', 'push']
const showCommitInput = computed(() =>
  state.value?.action_axes
    ? actionNeedsCommitMessage(state.value.action_axes, chosen.value)
    : LEGACY_COMMIT_ACTIONS.includes(chosen.value),
)
const commitMessageBlank = computed(() => !commitMessage.value.trim())
const commitSourceLabel = computed(() =>
  commitSource.value ? t(`main.git_finalize.commit_source.${commitSource.value}`) : '',
)
const runDisabled = computed(
  () => busy.value || !chosen.value || (showCommitInput.value && commitMessageBlank.value),
)
function restoreSuggested() {
  commitMessage.value = commitSuggested.value
}
const resolvedFileCount = computed(() => conflictFiles.value.filter(isFileResolved).length)
const allConflictsResolved = computed(
  () => conflictFiles.value.length > 0 && conflictFiles.value.every(isFileResolved),
)
const firstResidualMarker = computed(() => {
  for (const file of conflictFiles.value) {
    const markers = residualMarkers(currentFileContent(file))
    if (markers.length) {
      return t('main.git_finalize.marker_summary_item', {
        path: file.path,
        lines: markers.join(', '),
      })
    }
  }
  return ''
})

function actionLabel(c: string): string {
  return t(`main.git_finalize.action.${c}`)
}
function actionDesc(c: string): string {
  return t(`main.git_finalize.action_desc.${c}`)
}

async function fetchState() {
  if (!props.groupId) {
    state.value = null
    return
  }
  try {
    const { data } = await getRequest<{ ok: boolean; state: GitFinState }>(
      `/api/v1/groups/${props.groupId}/git/finalize`,
    )
    state.value = data.state
    chosen.value = data.state.default_action || 'wait'
    auxOpen.value = !!data.state.aux_choices?.includes(chosen.value)
    const cm = data.state.commit_message
    commitSuggested.value = cm?.suggested || ''
    commitSource.value = cm?.source || null
    commitMessage.value = cm?.suggested || ''
    mergeCommit.value = data.state.merge_commit || null
    if (data.state.status === 'conflict' && data.state.merge_id != null) {
      await fetchConflicts(data.state.merge_id)
    } else {
      conflictFiles.value = []
      conflictDialogOpen.value = false
    }
  } catch {
    state.value = null
  }
}

async function fetchConflicts(mergeId: number) {
  conflictError.value = ''
  conflictLoadStatus.value = 'loading'
  try {
    const { data } = await getRequest<{
      ok: boolean
      files: Array<{ path: string; content: string; conflict_count: number }>
    }>(`/api/v1/groups/${props.groupId}/git/merge/${mergeId}/conflicts`)
    conflictFiles.value = (data.files || []).map(initConflictFile)
    conflictLoadStatus.value = 'ready'
  } catch (e: any) {
    conflictFiles.value = []
    conflictError.value = e?.response?.data?.error?.message || t('main.git_finalize.load_failed')
    conflictLoadStatus.value = 'error'
  }
}
async function retryFetchConflicts() {
  const mergeId = state.value?.merge_id
  if (mergeId == null) return
  await fetchConflicts(mergeId)
}
async function openConflictDialog() {
  conflictDialogOpen.value = true
  // Populate the provider selector shown in the resolver footer (RC2).
  void aiProviderStore.ensureLoaded(providerProject.value)
  const mergeId = state.value?.merge_id
  if (mergeId != null && (!conflictFiles.value.length || conflictLoadStatus.value === 'error')) {
    await fetchConflicts(mergeId)
  }
}
function closeConflictDialog() {
  conflictDialogOpen.value = false
}

function groupParts(groupId: string) {
  const [project, module = 'none', ...rest] = groupId.split('.')
  return { project, module, group: rest.join('.') }
}

// Project id used to load the runtime provider list. The group id's first segment is the
// project; fall back to the active project store when the group id is not yet set.
const providerProject = computed(() => groupParts(props.groupId).project || projectStore.currentProjectId || '')

async function copyToClipboard(text: string) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text)
    return
  }
  const ta = document.createElement('textarea')
  ta.value = text
  ta.style.position = 'fixed'
  ta.style.left = '-9999px'
  document.body.appendChild(ta)
  ta.focus()
  ta.select()
  document.execCommand('copy')
  document.body.removeChild(ta)
}

async function invokeConflictAi() {
  const mergeId = state.value?.merge_id
  if (!props.groupId || mergeId == null || busy.value) return
  busy.value = true
  try {
    // RC1: forward the current provider selection so the run honours it instead of
    // silently falling back to the server default chain.
    await aiProviderStore.ensureLoaded(providerProject.value)
    const body: Record<string, unknown> = {
      ...groupParts(props.groupId),
      action_scope: 'resolve_conflict',
      mode: 'single',
      merge_id: mergeId,
    }
    if (aiProviderStore.selectedProviderId) body.provider_id = aiProviderStore.selectedProviderId
    await postRequest('/api/v1/ai-invoke/start', body)
    showToast(t('main.git_finalize.conflict_ai_started'), 'success')
  } catch (e: any) {
    showToast(e?.response?.data?.message || e?.response?.data?.error?.message || t('main.git_finalize.failed'), 'danger')
  } finally {
    busy.value = false
    await fetchState()
  }
}

async function copyConflictMention() {
  const mergeId = state.value?.merge_id
  if (!props.groupId || mergeId == null || busy.value) return
  busy.value = true
  try {
    const { data } = await postRequest<{ mention?: string }>('/api/v1/token/issue', {
      ...groupParts(props.groupId),
      action_scope: 'resolve_conflict',
      merge_id: mergeId,
    })
    if (!data.mention) throw new Error(t('main.git_finalize.failed'))
    await copyToClipboard(data.mention)
    showToast(t('main.git_finalize.conflict_mention_copied'), 'success')
  } catch (e: any) {
    showToast(e?.response?.data?.detail || e?.message || t('main.git_finalize.failed'), 'danger')
  } finally {
    busy.value = false
  }
}

async function runFinalize() {
  if (!props.groupId || !chosen.value) return
  if (showCommitInput.value && commitMessageBlank.value) return
  busy.value = true
  try {
    const payload: { action: string; commit_message?: string } = { action: chosen.value }
    if (showCommitInput.value) payload.commit_message = commitMessage.value.trim()
    await postFinalize(payload, false)
  } finally {
    busy.value = false
    await fetchState()
  }
}

async function postFinalize(
  payload: { action: string; commit_message?: string },
  retried: boolean,
): Promise<void> {
  try {
    const { data } = await postRequest<{ ok: boolean; result?: any; error?: any }>(
      `/api/v1/groups/${props.groupId}/git/finalize`,
      payload,
    )
    if (data.ok === false) {
      if (!retried && (await handleBaseDirty(data.error))) return postFinalize(payload, true)
      showToast(finalizeErrorMessage(data.error), 'danger')
    } else {
      const r = data.result
      mergeCommit.value = r?.merge_commit || null
      if (r?.status === 'conflict') {
        showToast(t('main.git_finalize.conflict_toast', { n: (r.conflict_files || []).length }), 'warning')
      } else if (r?.status === 'merged') {
        const key = r?.pushed === false ? 'main.git_finalize.merged_local_toast' : 'main.git_finalize.merged_toast'
        showToast(t(key, { commit: r.merge_commit || '' }), 'success')
      } else if (r?.status === 'pushed') {
        showToast(t('main.git_finalize.pushed_toast'), 'success')
      } else if (r?.status === 'waiting') {
        showToast(t('main.git_finalize.waiting_toast'), 'success')
      }
    }
  } catch (e: any) {
    const err = e?.response?.data?.error
    if (!retried && (await handleBaseDirty(err))) return postFinalize(payload, true)
    showToast(finalizeErrorMessage(err), 'danger')
  }
}

// 0331: the new `dirty_worktree` 409 is the one error the operator can act on
// directly — it names the fix (커밋 축으로 옮기세요) instead of echoing the raw
// server string, which is English-only and does not say what to press.
function finalizeErrorMessage(err: any): string {
  if (err?.code === 'dirty_worktree') {
    const n = Array.isArray(err?.details?.files) ? err.details.files.length : 0
    return t('main.git_finalize.dirty_push_blocked', { n })
  }
  return err?.message || t('main.git_finalize.failed')
}

// 0177 0007-CH: mirror GitActionMenu — the E3 base_dirty 409 is never auto-
// resolved. Open the commit/revert/cancel dialog; it clears the base checkout
// (and syncs the tree badges) and returns 'proceed' once clean so the merge
// retries with the original payload, or 'cancel' with no error toast.
async function handleBaseDirty(err: any): Promise<boolean> {
  const projectId = projectStore.currentProjectId
  if (err?.code !== 'base_dirty' || !projectId || !baseDirtyDialog.value) return false
  const files = Array.isArray(err.details?.files) ? err.details.files : []
  const outcome = await baseDirtyDialog.value.resolve(projectId, files)
  return outcome === 'proceed'
}

async function submitResolve() {
  const mergeId = state.value?.merge_id
  if (!props.groupId || mergeId == null || !allConflictsResolved.value) return
  busy.value = true
  conflictError.value = ''
  try {
    const { data } = await postRequest<{ ok: boolean; result?: any; error?: any }>(
      `/api/v1/groups/${props.groupId}/git/merge/${mergeId}/resolve`,
      {
        files: conflictFiles.value.map((f) => ({ path: f.path, content: currentFileContent(f) })),
        complete: true,
      },
    )
    if (data.ok === false) {
      conflictError.value = data.error?.message || t('main.git_finalize.failed')
    } else if (data.result?.status === 'merged') {
      mergeCommit.value = data.result.merge_commit || null
      conflictDialogOpen.value = false
      const key = data.result?.pushed === false ? 'main.git_finalize.merged_local_toast' : 'main.git_finalize.merged_toast'
      showToast(t(key, { commit: data.result.merge_commit || '' }), 'success')
    } else if (data.result?.status === 'conflict') {
      conflictError.value = data.result?.remaining_conflicts || t('main.git_finalize.failed')
    }
  } catch (e: any) {
    if (e?.response?.status === 404) {
      conflictDialogOpen.value = false
      showToast(e?.response?.data?.error?.message || t('main.git_finalize.failed'), 'danger')
    } else {
      conflictError.value = e?.response?.data?.error?.message || t('main.git_finalize.failed')
    }
  } finally {
    busy.value = false
    await fetchState()
  }
}

async function abortMerge() {
  const mergeId = state.value?.merge_id
  if (!props.groupId || mergeId == null) return
  busy.value = true
  try {
    await postRequest(`/api/v1/groups/${props.groupId}/git/merge/${mergeId}/abort`, {})
    conflictDialogOpen.value = false
    showToast(t('main.git_finalize.aborted_toast'), 'success')
  } catch (e: any) {
    showToast(e?.response?.data?.error?.message || t('main.git_finalize.failed'), 'danger')
  } finally {
    busy.value = false
    await fetchState()
  }
}

function matchesGroup(e: Event): boolean {
  const detail = (e as CustomEvent).detail || {}
  const eventGroup = detail.group_id || detail.groupId
  return !eventGroup || eventGroup === props.groupId
}

function onGitStatusChanged(e: Event) {
  if (matchesGroup(e)) fetchState()
}

onMounted(() => {
  if (typeof window !== 'undefined') {
    window.addEventListener('fg:git_pending_changed', onGitStatusChanged)
    window.addEventListener('fg:git_status_refresh', onGitStatusChanged)
    window.addEventListener('fg:git_status_open', onGitStatusChanged)
  }
})
onUnmounted(() => {
  if (typeof window !== 'undefined') {
    window.removeEventListener('fg:git_pending_changed', onGitStatusChanged)
    window.removeEventListener('fg:git_status_refresh', onGitStatusChanged)
    window.removeEventListener('fg:git_status_open', onGitStatusChanged)
  }
})

watch(() => props.groupId, fetchState, { immediate: true })

defineExpose({ fetchState })
</script>

<style scoped>
.git-fin-card {
  margin-bottom: 12px;
}
.git-fin-card .card-hd {
  display: flex;
  align-items: center;
  gap: 8px;
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
.badge-red {
  background: #fef2f2;
  color: #b91c1c;
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
.git-fin-meta {
  font-size: 0.78rem;
  color: var(--text-m);
  margin: 0 0 8px;
}
.git-choice-row {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}
.git-choice-row--aux {
  margin-top: 8px;
}
.git-aux {
  margin-top: 8px;
}
.git-aux-toggle {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: none;
  background: transparent;
  color: var(--text-m);
  font-size: 0.76rem;
  cursor: pointer;
  padding: 2px 0;
}
.git-aux-toggle:hover {
  color: var(--primary);
}
.git-choice {
  flex: 1 1 160px;
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 10px 12px;
  border: 1px solid var(--border, #e2e8f0);
  border-radius: var(--r, 8px);
  cursor: pointer;
}
.git-choice.sel {
  border-color: var(--primary);
  background: var(--primary-l, #eff6ff);
}
.git-choice input {
  display: none;
}
.git-choice-label {
  font-weight: 700;
  font-size: 0.82rem;
}
.git-choice-desc {
  font-size: 0.72rem;
  color: var(--text-m);
}
.git-commit-msg {
  margin-top: 12px;
}
.git-commit-msg-hd {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 5px;
}
.git-commit-msg-label {
  font-weight: 700;
  font-size: 0.8rem;
}
.git-commit-src-badge {
  background: #eff6ff;
  color: #1d4ed8;
  border: 1px solid #bfdbfe;
  font-size: 0.68rem;
}
.git-commit-msg-input {
  width: 100%;
  font-family: var(--mono, ui-monospace, monospace);
  font-size: 0.8rem;
}
.git-commit-msg-hint {
  font-size: 0.72rem;
  color: var(--text-m);
  margin: 5px 0 0;
}
.git-fin-done {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 0.85rem;
  color: #166534;
  margin: 0;
}
.git-fin-conflict-msg {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 0.8rem;
  color: #b91c1c;
  margin: 0 0 8px;
}
.git-conflict-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 14px;
  align-items: center;
  font-size: 0.78rem;
  color: var(--text-m);
}
.git-marker-warning {
  color: #b45309;
}
</style>

