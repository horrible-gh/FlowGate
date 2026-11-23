<template>
  <div v-if="visible" class="gx-exceptions" data-test="git-exceptions">
    <GitConflictCard v-if="!executionActive && (card === 'conflict' || card === 'unsupported')" :unsupported="card === 'unsupported'" />
    <GitAiResolvingCard v-else-if="!executionActive && card === 'ai'" />
    <GitReviewCard v-else-if="!executionActive && card === 'review'" />
    <GitHoldCard v-if="card === 'hold' && !worktree.gateOpen.value" />
    <section v-for="operation in autoFailures" :key="operation.nodeKey" class="gx-card danger" data-test="git-auto-approval-failure">
      <div class="gx-title" :title="operation.gate.auto_approval_error ?? undefined">{{ autoFailureLabel }}</div>
      <p class="gx-reason">{{ operation.gate.auto_approval_error }}</p>
      <button type="button" class="gx-btn" :title="t('gui.git.reapprove.goto_gate')" :aria-label="t('gui.git.reapprove.goto_gate')" @click="openGateCard(session.state.flow?.groupId, operation.nodeKey)">→</button>
    </section>
    <GitDirtyCard v-if="session.dirtyView.value" />
    <GitPushFailureCard v-if="session.state.pushFailure" />
    <GitPushFailureCard v-for="operation in jobCards" :key="operation.gate.recovery!.decision_id" :operation="operation" />
    <section v-if="reapproval" class="gx-card" :class="reapproval.code ? 'danger' : ''" data-test="git-reapprove-outcome" :data-code="reapproval.code ?? ''">
      <div class="gx-title">{{ t('gui.git.reapprove.title') }}</div>
      <p class="gx-desc">{{ reapproval.code ? t('gui.git.reapprove.cancel_failed', { reason: reapprovalReason }) : t('gui.git.reapprove.ready') }}</p>
      <div class="gx-actions">
        <!-- (task 1·4): the next press is on the check card — take the person there. -->
        <button v-if="!reapproval.code && reapproval.nodeKey" type="button" class="gx-btn primary" data-test="git-reapprove-goto" @click="openGateCard(session.state.flow?.groupId, reapproval.nodeKey)">{{ t('gui.git.reapprove.goto_gate') }}</button>
        <button type="button" class="gx-btn ghost" data-test="git-reapprove-dismiss" @click="gateJobs!.reapproval.value = null">{{ t('gui.git.reapprove.dismiss') }}</button>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
// Git Exception Area: only the cards the session state calls
// for — at most one card of the active Flow's group, the project's uncommitted Base changes,
// and this session's remote-apply failure. Nothing to do = nothing here (no success card).
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import GitAiResolvingCard from './GitAiResolvingCard.vue'
import GitConflictCard from './GitConflictCard.vue'
import GitDirtyCard from './GitDirtyCard.vue'
import GitHoldCard from './GitHoldCard.vue'
import { useGitWorktree } from './gitWorktreeSession'
import GitPushFailureCard from './GitPushFailureCard.vue'
import GitReviewCard from './GitReviewCard.vue'
import { isConflictOperation, openGateCard, useGitGateOperations } from './gateOperations'
import { useGitPanel } from './gitSession'

defineProps<{ executionActive?: boolean }>()
const gateJobs = useGitGateOperations()
const { worktree } = useGitWorktree()
const { t, te, locale } = useI18n()
const autoFailures = computed(() => gateJobs?.autoFailures?.value ?? [])
const autoFailureLabel = computed(() => locale.value.startsWith('ko') ? '자동 승인 실패' : 'Automatic approval stopped')
const reapproval = computed(() => gateJobs?.reapproval.value ?? null)
const reapprovalReason = computed(() => {
  const code = reapproval.value?.code ?? ''
  const key = `gui.git.reapprove.reason.${code}`
  return te(key) ? t(key) : code
})
const session = useGitPanel().session
const card = computed(() => session.groupCard.value)
// one card per conflict. While the group's conflict / AI / review card is up,
// the held decision parked on that same conflict is part of it, not a second "Git 작업 실패" card.
const conflictShown = computed(() => card.value === 'conflict' || card.value === 'ai' || card.value === 'review' || card.value === 'unsupported')
const jobCards = computed(() => (gateJobs?.operations.value ?? []).filter((operation) => !(conflictShown.value && isConflictOperation(operation.gate))))
const visible = computed(() => !!autoFailures.value.length || !!card.value || !!session.dirtyView.value || !!session.state.pushFailure || !!jobCards.value.length || !!reapproval.value)
</script>
