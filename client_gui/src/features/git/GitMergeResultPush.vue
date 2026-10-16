<template>
  <div
    v-if="mode"
    class="gw-push-later"
    :class="{ 'is-highlight': highlighted }"
    data-test="git-result-push"
    :data-mode="mode"
    :data-highlight="highlighted ? 'true' : 'false'"
  >
    <span v-if="mode === 'one' && row" class="gw-grow" data-test="git-result-push-label">{{ t('gui.git.apply.push_pending_one', { target: row.target.targetBranch }) }}</span>
    <template v-else-if="mode === 'many'">
      <span>{{ t('gui.git.apply.push_pending_label') }}</span>
      <select
        class="gw-select gw-grow"
        data-test="git-result-push-choice"
        :aria-label="t('gui.git.apply.push_choice_aria')"
        :value="row?.mergeId ?? ''"
        @change="wt.setPushChoice(Number(($event.target as HTMLSelectElement).value))"
      >
        <option v-for="candidate in candidates" :key="candidate.mergeId" :value="candidate.mergeId">{{ choiceLabel(candidate) }}</option>
      </select>
    </template>
    <span v-else class="gw-grow">{{ t('gui.git.apply.push_pending_label') }}</span>
    <button
      type="button"
      class="gw-act"
      data-test="git-result-push-button"
      :disabled="reasons.push.disabled"
      :title="pushReason ?? undefined"
      @click="onPush"
    >{{ t('gui.git.apply.push') }}</button>
  </div>
  <p v-if="mode === 'blocked' && pushReason" class="gw-line is-muted" data-test="git-result-push-reason">{{ pushReason }}</p>
  <p v-if="notice" class="gw-line" :class="{ 'is-error': notice.tone === 'error' }" data-test="git-result-push-notice">
    <span>{{ t(`gui.git.worktree.notice.${notice.code}`, { ...(notice.params ?? {}) }) }}</span>
    <button type="button" class="gw-link" :aria-label="t('gui.git.worktree.dismiss')" @click="wt.dismissNotice('push')">×</button>
  </p>
</template>

<script setup lang="ts">
// flowgate.v02.0066 T#7 — the follow-up Push part (0066 D#2 §3-4(5)), shown only while a recorded
// merge is not on its remote. One candidate: its target and one Push; two or more: a small choice
// (target · short merge commit · time), the newest first. A Push sends that record's merge id and
// commit only — never a branch name, never the worktree, never /git/push. No candidate but an
// unmeasured or blocked row: a disabled Push and its reason. Already published or moved rows: nothing.
// flowgate.v02.0067 T#3 — only a merge that really waits for its remote shows it (R §5-6,
// followUpPushMode): a row behind the group's Git job keeps the disabled Push and its reason; an
// unmeasured remote shows nothing (the Fetch icon above measures it again).
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useGitWorktree } from './gitWorktreeSession'
import type { MergeResultRow, MergeResultsView } from './types'
import { followUpPushMode, reasonMessage } from './worktreeReasons'

const { t } = useI18n()
const { worktree: wt, confirm } = useGitWorktree()

const reasons = computed(() => wt.reasons.value)
const candidates = computed(() => wt.pushCandidates.value as MergeResultRow[])
const row = computed(() => wt.pushRow.value as MergeResultRow | null)
const notice = computed(() => wt.state.notices.push ?? null)

const mode = computed(() => followUpPushMode(wt.state.results as MergeResultsView | null, candidates.value.length))

const highlighted = computed(() => wt.state.highlightMergeId !== null && row.value?.mergeId === wt.state.highlightMergeId)

const pushReason = computed(() => {
  const message = reasonMessage(reasons.value.push)
  return message ? t(message.key, message.params) : null
})

function choiceLabel(candidate: MergeResultRow): string {
  const at = candidate.completedAt ?? ''
  const time = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(at) ? `${at.slice(5, 10)} ${at.slice(11, 16)}` : at
  return t('gui.git.apply.push_choice', {
    target: candidate.target.targetBranch,
    commit: (candidate.mergeCommit ?? '').slice(0, 7),
    time,
  })
}

// The dialog names the target; a Base push also says what else of the Base goes with it.
function onPush(): void {
  const snapshot = wt.pushSnapshot()
  if (!snapshot) return
  const lines = [t('gui.git.apply.push_confirm', { target: snapshot.target })]
  if (snapshot.isProjectBase) {
    const unpushed = wt.projectGit.value?.unpushed
    if (unpushed && !unpushed.measured) lines.push(t('gui.git.apply.push_confirm_base_unmeasured'))
    else if (unpushed && (unpushed.count ?? 0) > 1) lines.push(t('gui.git.apply.push_confirm_base_more', { n: (unpushed.count ?? 0) - 1 }))
  }
  confirm(
    {
      title: t('gui.git.apply.push_title'),
      message: lines.join(' '),
      confirmLabel: t('gui.git.apply.push'),
      run: () => void wt.pushResult(snapshot),
    },
    snapshot.ticket,
  )
}
</script>
