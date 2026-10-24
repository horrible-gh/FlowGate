<template>
  <div v-if="visible" class="gx-exceptions" data-test="git-exceptions">
    <GitConflictCard v-if="card === 'conflict' || card === 'unsupported'" :unsupported="card === 'unsupported'" />
    <GitAiResolvingCard v-else-if="card === 'ai'" />
    <GitReviewCard v-else-if="card === 'review'" />
    <GitHoldCard v-else-if="card === 'hold'" />
    <GitDirtyCard v-if="session.dirtyView.value" />
    <GitPushFailureCard v-if="session.state.pushFailure" />
    <GitPushFailureCard v-for="operation in gateJobs?.operations.value ?? []" :key="operation.gate.recovery!.decision_id" :operation="operation" />
  </div>
</template>

<script setup lang="ts">
// flowgate.v02.0033 — Git Exception Area (0033-D §3): only the cards the session state calls
// for — at most one card of the active Flow's group, the project's uncommitted Base changes,
// and this session's remote-apply failure. Nothing to do = nothing here (no success card).
import { computed } from 'vue'
import GitAiResolvingCard from './GitAiResolvingCard.vue'
import GitConflictCard from './GitConflictCard.vue'
import GitDirtyCard from './GitDirtyCard.vue'
import GitHoldCard from './GitHoldCard.vue'
import GitPushFailureCard from './GitPushFailureCard.vue'
import GitReviewCard from './GitReviewCard.vue'
import { useGitGateOperations } from './gateOperations'
import { useGitPanel } from './gitSession'

const gateJobs = useGitGateOperations()
const session = useGitPanel().session
const card = computed(() => session.groupCard.value)
const visible = computed(() => !!card.value || !!session.dirtyView.value || !!session.state.pushFailure || !!gateJobs?.operations.value.length)
</script>
