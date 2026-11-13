<template>
  <div v-if="visible" class="gx-exceptions" data-test="git-exceptions">
    <GitConflictCard v-if="card === 'conflict' || card === 'unsupported'" :unsupported="card === 'unsupported'" />
    <GitAiResolvingCard v-else-if="card === 'ai'" />
    <GitReviewCard v-else-if="card === 'review'" />
    <GitHoldCard v-else-if="card === 'hold'" />
    <GitDirtyCard v-if="session.dirtyView.value" />
    <GitPushFailureCard v-if="session.state.pushFailure" />
    <GitPushFailureCard v-for="operation in jobCards" :key="operation.gate.recovery!.decision_id" :operation="operation" />
    <section v-if="reapproval" class="gx-card" :class="reapproval.code ? 'danger' : ''" data-test="git-reapprove-outcome" :data-code="reapproval.code ?? ''">
      <div class="gx-title">{{ t('gui.git.reapprove.title') }}</div>
      <p class="gx-desc">{{ reapproval.code ? t('gui.git.reapprove.cancel_failed', { reason: reapprovalReason }) : t('gui.git.reapprove.ready') }}</p>
      <div class="gx-actions">
        <button type="button" class="gx-btn ghost" data-test="git-reapprove-dismiss" @click="gateJobs!.reapproval.value = null">{{ t('gui.git.reapprove.dismiss') }}</button>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
// flowgate.v02.0033 — Git Exception Area (0033-D §3): only the cards the session state calls
// for — at most one card of the active Flow's group, the project's uncommitted Base changes,
// and this session's remote-apply failure. Nothing to do = nothing here (no success card).
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import GitAiResolvingCard from './GitAiResolvingCard.vue'
import GitConflictCard from './GitConflictCard.vue'
import GitDirtyCard from './GitDirtyCard.vue'
import GitHoldCard from './GitHoldCard.vue'
import GitPushFailureCard from './GitPushFailureCard.vue'
import GitReviewCard from './GitReviewCard.vue'
import { isConflictOperation, useGitGateOperations } from './gateOperations'
import { useGitPanel } from './gitSession'

const gateJobs = useGitGateOperations()
const { t, te } = useI18n()
const reapproval = computed(() => gateJobs?.reapproval.value ?? null)
const reapprovalReason = computed(() => {
  const code = reapproval.value?.code ?? ''
  const key = `gui.git.reapprove.reason.${code}`
  return te(key) ? t(key) : code
})
const session = useGitPanel().session
const card = computed(() => session.groupCard.value)
// flowgate.v02gui.0004 (0001-B §A-1): one card per conflict. While the group's conflict / AI / review card is up,
// the held decision parked on that same conflict is part of it, not a second "Git 작업 실패" card.
const conflictShown = computed(() => card.value === 'conflict' || card.value === 'ai' || card.value === 'review' || card.value === 'unsupported')
const jobCards = computed(() => (gateJobs?.operations.value ?? []).filter((operation) => !(conflictShown.value && isConflictOperation(operation.gate))))
const visible = computed(() => !!card.value || !!session.dirtyView.value || !!session.state.pushFailure || !!jobCards.value.length || !!reapproval.value)
</script>
