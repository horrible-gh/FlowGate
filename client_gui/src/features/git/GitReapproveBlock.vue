<template>
  <div v-if="operation" class="gx-reapprove" data-test="git-reapprove">
    <p v-if="moved" class="gx-desc" data-test="git-reapprove-pin">
      {{ t('gui.git.reapprove.new_commit', { from: short(frozen!), to: short(head!) }) }}
    </p>
    <p v-if="!conflictReason" class="gx-reason" data-test="git-reapprove-blocked">{{ t('gui.git.reapprove.blocked', { reason: reasonText }) }}</p>
    <div class="gx-actions">
      <button type="button" class="gx-btn" :disabled="locked" :title="t('gui.git.reapprove.confirm_message')" data-test="git-reapprove-start" @click="ask">
        {{ actionText }}
      </button>
    </div>
    <p v-if="locked && lockedText && lockedText !== blockedText" class="gx-reason" data-test="git-reapprove-locked">{{ lockedText }}</p>
  </div>
</template>

<script setup lang="ts">
// §B: the held approval stays pinned to the commit it froze (that pin is never moved
// here). This block says which commit that is, whether the group HEAD has moved past it and why the approval cannot
// go on, and offers "새 커밋으로 다시 승인": close the conflict review (existing merge abort) → cancel the parked
// decision (existing gate-decision cancel, only when the server says cancelable) → the person presses [승인] on the
// check card, which pins the current HEAD. A refusal is shown as the server's code; nothing is reported as done
// that the server did not do.
import { computed, inject } from 'vue'
import { useI18n } from 'vue-i18n'
import { abortAndCancelApproval, frozenShaOf, useGitGateOperations } from './gateOperations'
import { useGitPanel } from './gitSession'
import { GIT_WORKTREE_CONTEXT } from './gitWorktreeSession'

defineProps<{ blockedText?: string | null }>()

const { t, te } = useI18n()
const ctx = useGitPanel()
const session = ctx.session
const gateJobs = useGitGateOperations()
// The group HEAD comes from the worktree area when the panel has one (no worktree source = no HEAD line).
const worktree = inject(GIT_WORKTREE_CONTEXT, null)?.worktree ?? null

const operation = computed(() => gateJobs?.conflictOperation.value ?? null)
const frozen = computed(() => frozenShaOf(operation.value?.gate))
const head = computed(() => worktree?.state.status?.head ?? null)
const short = (sha: string) => sha.slice(0, 8)
const moved = computed(() => !!frozen.value && !!head.value && !head.value.startsWith(frozen.value) && !frozen.value.startsWith(head.value))
const actionText = computed(() => t(moved.value ? 'gui.git.reapprove.action' : frozen.value && head.value ? 'gui.git.reapprove.restart' : 'gui.git.reapprove.title'))
const reasonCode = computed(() => operation.value?.gate.recovery?.error_code ?? operation.value?.gate.job?.last_error_code ?? 'merge_conflict_open')
const conflictReason = computed(() => ['merge_conflict_open', 'gate_conflict', 'merge_conflict'].includes(reasonCode.value))
const reasonText = computed(() => {
  const code = reasonCode.value
  const key = `gui.git.reapprove.reason.${code}`
  return te(key) ? t(key) : code
})
const locked = computed(() => !session.canAct.value || !!gateJobs?.busy.value || gateJobs?.readOnly() !== false)
const lockedText = computed(() => {
  if (session.state.readOnly || gateJobs?.readOnly()) return t('gui.git.conflict.blocked.read_only')
  if (session.state.forbidden) return t('gui.git.conflict.blocked.forbidden')
  return t('gui.git.conflict.blocked.busy')
})

function ask(): void {
  const current = operation.value
  if (!current || locked.value) return
  ctx.confirm({
    title: actionText.value,
    message: t('gui.git.reapprove.confirm_message'),
    confirmLabel: actionText.value,
    run: () => void run(),
  })
}

async function run(): Promise<void> {
  // ① close the conflict review (a refusal is the card's own notice) → ② cancel the parked decision only when the server
  // now allows it → the guidance. The same flow as [병합 중단] (flowgate.v02test.0052 0008).
  await abortAndCancelApproval(session, gateJobs, operation.value)
}
</script>
