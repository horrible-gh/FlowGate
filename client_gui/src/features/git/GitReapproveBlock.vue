<template>
  <div v-if="operation" class="gx-reapprove" data-test="git-reapprove">
    <p class="gx-desc" data-test="git-reapprove-pin">
      <template v-if="frozen">{{ t('gui.git.reapprove.pinned', { sha: short(frozen) }) }}</template>
      <template v-else>{{ t('gui.git.reapprove.pinned_unknown') }}</template>
      <template v-if="head">
        · {{ t('gui.git.reapprove.head', { sha: short(head) }) }}
        <strong v-if="moved" class="gx-reapprove-moved" data-test="git-reapprove-moved">{{ t('gui.git.reapprove.moved') }}</strong>
      </template>
    </p>
    <p class="gx-reason" data-test="git-reapprove-blocked">{{ t('gui.git.reapprove.blocked', { reason: reasonText }) }}</p>
    <div class="gx-actions">
      <button type="button" class="gx-btn" :disabled="locked" data-test="git-reapprove-start" @click="ask">
        {{ t('gui.git.reapprove.action') }}
      </button>
    </div>
    <p v-if="locked && lockedText" class="gx-reason" data-test="git-reapprove-locked">{{ lockedText }}</p>
  </div>
</template>

<script setup lang="ts">
// flowgate.v02gui.0004 — 0001-B §B: the held approval stays pinned to the commit it froze (that pin is never moved
// here). This block says which commit that is, whether the group HEAD has moved past it and why the approval cannot
// go on, and offers "새 커밋으로 다시 승인": close the conflict review (existing merge abort) → cancel the parked
// decision (existing gate-decision cancel, only when the server says cancelable) → the person presses [승인] on the
// check card, which pins the current HEAD. A refusal is shown as the server's code; nothing is reported as done
// that the server did not do.
import { computed, inject } from 'vue'
import { useI18n } from 'vue-i18n'
import { frozenShaOf, useGitGateOperations } from './gateOperations'
import { useGitPanel } from './gitSession'
import { GIT_WORKTREE_CONTEXT } from './gitWorktreeSession'

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
const moved = computed(() => !!frozen.value && !!head.value && !head.value.startsWith(frozen.value.slice(0, 7)) && !frozen.value.startsWith(head.value.slice(0, 7)))
const reasonText = computed(() => {
  const code = operation.value?.gate.recovery?.error_code ?? operation.value?.gate.job?.last_error_code ?? 'merge_conflict_open'
  const key = `gui.git.reapprove.reason.${code}`
  return te(key) ? t(key) : code
})
const locked = computed(() => !session.canAct.value || !!gateJobs?.busy.value || gateJobs?.readOnly() !== false)
const lockedText = computed(() => (session.state.readOnly || gateJobs?.readOnly() ? t('gui.git.conflict.blocked.read_only') : t('gui.git.conflict.blocked.busy')))

function ask(): void {
  const current = operation.value
  if (!current || locked.value) return
  const decisionId = current.gate.recovery!.decision_id
  ctx.confirm({
    title: t('gui.git.reapprove.confirm_title'),
    message: t('gui.git.reapprove.confirm_message'),
    confirmLabel: t('gui.git.reapprove.action'),
    run: () => void run(decisionId),
  })
}

async function run(decisionId: string): Promise<void> {
  if (!gateJobs) return
  // ① close the conflict review; a refusal is the card's own notice.
  if (!(await session.abortForReapproval())) return
  // ② cancel the parked decision only when the server now allows it.
  const code = await gateJobs.cancelAfterAbort(decisionId)
  gateJobs.reapproval.value = { decisionId, code }
  await session.refresh()
}
</script>
