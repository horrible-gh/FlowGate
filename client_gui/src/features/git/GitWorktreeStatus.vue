<template>
  <div class="gw-status" data-test="git-status">
    <div class="gw-status-metrics">
      <div class="gw-status-metric">
        <b data-test="git-status-changed">{{ counts ? counts.changed ?? 0 : '—' }}</b>
        <span>{{ t('gui.git.worktree.status.changed') }}</span>
      </div>
      <div class="gw-status-metric">
        <b data-test="git-status-staged">{{ counts ? counts.staged ?? 0 : '—' }}</b>
        <span>{{ t('gui.git.worktree.status.staged') }}</span>
      </div>
      <div class="gw-status-metric">
        <b data-test="git-status-ahead">{{ ahead ?? '—' }}</b>
        <span>{{ t('gui.git.worktree.status.ahead') }}</span>
      </div>
    </div>
    <dl class="gw-status-rows">
      <div class="gw-status-row">
        <dt>{{ t('gui.git.worktree.status.worktree') }}</dt>
        <dd data-test="git-status-worktree">{{ status?.branch || '—' }}</dd>
      </div>
      <div class="gw-status-row">
        <dt>{{ t('gui.git.worktree.status.base') }}</dt>
        <dd data-test="git-status-base">{{ base || '—' }}</dd>
      </div>
      <div class="gw-status-row">
        <dt>{{ t('gui.git.worktree.status.target') }}</dt>
        <dd data-test="git-status-target">{{ targetText }}</dd>
      </div>
      <div class="gw-status-row">
        <dt>{{ t('gui.git.worktree.status.state') }}</dt>
        <dd data-test="git-status-state">{{ stateText }}</dd>
      </div>
    </dl>
    <p v-if="wt.state.mergeLoad === 'error'" class="gw-line is-error" data-test="git-status-failed">{{ t('gui.git.apply.info_failed') }}</p>
  </div>
</template>

<script setup lang="ts">
// flowgate.v02.0067 T#1 — the `상태` tab of the Git modeless (0067-R §7, v27 `Git 상태`): three
// numbers (changed files, Stage, commits not in the target) and four rows (Worktree, its base,
// the merge target, the state). Nothing more is added here. Every value is one the session already
// read for the Base apply `상태` pane (E1 counts / branch, the E8 judgment of the chosen target, the
// 0033 git/status Base); the state row is that judgment's one word (WorktreeApplySummary). A voided
// judgment shows "—", never the old target's values.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useGitWorktree } from './gitWorktreeSession'
import { applySummaryMessage } from './worktreeReasons'

const { t } = useI18n()
const { worktree: wt } = useGitWorktree()

const status = computed(() => wt.state.status)
const counts = computed(() => (status.value?.worktreeAvailable ? status.value.counts : null))
const merge = computed(() => (wt.state.mergeLoad === 'ready' ? wt.state.merge : null))
const ahead = computed(() => (merge.value && wt.state.mergeTarget ? merge.value.aheadOfTarget : null))
const base = computed(() => wt.projectGit.value?.baseBranch ?? null)

const targetText = computed(() => {
  const target = wt.state.mergeTarget
  if (!target) return t('gui.git.apply.target_none')
  const isDefault = wt.state.mergeTargets.some((x) => x.name === target && x.isWorkBase)
  return isDefault ? t('gui.git.apply.target_default', { name: target }) : target
})

const stateText = computed(() => {
  const message = applySummaryMessage(wt.applySummary.value)
  return message ? t(message.key, message.params) : '—'
})
</script>
