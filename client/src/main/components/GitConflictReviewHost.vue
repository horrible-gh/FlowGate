<template>
  <!-- flowgate.default.0668 T0004 (R2) — the review screen without a Git panel in front of
       it. An AI resolution runs on the server and the operator is free to leave; when they
       come back from anywhere (the miniplayer card, a notification) this opens the SAME
       review screen the panels use, for whatever the server says is waiting. Mounted once at
       the app root, so "검토 대기" never depends on a particular screen being alive. -->
  <GitMergeReviewDialog
    v-if="target"
    :group-id="target.group_id"
    :merge-id="target.merge_id"
    :branch="target.branch"
    :base-branch="target.base_branch"
    :providers="aiProviderStore.providers"
    :selected-provider="aiProviderStore.selectedProviderId"
    :provider-loading="aiProviderStore.loading"
    :provider-errored="!!aiProviderStore.error"
    @close="target = null"
    @update:provider="aiProviderStore.selectProvider"
  />
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { getRequest } from '@shared/api'
import { resolveGitError } from '@shared/gitErrors'
import { useAiProviderStore } from '../stores/aiProvider'
import { useToast } from './common/useToast'
import GitMergeReviewDialog from './GitMergeReviewDialog.vue'
import {
  OPEN_CONFLICT_REVIEW_EVENT,
  groupParts,
  isReviewPendingState,
  type OpenConflictReviewDetail,
} from '../composables/useConflictSession'

interface ReviewTarget {
  group_id: string
  merge_id: number
  branch: string | null
  base_branch: string | null
}

const { t } = useI18n()
const { showToast } = useToast()
const aiProviderStore = useAiProviderStore()
const target = ref<ReviewTarget | null>(null)

/**
 * Resolve what is waiting from the server, never from the request: the finalize state is
 * the one poll that carries `merge_id` + `review_state` for both a merge and (0668) a TR
 * conflict. Nothing waiting → say so instead of opening an empty screen.
 */
async function open(detail: OpenConflictReviewDetail): Promise<void> {
  const groupId = detail?.group_id
  if (!groupId) return
  try {
    const { data } = await getRequest<{ ok: boolean; state?: any }>(
      `/api/v1/groups/${groupId}/git/finalize`,
    )
    const state = data?.state
    const mergeId = state?.merge_id
    if (state?.status !== 'conflict' || mergeId == null || !isReviewPendingState(state?.review_state)) {
      showToast(t('main.git_review.no_review_pending'), 'warning')
      return
    }
    void aiProviderStore.ensureLoaded(groupParts(groupId).project)
    target.value = {
      group_id: groupId,
      merge_id: Number(mergeId),
      branch: state?.branch ?? null,
      base_branch: state?.base_branch ?? null,
    }
  } catch (e: any) {
    showToast(resolveGitError(e, t, 'main.git_finalize.failed'), 'danger')
  }
}

function onOpen(event: Event) {
  void open((event as CustomEvent<OpenConflictReviewDetail>).detail)
}

onMounted(() => window.addEventListener(OPEN_CONFLICT_REVIEW_EVENT, onOpen))
onBeforeUnmount(() => window.removeEventListener(OPEN_CONFLICT_REVIEW_EVENT, onOpen))

defineExpose({ open })
</script>
