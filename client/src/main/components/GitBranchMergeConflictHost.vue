<template>
  <!-- flowgate.default.0630 T0005 §12 — the 0.1 Branch Manager's minimal wiring into the
       EXISTING conflict resolver and merge review dialogs for an ordinary branch merge.
       This component draws nothing of its own: it fetches the server attempt, feeds the
       two dialogs from the project-scoped routes, and switches between them on the
       server's attempt state (never on AI messages). -->
  <GitConflictResolverDialog
    v-if="mode === 'resolve'"
    :files="conflictFiles"
    :branch="sourceLabel"
    :base-branch="targetLabel"
    :busy="busy"
    :load-status="loadStatus"
    :error-message="errorMessage"
    :providers="aiProviderStore.providers"
    :selected-provider="aiProviderStore.selectedProviderId"
    :provider-loading="aiProviderStore.loading"
    :provider-errored="!!aiProviderStore.error"
    :ai-run-notice="aiRunNotice"
    :ai-run-pending="aiStarting"
    :hide-auto-authority="true"
    :hide-copy-mention="true"
    @close="emit('close')"
    @abort="abortMerge"
    @submit="submitResolve"
    @retry="reload"
    @ai-invoke="invokeAi"
    @update:provider="aiProviderStore.selectProvider"
    @reload-providers="reloadProviders"
  />
  <GitMergeReviewDialog
    v-else-if="mode === 'review'"
    group-id=""
    :merge-id="mergeId"
    :merge-api-base="apiBase"
    :branch="sourceLabel"
    :base-branch="targetLabel"
    :providers="aiProviderStore.providers"
    :selected-provider="aiProviderStore.selectedProviderId"
    :provider-loading="aiProviderStore.loading"
    :provider-errored="!!aiProviderStore.error"
    :abortable="true"
    :abort-busy="busy"
    @close="emit('close')"
    @resolved="onReviewResolved"
    @abort="abortFromReview"
    @update:provider="aiProviderStore.selectProvider"
  />
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { getRequest, postRequest } from '@shared/api'
import { resolveGitError } from '@shared/gitErrors'
import { confirm } from '../composables/useDialogStack'
import { useAiProviderStore } from '../stores/aiProvider'
import { useToast } from './common/useToast'
import {
  useConflictChunks,
  currentFileContent,
  type ConflictFileState,
} from '../composables/useConflictChunks'
import GitConflictResolverDialog from './GitConflictResolverDialog.vue'
import GitMergeReviewDialog from './GitMergeReviewDialog.vue'

interface BranchMergeAttempt {
  merge_id: number
  state: string
  source_branch: string
  target_branch: string
  source_kind?: string
  source_group_id?: string | null
  target_kind?: string
  target_group_id?: string | null
  push: boolean
  file_count: number
  resolved_count: number
  ai: { status: string; run_id?: string | null; provider_id?: string | null; error?: { code?: string; message?: string } | null }
}

const props = defineProps<{ projectId: string; mergeId: number }>()
const emit = defineEmits<{ close: []; changed: [] }>()

const { t } = useI18n()
const { showToast } = useToast()
const aiProviderStore = useAiProviderStore()
const { initConflictFile } = useConflictChunks()

const REVIEW_STATES = new Set(['resolved_pending_review', 're_review', 'applying', 'reconciling'])
const RESOLVE_STATES = new Set(['conflict', 'ai_resolving', 'conflict_remaining', 'interrupted', 'starting'])
// 0683 T0004 §1 — states the server can still move on its own (the AI resolver finishing,
// the attempt leaving `starting`); the host keeps reading them so a resolution that lands
// while this dialog is open hands over to the review instead of sitting on the resolver.
const LIVE_STATES = new Set(['ai_resolving', 'starting'])
const POLL_MS = 3000

const attempt = ref<BranchMergeAttempt | null>(null)
const conflictFiles = ref<ConflictFileState[]>([])
const loadStatus = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
const errorMessage = ref('')
const busy = ref(false)
const aiStarting = ref(false)
const mode = ref<'resolve' | 'review' | null>(null)
let pollTimer: ReturnType<typeof setTimeout> | null = null
let disposed = false

const apiBase = computed(() => `/api/v1/projects/${props.projectId}/git/merge/${props.mergeId}`)

const sourceLabel = computed(() => {
  const att = attempt.value
  if (!att) return null
  if (att.source_kind === 'worktree' && att.source_group_id) {
    return `${att.source_branch} (${t('main.git_branch_manager.kind.internal_slot')}: ${att.source_group_id})`
  }
  return att.source_branch || null
})

const targetLabel = computed(() => {
  const att = attempt.value
  if (!att) return null
  if (att.target_kind === 'worktree' && att.target_group_id) {
    return `${att.target_branch} (${t('main.git_branch_manager.kind.internal_slot')}: ${att.target_group_id})`
  }
  return att.target_branch || null
})

// The one sentence the resolver dialog shows about the AI half — the server's attempt
// state decides it; the run's messages are the run's own detail view, not this line.
const aiRunNotice = computed(() => {
  const ai = attempt.value?.ai
  if (!ai || attempt.value?.state !== 'ai_resolving') return null
  return t('main.git_branch_manager.conflict_ai_running', {
    provider: ai.provider_id || t('main.git_review.unknown_provider'),
  })
})

function aiFailureText(): string {
  const ai = attempt.value?.ai
  if (ai?.status !== 'start_failed') return ''
  return t('main.git_branch_manager.merge_conflict_ai.start_failed_reason', {
    reason: ai.error?.message || ai.error?.code || '',
  })
}

async function loadAttempt(): Promise<BranchMergeAttempt | null> {
  const { data } = await getRequest<{ ok: boolean; result: BranchMergeAttempt }>(apiBase.value)
  attempt.value = data?.result || null
  return attempt.value
}

async function loadConflicts() {
  const { data } = await getRequest<{ files: Array<{ path: string; content: string; conflict_count: number }> }>(
    `${apiBase.value}/conflicts`,
  )
  conflictFiles.value = (data?.files || []).map(initConflictFile)
}

function settleMode(current: BranchMergeAttempt | null): boolean {
  if (!current) return false
  if (REVIEW_STATES.has(current.state)) {
    mode.value = 'review'
    return true
  }
  if (RESOLVE_STATES.has(current.state)) {
    mode.value = 'resolve'
    return true
  }
  // completed / aborted / failed — nothing left to open.
  showToast(t(`main.git_branch_manager.attempt_state.${current.state}`, current.state), 'success')
  emit('changed')
  emit('close')
  return false
}

async function reload() {
  loadStatus.value = conflictFiles.value.length ? loadStatus.value : 'loading'
  errorMessage.value = ''
  try {
    const current = await loadAttempt()
    if (!settleMode(current)) return
    if (mode.value === 'resolve') await loadConflicts()
    loadStatus.value = 'ready'
    errorMessage.value = aiFailureText()
  } catch (e: any) {
    loadStatus.value = 'error'
    errorMessage.value = resolveGitError(e, t, 'main.git_branch_manager.op_failed')
  } finally {
    schedulePoll()
  }
}

// While the server says the AI is resolving, re-read the attempt in the background —
// the dialog stays mounted (no loading gate), and the conflict list is refreshed only
// when the state actually moves.
function schedulePoll() {
  if (pollTimer) clearTimeout(pollTimer)
  pollTimer = null
  if (disposed || !LIVE_STATES.has(attempt.value?.state || '')) return
  pollTimer = setTimeout(async () => {
    if (disposed) return
    const before = attempt.value?.state
    try {
      const current = await loadAttempt()
      if (current && current.state !== before) {
        if (!settleMode(current)) return
        if (mode.value === 'review') {
          // `resolved_pending_review` is not a finished merge: say the review is where it goes on.
          showToast(t('main.git_review.resolved_pending_opened'), 'success')
        }
        if (mode.value === 'resolve') await loadConflicts()
        errorMessage.value = aiFailureText()
        emit('changed')
      }
    } catch {
      // a transient read failure keeps the current screen; the next tick retries
    }
    schedulePoll()
  }, POLL_MS)
}

async function reloadProviders() {
  await aiProviderStore.loadForProject(props.projectId, true)
}

async function invokeAi(message: string) {
  if (busy.value) return
  busy.value = true
  aiStarting.value = true
  try {
    await aiProviderStore.ensureLoaded(props.projectId)
    const provider = aiProviderStore.selectedProviderId || null
    const { data } = await postRequest<{ ok: boolean; result?: any }>(`${apiBase.value}/ai-resolve`, {
      message: message || null,
      provider_id: provider,
      provider_pinned: !!provider,
    })
    const status = data?.result?.status
    if (status === 'start_failed') {
      showToast(t('main.git_branch_manager.merge_conflict_ai.start_failed'), 'danger')
    } else {
      showToast(t('main.git_finalize.conflict_ai_started'), 'success')
    }
  } catch (e: any) {
    showToast(resolveGitError(e, t, 'main.git_branch_manager.op_failed'), 'danger')
  } finally {
    busy.value = false
    aiStarting.value = false
    await reload()
  }
}

async function submitResolve() {
  if (busy.value) return
  busy.value = true
  let outcome = ''
  try {
    const { data } = await postRequest<{ ok: boolean; result?: any; error?: any }>(`${apiBase.value}/resolve`, {
      files: conflictFiles.value.map((f) => ({ path: f.path, content: currentFileContent(f) })),
      complete: true,
    })
    const status = data?.result?.status
    if (data?.ok === false) {
      outcome = resolveGitError(data, t, 'main.git_branch_manager.op_failed')
    } else if (status === 'resolved_pending_review') {
      showToast(t('main.git_review.resolved_pending_opened'), 'success')
      emit('changed')
    } else if (status === 'conflict') {
      const remaining = (data?.result?.remaining_conflicts || []).join(', ')
      outcome = t('main.git_finalize.resolve_remaining', { paths: remaining })
    } else {
      outcome = t('main.git_finalize.resolve_unknown_result', { status: String(status ?? '') })
    }
  } catch (e: any) {
    outcome = resolveGitError(e, t, 'main.git_branch_manager.op_failed')
  } finally {
    busy.value = false
    await reload()
    if (outcome) errorMessage.value = outcome
  }
}

async function abortMerge() {
  if (busy.value) return
  busy.value = true
  try {
    await postRequest(`${apiBase.value}/abort`, {})
    showToast(t('main.git_branch_manager.merge_aborted_toast'), 'success')
    emit('changed')
    emit('close')
  } catch (e: any) {
    errorMessage.value = resolveGitError(e, t, 'main.git_branch_manager.op_failed')
    // The review dialog has no error line of its own; never let a refused abort go unseen.
    if (mode.value === 'review') showToast(errorMessage.value, 'danger')
  } finally {
    busy.value = false
  }
}

// 0683 T0004 §2 — giving the merge up from its review: the same project route the resolver's
// [중단] uses (merge --abort → aborted → workspace released → resolver stopped).
async function abortFromReview() {
  if (busy.value) return
  const att = attempt.value
  const ok = await confirm({
    title: t('main.git_branch_manager.review_abort_confirm_title'),
    message: t('main.git_branch_manager.review_abort_confirm_message', {
      source: att?.source_branch || '', target: att?.target_branch || '',
    }),
    danger: true,
    confirmLabel: t('main.git_review.abort'),
  })
  if (!ok) return
  await abortMerge()
}

async function onReviewResolved() {
  emit('changed')
  await reload()
}

onMounted(() => {
  void aiProviderStore.ensureLoaded(props.projectId)
  void reload()
})
onBeforeUnmount(() => {
  disposed = true
  if (pollTimer) clearTimeout(pollTimer)
})
</script>
