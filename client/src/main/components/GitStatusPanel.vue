<template>
  <!-- flowgate.default.0162 §2·§3 — project Git control panel ("관제소"). Renders
       only for git-integrated projects; hidden entirely otherwise.
       0165 T0004: the pending list now finalizes any group without opening its R
       document — per-row action selection (merge / push / wait) and inline
       conflict resolution live here, so the header panel is self-sufficient. -->
  <div v-if="status && status.enabled" class="card git-status-card">
    <div class="card-hd">
      <span class="card-title">
        <AppIcon name="tree-structure" style="color:var(--text-m);" />
        {{ t('main.git_status.title') }}
      </span>
      <span class="git-branch-badge">
        <AppIcon name="git-commit" />
        {{ t('main.git_status.base_label') }}: {{ status.base_branch }}
      </span>
      <span class="git-ab-meta">{{ aheadBehindText }}</span>
      <span v-if="unpushedBadgeText" class="badge git-unpushed-badge">{{ unpushedBadgeText }}</span>
      <button
        v-if="canPushBase"
        class="btn btn-sm btn-primary"
        type="button"
        :disabled="busy"
        :title="needsFirstPush ? t('main.git_status.first_push_hint') : undefined"
        @click="doPush(status.base_branch)"
      >
        <AppIcon name="cloud-arrow-up" />
        {{ needsFirstPush ? t('main.git_status.first_push') : t('main.git_status.push_all') }}
      </button>
      <button
        class="git-refresh-btn"
        :disabled="busy"
        :title="t('main.git_status.fetch')"
        @click="doFetch"
      >
        <AppIcon name="cloud-arrow-down" />
      </button>
    </div>
    <!-- flowgate.default.0177 L0002 §2.6-b·c — base-checkout edits pending commit.
         The 0176 passive banner became an actionable section: per-file revert, an
         editable commit subject (seeded with the §2.2 default), and — when a merge
         finalize was parked on the base_dirty 409 — commit-then-merge in one go. -->
    <div v-if="showBaseDirtySection" class="git-base-dirty-alert" role="alert">
      <AppIcon name="warning" />
      <div class="git-base-dirty-alert__body">
        <div class="git-base-dirty-alert__msg">{{ t('main.git_finalize.base_dirty_alert') }}</div>
        <div v-for="f in baseDirtyFiles" :key="f" class="git-base-dirty-filerow">
          <span class="git-base-dirty-filerow__path">{{ f }}</span>
          <button
            class="btn btn-sm btn-secondary"
            type="button"
            :disabled="busy"
            @click="doBaseRevert(f)"
          >
            <AppIcon name="arrow-counter-clockwise" /> {{ t('main.git_status.base_revert_btn') }}
          </button>
        </div>
        <div v-if="baseDirtyFiles.length" class="git-base-commit-row">
          <input
            class="form-ctrl git-commit-msg-input"
            type="text"
            maxlength="200"
            :value="baseCommitMsg"
            :placeholder="baseCommitSuggested"
            @input="onBaseCommitInput(($event.target as HTMLInputElement).value)"
          />
          <button class="btn btn-sm btn-primary" type="button" :disabled="busy" @click="doBaseCommit">
            <AppIcon name="check" />
            {{ pendingFinalize ? t('main.git_status.base_commit_merge_btn') : t('main.git_status.base_commit_btn') }}
          </button>
        </div>
        <!-- everything reverted while a merge was parked → proceed without a commit -->
        <div v-else-if="pendingFinalize" class="git-base-commit-row">
          <button class="btn btn-sm btn-primary" type="button" :disabled="busy" @click="resumePendingFinalize">
            <AppIcon name="play" /> {{ t('main.git_status.base_merge_now_btn') }}
          </button>
        </div>
      </div>
      <button
        v-if="pendingFinalize"
        class="git-base-dirty-alert__close"
        type="button"
        :title="t('common.close')"
        @click="pendingFinalize = null"
      >
        <AppIcon name="x" />
      </button>
    </div>
    <!-- flowgate.default.0296 T0004 (NR0003 R1) — base-checkout files that were
         never committed. Unlike the block above these are NOT a merge blocker
         (the E3 guard is tracked-only, by design): they are simply absent from
         every group worktree, because `worktree add` checks out a commit. That
         is why an agent reports a file as missing while the file explorer plainly
         shows it. Committing here is the only in-app way to hand one to a worker,
         hence the explicit per-file pick — never a blanket `add -A`. -->
    <div v-if="baseUntrackedFiles.length" class="git-base-untracked" role="note">
      <AppIcon name="file-plus" />
      <div class="git-base-untracked__body">
        <div class="git-base-untracked__msg">
          {{ t('main.git_status.base_untracked_alert', { n: baseUntrackedCount }) }}
        </div>
        <label v-for="f in baseUntrackedFiles" :key="f" class="git-base-untracked-row">
          <input
            type="checkbox"
            :checked="untrackedPicked.includes(f)"
            :disabled="busy"
            @change="toggleUntracked(f)"
          />
          <span class="git-base-untracked-row__path">{{ f }}</span>
        </label>
        <div v-if="baseUntrackedTruncated" class="git-base-untracked__more">
          {{ t('main.git_status.base_untracked_truncated', { n: baseUntrackedFiles.length }) }}
        </div>
        <div class="git-base-commit-row">
          <input
            class="form-ctrl git-commit-msg-input"
            type="text"
            maxlength="200"
            :value="untrackedCommitMsg"
            :placeholder="untrackedCommitSuggested"
            @input="untrackedCommitMsg = ($event.target as HTMLInputElement).value"
          />
          <button class="btn btn-sm btn-secondary" type="button" :disabled="busy" @click="toggleAllUntracked">
            {{ allUntrackedPicked ? t('main.git_status.base_untracked_pick_none')
                                  : t('main.git_status.base_untracked_pick_all') }}
          </button>
          <button
            class="btn btn-sm btn-primary"
            type="button"
            :disabled="busy || !untrackedPicked.length"
            @click="doCommitUntracked"
          >
            <AppIcon name="check" />
            {{ t('main.git_status.base_untracked_commit_btn', { n: untrackedPicked.length }) }}
          </button>
        </div>
      </div>
    </div>
      <div class="card-bd pad">
      <!-- Unpushed base-merge list (0202 P0006 scenarios 5-8). -->
      <div v-if="showUnpushedSection" class="git-status-sect git-unpushed-sect">
        <p class="git-status-sub">{{ unpushedBadgeText }}</p>
        <div v-for="m in status.unpushed?.merges || []" :key="m.merge_commit" class="git-unpushed-row">
          <div class="git-unpushed-main">
            <span class="git-status-gid">{{ m.group_id || '-' }}</span>
            <span class="git-unpushed-commit">{{ m.merge_commit }}</span>
            <span class="git-unpushed-subject">{{ m.subject || '' }}</span>
          </div>
          <button
            class="btn btn-sm btn-secondary"
            type="button"
            :disabled="busy || !m.can_unmerge || !m.group_id"
            @click="doUnmerge(m)"
          >
            <AppIcon name="arrow-counter-clockwise" />
            {{ t('main.git_status.unmerge_btn') }}
          </button>
        </div>
      </div>

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
              <AppIcon name="warning" />
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
                <AppIcon name="play" /> {{ t('main.git_finalize.execute') }}
              </button>
            </template>

            <button class="btn btn-sm btn-secondary" @click="emit('open-group', p.group_id)">
              <AppIcon name="arrow-square-out" /> {{ t('main.git_status.open') }}
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

          <!-- Conflict resolution. 0182 NR0003 §6 introduced the chunk workflow;
               0212 T0009 replaced the compacted in-house overlay with the SAME
               shared 1180×820 resolver dialog (0207 시안 A) the finalize panel
               uses: file sidebar, chunk chips/navigation, AI assist strip,
               common-block folding, font-size controls. Submits resolve/abort
               against the same backend endpoints as before. -->
          <GitConflictResolverDialog
            v-if="expanded === p.group_id && p.status === 'conflict'"
            :files="conflictFiles"
            :branch="p.branch || p.group_id"
            :base-branch="status?.base_branch || null"
            :busy="busy"
            :load-status="conflictLoadStatus"
            :error-message="conflictError"
            :providers="aiProviderStore.providers"
            :selected-provider="aiProviderStore.selectedProviderId"
            :provider-loading="aiProviderStore.loading"
            :provider-errored="!!aiProviderStore.error"
            @close="collapseResolve"
            @abort="abortInline(p)"
            @submit="submitResolveInline(p)"
            @retry="openResolve(p.group_id)"
            @ai-invoke="invokeConflictAi(p)"
            @copy-mention="copyConflictMention(p)"
            @update:provider="aiProviderStore.selectProvider"
          />
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
          <AppIcon name="git-branch" />
          <span class="git-status-branch">{{ s.branch }}</span>
          <span class="git-status-slot-gid">{{ s.group_id }}</span>
          <span class="badge" :class="statusBadgeClass(s.status)">{{ statusLabel(s.status) }}</span>
        </div>
      </div>

      <!-- Recovery (manual cleanup only; base push is a first-class header action). -->
      <div class="git-status-sect git-status-recovery">
        <p class="git-status-sub">{{ t('main.git_status.recovery_header') }}</p>
        <!-- 0182 NR0003 §5: backlog sweep of finalized slots' leftovers (worktree
             dir + local work branch + ledger). New finalizes clean up after
             themselves; this clears what accumulated before that (or failed). -->
        <button
          v-if="(status.cleanable_count ?? 0) > 0"
          class="btn btn-sm btn-secondary"
          :disabled="busy"
          @click="doCleanup"
        >
          <AppIcon name="broom" />
          {{ t('main.git_status.cleanup_btn', { n: status.cleanable_count }) }}
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
import { useExplorerStore } from '../stores/explorer'
import { useAiProviderStore } from '../stores/aiProvider'
import AppIcon from '@shared/AppIcon.vue'
// 0182 NR0003 §6: chunk-based conflict resolution shared with GitFinalizePanel
// (parser state machine + reassembly + residual-marker guard). 0212 T0009: the
// resolver UI itself is the shared GitConflictResolverDialog.
import {
  useConflictChunks,
  currentFileContent,
  isFileResolved,
  type ConflictFileState,
} from '../composables/useConflictChunks'
import GitConflictResolverDialog from './GitConflictResolverDialog.vue'

const props = defineProps<{ projectId: string }>()
const emit = defineEmits<{ 'open-group': [groupId: string] }>()

const { t } = useI18n()
const { showToast } = useToast()
const { initConflictFile } = useConflictChunks()
// 0234 B0001: the header provider selection must reach the conflict AI run. This store
// is the single source of truth for the runtime provider (also driven by AppHeader).
const aiProviderStore = useAiProviderStore()

// Fixed finalize actions (git_service.ACTION_VALUES). Kept as an array literal so
// the i18n static-reference scanner sees the backtick keys, not a computed one.
// 0331 NR0005 §4.1: this header panel is the third finalize surface. It used to
// offer only three of the actions, so work that could be finished from the
// document panel could not be finished from here. Ordered like the approved v4
// axis reads top-to-bottom (머지 → 커밋 → 대기, push variant first).
const ACTIONS = ['merge', 'merge_only', 'commit_push', 'commit_only', 'push', 'wait'] as const

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
  // 0182 NR0003 §4: the group's final-approval doc (pending implies wf_done)
  ac_doc_id?: string | null
}
interface GitStatus {
  enabled: boolean
  base_branch: string | null
  base_path_state: string
  ahead_count: number | null
  behind_count: number | null
  // 0177 L0002 §2.1: base-checkout dirty set (tracked files only)
  base_dirty?: { dirty: boolean; files: string[] }
  // 0296 T0004: never-committed files. A SEPARATE field on purpose — folding it
  // into base_dirty would widen the E3 guard to build artifacts (0165.0009).
  base_untracked?: { count: number; files: string[]; truncated?: boolean }
  slots: Slot[]
  pending: Pending[]
  pending_count: number
  // 0182 NR0003 §5: finalized (merged/pushed) slots whose leftovers await cleanup
  cleanable_count?: number
  unpushed?: {
    count: number
    commit_count: number
    merges: UnpushedMerge[]
    // 0297 B0001: `measured: false` means origin/{base} was absent, NOT "in sync".
    // remote_branch_missing narrows that to the bootstrap case (empty remote) and
    // local_commit_count says whether there is anything to publish.
    measured?: boolean
    remote_branch_missing?: boolean
    local_commit_count?: number | null
  }
}
interface UnpushedMerge {
  merge_commit: string
  group_id: string | null
  subject?: string | null
  merged_at?: string | null
  can_unmerge: boolean
  blocked_reason?: string | null
}

const status = ref<GitStatus | null>(null)
const busy = ref(false)
const explorerStore = useExplorerStore()

// ── Base-checkout commit / revert (0177 L0002 §2.6-b·c) ──────────────────────

// A merge finalize that bounced off the E3 base_dirty 409 parks here; after the
// user commits (or reverts everything) it is re-posted with the ORIGINAL action
// and absorb commit_message. Cleared on any finalize success or explicit close.
const pendingFinalize = ref<{
  groupId: string
  payload: { action: string; commit_message?: string }
} | null>(null)

const baseDirtyFiles = computed(() => status.value?.base_dirty?.files ?? [])
const showBaseDirtySection = computed(
  () => baseDirtyFiles.value.length > 0 || !!pendingFinalize.value,
)

// Mirrors git_service.default_base_commit_message (L0002 §2.2): what the user
// sees seeded is exactly what the server derives from a blank message.
const COMMIT_SUBJECT_MAX = 200
function defaultBaseCommitMessage(files: string[]): string {
  if (!files.length) return ''
  const joined = 'fix: ' + files.join(', ')
  if (joined.length <= COMMIT_SUBJECT_MAX) return joined
  return `fix: ${files[0]} 외 ${files.length - 1}건`.slice(0, COMMIT_SUBJECT_MAX)
}

const baseCommitSuggested = computed(() => defaultBaseCommitMessage(baseDirtyFiles.value))

// ── Never-committed base files (0296 T0004 / NR0003 R1) ──────────────────────

const baseUntrackedFiles = computed(() => status.value?.base_untracked?.files ?? [])
// The server caps the listed paths; `count` is what it actually listed, so the
// header stays honest about a checkout holding more than one screenful.
const baseUntrackedCount = computed(
  () => status.value?.base_untracked?.count ?? baseUntrackedFiles.value.length,
)
const baseUntrackedTruncated = computed(() => !!status.value?.base_untracked?.truncated)

const untrackedPicked = ref<string[]>([])
const untrackedCommitMsg = ref('')
const untrackedCommitSuggested = computed(() => defaultBaseCommitMessage(untrackedPicked.value))
const allUntrackedPicked = computed(
  () =>
    baseUntrackedFiles.value.length > 0 &&
    untrackedPicked.value.length === baseUntrackedFiles.value.length,
)

function toggleUntracked(file: string) {
  const i = untrackedPicked.value.indexOf(file)
  if (i >= 0) untrackedPicked.value.splice(i, 1)
  else untrackedPicked.value.push(file)
}
function toggleAllUntracked() {
  untrackedPicked.value = allUntrackedPicked.value ? [] : [...baseUntrackedFiles.value]
}
const baseCommitMsg = ref('')
const baseCommitEdited = ref(false)
function onBaseCommitInput(value: string) {
  baseCommitMsg.value = value
  baseCommitEdited.value = true
}
// Keep the seed following the live file list until the user takes over.
watch(baseCommitSuggested, (suggested) => {
  if (!baseCommitEdited.value) baseCommitMsg.value = suggested
})

// Per-row chosen action (overrides default_action); keyed by group_id.
const chosen = ref<Record<string, string>>({})
// Currently expanded conflict row + its fetched files (chunk view state, §6).
const expanded = ref<string | null>(null)
const conflictFiles = ref<ConflictFileState[]>([])
const conflictError = ref('')
// Load lifecycle for the shared resolver dialog (loading spinner / retry state).
const conflictLoadStatus = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')

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

// Residual-marker guard (B0001 F2): the shared dialog disables its submit
// button on unresolved markers; keep the same check here so a stray submit
// event can never post <<<<<<< />>>>>>>> content to bounce off the backend 422.
const inlineResolved = computed(
  () => conflictFiles.value.length > 0 && conflictFiles.value.every(isFileResolved),
)

const aheadBehindText = computed(() => {
  const s = status.value
  if (!s || s.ahead_count == null || s.behind_count == null) {
    // 0297 B0001: an empty remote is not a stale fetch — telling the user to
    // fetch there is a dead end, the actual next step is the first push.
    return needsFirstPush.value
      ? t('main.git_status.remote_empty')
      : t('main.git_status.unmeasured')
  }
  return t('main.git_finalize.ahead_behind', { ahead: s.ahead_count, behind: s.behind_count })
})
const unpushedCount = computed(() => status.value?.unpushed?.count ?? 0)
const unpushedCommitCount = computed(() => status.value?.unpushed?.commit_count ?? status.value?.ahead_count ?? 0)
// 0297 B0001: with an empty remote there is no origin/{base}, so ahead/behind and
// the unpushed count come back unmeasured (0) — the push button used to vanish
// exactly when the first push was needed. Treat "remote branch missing + local
// commits exist" as pushable; the server's push endpoint already handles it.
const needsFirstPush = computed(() => {
  const u = status.value?.unpushed
  if (!u || u.measured !== false || !u.remote_branch_missing) return false
  return (u.local_commit_count ?? 0) > 0
})
const canPushBase = computed(
  () => !!status.value?.base_branch && (unpushedCommitCount.value > 0 || needsFirstPush.value),
)
const showUnpushedSection = computed(
  () => unpushedCount.value > 0 || (unpushedCount.value === 0 && unpushedCommitCount.value > 0),
)
const unpushedBadgeText = computed(() => {
  if (!status.value) return ''
  if (needsFirstPush.value) {
    return t('main.git_status.first_push_badge', {
      n: status.value.unpushed?.local_commit_count ?? 0,
    })
  }
  if (unpushedCount.value > 0) return t('main.git_status.unpushed_badge', { n: unpushedCount.value })
  if (unpushedCommitCount.value > 0) return t('main.git_status.unpushed_commits', { n: unpushedCommitCount.value })
  return ''
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
    // 0282 NR0003 발견 3: shared store fetch — concurrent callers coalesce onto
    // one git/status request. The §2.6-a badge sync (trigger 1/4) moved into the
    // store fetch itself.
    const next = (await explorerStore.fetchGitStatus(
      props.projectId,
    )) as unknown as GitStatus | null
    status.value = next
    // Drop an expanded conflict editor whose row no longer reports a conflict.
    if (next && expanded.value) {
      const still = next.pending.find(
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
  const action = actionOf(item)
  // Attach the confirmed commit subject for merge/push (B0001 F1). Blank →
  // omit the field so git_service resolves the subject on the unmanned path.
  const payload: { action: string; commit_message?: string } = { action }
  // 0331: `push` no longer absorbs a dirty worktree (it 409s instead), so a
  // subject sent with it would describe a commit that is never made.
  if (action !== 'wait' && action !== 'push') {
    const msg = (commitDrafts.value[item.group_id]?.message || '').trim()
    if (msg) payload.commit_message = msg
  }
  busy.value = true
  try {
    await runFinalize(item.group_id, payload)
  } finally {
    busy.value = false
    await fetchStatus()
  }
}

// One finalize round-trip with the shared outcome handling; both the pending-row
// [execute] and the base-dirty [commit-then-merge]/[merge now] paths land here.
async function runFinalize(groupId: string, payload: { action: string; commit_message?: string }) {
  try {
    const { data } = await postRequest<{ ok: boolean; result?: any; error?: any }>(
      `/api/v1/groups/${groupId}/git/finalize`,
      payload,
    )
    if (data.ok === false) {
      if (!handleBaseDirty(groupId, payload, data.error)) {
        // 0331: name the fix for a blocked bare push instead of echoing the raw
        // English server string.
        const err = data.error
        const msg =
          err?.code === 'dirty_worktree'
            ? t('main.git_finalize.dirty_push_blocked', {
                n: Array.isArray(err?.details?.files) ? err.details.files.length : 0,
              })
            : err?.message || t('main.git_finalize.failed')
        showToast(msg, 'danger')
      }
      return
    }
    pendingFinalize.value = null // a finalize got through — nothing parked anymore
    const r = data.result
    if (r?.status === 'conflict') {
      showToast(t('main.git_finalize.conflict_toast', { n: (r.conflict_files || []).length }), 'warning')
      // Resolve right here instead of routing to the R document.
      await fetchStatus()
      await openResolve(groupId)
    } else if (r?.status === 'merged') {
      const key = r?.pushed === false ? 'main.git_finalize.merged_local_toast' : 'main.git_finalize.merged_toast'
      showToast(t(key, { commit: r.merge_commit || '' }), 'success')
    } else if (r?.status === 'pushed') {
      showToast(t('main.git_finalize.pushed_toast'), 'success')
    } else if (r?.status === 'waiting') {
      showToast(t('main.git_finalize.waiting_toast'), 'success')
    }
  } catch (e: any) {
    const err = e?.response?.data?.error
    if (!handleBaseDirty(groupId, payload, err)) {
      showToast(err?.message || t('main.git_finalize.failed'), 'danger')
    }
  }
}

// 0177 L0002 §2.6-c (evolved from the 0176 passive banner): the E3 base_dirty
// guard (now HTTP 409 + {code:'base_dirty', details:{files:[...]}}) means the
// base checkout has uncommitted edits blocking merge finalize for EVERY group.
// Park the attempted finalize and open the actionable commit/revert section —
// after a commit (or reverting everything) the merge re-runs with the original
// action and commit_message. Returns true when it handled the error.
function handleBaseDirty(
  groupId: string,
  payload: { action: string; commit_message?: string },
  err: any,
): boolean {
  // 0296 T0004 (NR0003 R5): the sibling failure — an untracked base file sits on
  // a path the merge wants to create. git refuses, the tracked-only E3 guard
  // never saw it, and this used to arrive as a bare 500. Point at the same
  // section: committing those files is exactly the fix.
  if (err?.code === 'base_untracked_conflict') {
    const blocked: string[] = Array.isArray(err.details?.files) ? err.details.files : []
    untrackedPicked.value = blocked
    showToast(
      t('main.git_status.base_untracked_conflict_toast', { files: blocked.join(', ') }),
      'danger',
    )
    return true
  }
  if (err?.code !== 'base_dirty') return false
  pendingFinalize.value = { groupId, payload }
  const files = Array.isArray(err.details?.files) ? err.details.files : []
  // Badge trigger 4/4 + immediate section render (fetchStatus follows anyway).
  if (status.value) status.value.base_dirty = { dirty: files.length > 0, files }
  explorerStore.setBaseDirtyFiles(props.projectId, files)
  showToast(t('main.git_finalize.base_dirty_toast'), 'danger')
  return true
}

async function doBaseCommit() {
  if (busy.value || !props.projectId) return
  busy.value = true
  try {
    const msg = baseCommitMsg.value.trim()
    // Blank → omit; the server derives the identical §2.2 default itself.
    const { data } = await postRequest<{ ok: boolean; result?: any; error?: any }>(
      `/api/v1/projects/${props.projectId}/git/base-commit`,
      msg ? { message: msg } : {},
    )
    if (data.ok === false) {
      showToast(data.error?.message || t('main.git_status.failed'), 'danger')
      return
    }
    const r = data.result
    const remaining: string[] = Array.isArray(r?.remaining) ? r.remaining : []
    if (status.value) status.value.base_dirty = { dirty: remaining.length > 0, files: remaining }
    explorerStore.setBaseDirtyFiles(props.projectId, remaining) // badge trigger 3/4
    if (r?.committed) {
      showToast(t('main.git_status.base_commit_done', { commit: r.commit || '' }), 'success')
    }
    baseCommitMsg.value = ''
    baseCommitEdited.value = false
    // Commit-then-merge: the parked finalize resumes as soon as the base is clean
    // (an idempotent {committed:false} race result resumes just the same).
    if (pendingFinalize.value && remaining.length === 0) {
      const { groupId, payload } = pendingFinalize.value
      await runFinalize(groupId, payload)
    }
  } catch (e: any) {
    showToast(e?.response?.data?.error?.message || t('main.git_status.failed'), 'danger')
  } finally {
    busy.value = false
    await fetchStatus()
  }
}

// 0296 T0004: commit exactly the picked new files. `paths` is what makes the
// server stage untracked entries at all — without it base-commit runs `add -u`
// and silently ignores them, which is how B0001's "why?????" happened.
async function doCommitUntracked() {
  if (busy.value || !props.projectId || !untrackedPicked.value.length) return
  busy.value = true
  try {
    const msg = untrackedCommitMsg.value.trim()
    const body: { paths: string[]; message?: string } = { paths: [...untrackedPicked.value] }
    if (msg) body.message = msg
    const { data } = await postRequest<{ ok: boolean; result?: any; error?: any }>(
      `/api/v1/projects/${props.projectId}/git/base-commit`,
      body,
    )
    if (data.ok === false) {
      showToast(data.error?.message || t('main.git_status.failed'), 'danger')
      return
    }
    const r = data.result
    // Tracked leftovers still drive the E3 badges; the untracked list is its own
    // field and must not be mistaken for them.
    const remaining: string[] = Array.isArray(r?.remaining) ? r.remaining : []
    if (status.value) status.value.base_dirty = { dirty: remaining.length > 0, files: remaining }
    explorerStore.setBaseDirtyFiles(props.projectId, remaining)
    if (r?.committed) {
      showToast(
        t('main.git_status.base_untracked_commit_done', {
          n: (r.files || []).length,
          commit: r.commit || '',
        }),
        'success',
      )
    }
    untrackedPicked.value = []
    untrackedCommitMsg.value = ''
  } catch (e: any) {
    const err = e?.response?.data?.error
    // A .gitignore'd path can never be committed (NR0003 §C4) — the server says
    // which ones, so the operator stops retrying a commit that cannot work.
    if (err?.code === 'path_ignored') {
      showToast(
        t('main.git_status.base_untracked_ignored', {
          files: (err.details?.files || []).join(', '),
        }),
        'danger',
      )
    } else {
      showToast(err?.message || t('main.git_status.failed'), 'danger')
    }
  } finally {
    busy.value = false
    await fetchStatus()
  }
}

async function doBaseRevert(file: string) {
  if (busy.value || !props.projectId) return
  busy.value = true
  try {
    const { data } = await postRequest<{ ok: boolean; result?: any; error?: any }>(
      `/api/v1/projects/${props.projectId}/git/base-revert`,
      { files: [file] },
    )
    if (data.ok === false) {
      showToast(data.error?.message || t('main.git_status.failed'), 'danger')
      return
    }
    const remaining: string[] = Array.isArray(data.result?.remaining) ? data.result.remaining : []
    if (status.value) status.value.base_dirty = { dirty: remaining.length > 0, files: remaining }
    explorerStore.setBaseDirtyFiles(props.projectId, remaining) // badge trigger 3/4
    showToast(t('main.git_status.base_revert_done', { file }), 'success')
  } catch (e: any) {
    showToast(e?.response?.data?.error?.message || t('main.git_status.failed'), 'danger')
  } finally {
    busy.value = false
    await fetchStatus()
  }
}

// §2.6-c: everything was reverted while a merge sat parked — run it commit-free.
async function resumePendingFinalize() {
  if (busy.value || !pendingFinalize.value) return
  const { groupId, payload } = pendingFinalize.value
  busy.value = true
  try {
    await runFinalize(groupId, payload)
  } finally {
    busy.value = false
    await fetchStatus()
  }
}

// ── Inline conflict resolution (endpoints already exist, P0005 §6) ────────────

function collapseResolve() {
  expanded.value = null
  conflictFiles.value = []
  conflictError.value = ''
  conflictLoadStatus.value = 'idle'
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
  conflictLoadStatus.value = 'loading'
  // Populate the provider selector shown in the resolver footer (RC2).
  void aiProviderStore.ensureLoaded(props.projectId)
  try {
    const { data } = await getRequest<{
      ok: boolean
      files: Array<{ path: string; content: string; conflict_count: number }>
    }>(`/api/v1/groups/${groupId}/git/merge/${p.merge_id}/conflicts`)
    conflictFiles.value = (data.files || []).map(initConflictFile)
    conflictLoadStatus.value = 'ready'
  } catch (e: any) {
    conflictError.value = e?.response?.data?.error?.message || t('main.git_finalize.failed')
    conflictLoadStatus.value = 'error'
  }
}

async function submitResolveInline(p: Pending) {
  if (p.merge_id == null || busy.value || !inlineResolved.value) return
  busy.value = true
  conflictError.value = ''
  try {
    const { data } = await postRequest<{ ok: boolean; result?: any; error?: any }>(
      `/api/v1/groups/${p.group_id}/git/merge/${p.merge_id}/resolve`,
      {
        files: conflictFiles.value.map((f) => ({ path: f.path, content: currentFileContent(f) })),
        complete: true,
      },
    )
    if (data.ok === false) {
      conflictError.value = data.error?.message || t('main.git_finalize.failed')
    } else if (data.result?.status === 'merged') {
      const key = data.result?.pushed === false ? 'main.git_finalize.merged_local_toast' : 'main.git_finalize.merged_toast'
      showToast(t(key, { commit: data.result.merge_commit || '' }), 'success')
      collapseResolve()
    }
  } catch (e: any) {
    conflictError.value = e?.response?.data?.error?.message || t('main.git_finalize.failed')
  } finally {
    busy.value = false
    await fetchStatus()
  }
}

function groupParts(groupId: string) {
  const [project, module = 'none', ...rest] = groupId.split('.')
  return { project, module, group: rest.join('.') }
}

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

async function invokeConflictAi(p: Pending) {
  if (p.merge_id == null || busy.value) return
  busy.value = true
  try {
    // RC1: forward the header/dialog provider selection so the run honours it instead
    // of silently falling back to the server default chain (first = e.g. Fable).
    await aiProviderStore.ensureLoaded(props.projectId)
    const body: Record<string, unknown> = {
      ...groupParts(p.group_id),
      action_scope: 'resolve_conflict',
      mode: 'single',
      merge_id: p.merge_id,
    }
    if (aiProviderStore.selectedProviderId) body.provider_id = aiProviderStore.selectedProviderId
    await postRequest('/api/v1/ai-invoke/start', body)
    showToast(t('main.git_finalize.conflict_ai_started'), 'success')
  } catch (e: any) {
    showToast(e?.response?.data?.message || e?.response?.data?.error?.message || t('main.git_finalize.failed'), 'danger')
  } finally {
    busy.value = false
    await fetchStatus()
  }
}

async function copyConflictMention(p: Pending) {
  if (p.merge_id == null || busy.value) return
  busy.value = true
  try {
    const { data } = await postRequest<{ mention?: string }>('/api/v1/token/issue', {
      ...groupParts(p.group_id),
      action_scope: 'resolve_conflict',
      merge_id: p.merge_id,
    })
    if (!data.mention) throw new Error(t('main.git_finalize.failed'))
    await copyToClipboard(data.mention)
    showToast(t('main.git_finalize.conflict_mention_copied'), 'success')
  } catch (e: any) {
    // A stale conflict card can point at a non-open merge session; the server now
    // returns the git envelope {ok:false,error:{message}} (0233 B0001) instead of a
    // bodyless 500, so surface error.message like the other git actions do.
    showToast(e?.response?.data?.error?.message || e?.response?.data?.detail || e?.message || t('main.git_finalize.failed'), 'danger')
  } finally {
    busy.value = false
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

// 0182 NR0003 §5: retroactive cleanup of merged/pushed slot leftovers.
async function doCleanup() {
  if (busy.value || !props.projectId) return
  busy.value = true
  try {
    const { data } = await postRequest<{
      ok: boolean
      result?: { cleaned: string[]; failed: string[] }
      error?: any
    }>(`/api/v1/projects/${props.projectId}/git/cleanup`, {})
    if (data.ok === false) {
      showToast(data.error?.message || t('main.git_status.failed'), 'danger')
    } else {
      const cleaned = data.result?.cleaned?.length ?? 0
      const failed = data.result?.failed?.length ?? 0
      if (failed > 0) {
        showToast(t('main.git_status.cleanup_partial', { n: cleaned, failed }), 'warning')
      } else {
        showToast(t('main.git_status.cleanup_done', { n: cleaned }), 'success')
      }
    }
  } catch (e: any) {
    showToast(e?.response?.data?.error?.message || t('main.git_status.failed'), 'danger')
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

async function doUnmerge(m: UnpushedMerge) {
  if (busy.value || !m.group_id || !m.merge_commit) return
  const ok = window.confirm(t('main.git_status.unmerge_confirm', {
    gid: m.group_id,
    commit: m.merge_commit,
  }))
  if (!ok) return
  busy.value = true
  try {
    const { data } = await postRequest<{ ok: boolean; result?: any; error?: any }>(
      `/api/v1/groups/${m.group_id}/git/unmerge`,
      { merge_commit: m.merge_commit },
    )
    if (data.ok === false) {
      showToast(data.error?.message || t('main.git_status.failed'), 'danger')
    } else {
      const key = data.result?.reprovisioned === false
        ? 'main.git_status.unmerge_reprovisioning'
        : 'main.git_status.unmerged_toast'
      showToast(t(key), 'success')
      if (typeof window !== 'undefined') {
        window.dispatchEvent(new CustomEvent('fg:git_status_refresh', {
          detail: { project: props.projectId, group_id: m.group_id, status: 'awaiting_choice' },
        }))
      }
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
.git-unpushed-badge {
  background: #fff7ed;
  color: #c2410c;
  border: 1px solid #fed7aa;
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
.git-unpushed-sect {
  border-bottom: 1px dashed var(--border, #e2e8f0);
  padding-bottom: 10px;
}
.git-unpushed-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 0;
}
.git-unpushed-main {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
}
.git-unpushed-commit {
  font-family: var(--mono, ui-monospace, monospace);
  font-size: 0.76rem;
  color: #0369a1;
}
.git-unpushed-subject {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 0.76rem;
  color: var(--text-m);
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
/* flowgate.default.0176 T0010 §b banner → 0177 L0002 §2.6 actionable section. */
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
.git-base-dirty-filerow {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
}
.git-base-dirty-filerow__path {
  flex: 1 1 auto;
  min-width: 0;
  font-family: var(--font-mono, monospace);
  font-size: 0.74rem;
  color: var(--text-m);
  word-break: break-all;
}
.git-base-commit-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}
.git-base-commit-row .git-commit-msg-input {
  flex: 1 1 auto;
  min-width: 0;
}
.git-base-commit-row .btn {
  flex: none;
  white-space: nowrap;
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
/* 0296 T0004 — informational, not a blocker: an accent/neutral treatment so it
   never reads as the danger-coloured base_dirty alert directly above it. */
.git-base-untracked {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin: 0 16px 4px;
  padding: 10px 12px;
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 8px;
  background: var(--bg-subtle, #f8fafc);
  font-size: 0.8rem;
  line-height: 1.45;
}
.git-base-untracked > i {
  color: var(--accent, #2563eb);
  margin-top: 2px;
  flex: none;
}
.git-base-untracked__body {
  flex: 1 1 auto;
  min-width: 0;
}
.git-base-untracked__msg {
  color: var(--text, inherit);
}
.git-base-untracked-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
  cursor: pointer;
}
.git-base-untracked-row__path {
  flex: 1 1 auto;
  min-width: 0;
  font-family: var(--font-mono, monospace);
  font-size: 0.74rem;
  color: var(--text-m);
  word-break: break-all;
}
.git-base-untracked__more {
  margin-top: 6px;
  font-size: 0.72rem;
  color: var(--text-m);
}
</style>
