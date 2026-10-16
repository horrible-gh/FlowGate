<template>
  <div class="gw-info" data-test="git-apply-info" :data-pane="pane">
    <div class="gw-info-head">
      <b>{{ pane === 'status' ? t('gui.git.apply.status') : t('gui.git.apply.diff') }}</b>
      <button
        type="button"
        class="gw-link"
        data-test="git-apply-info-close"
        :aria-label="t('gui.git.apply.info_close')"
        @click="pane && wt.toggleInfo(pane)"
      >×</button>
    </div>

    <template v-if="pane === 'status'">
      <p v-if="statusLoading" class="gw-line is-muted" data-test="git-apply-info-loading">{{ t('gui.git.apply.info_loading') }}</p>
      <p v-else-if="wt.state.mergeLoad === 'error'" class="gw-line is-error" data-test="git-apply-info-failed">{{ t('gui.git.apply.info_failed') }}</p>
      <p v-else-if="!merge || !target" class="gw-line is-muted" data-test="git-apply-info-none">{{ t('gui.git.apply.diff_no_target') }}</p>
      <dl v-else class="gw-info-list" data-test="git-apply-info-status">
        <dt>{{ t('gui.git.apply.info.target') }}</dt>
        <dd data-test="git-apply-info-target">{{ targetIsDefault ? t('gui.git.apply.target_default', { name: target }) : target }}</dd>
        <dt>{{ t('gui.git.apply.info.ahead') }}</dt>
        <dd data-test="git-apply-info-ahead">{{ merge.aheadOfTarget ?? '—' }}</dd>
        <dt>{{ t('gui.git.apply.info.merge_needed') }}</dt>
        <dd>{{ merge.mergeNeeded ? t('gui.git.apply.info.yes') : t('gui.git.apply.info.no') }}</dd>
        <template v-if="merge.recovery">
          <dt>{{ t('gui.git.apply.info.recovery') }}</dt>
          <dd data-test="git-apply-info-recovery">{{ t('gui.git.apply.info.yes') }}</dd>
        </template>
        <dt>{{ t('gui.git.apply.info.uncommitted') }}</dt>
        <dd data-test="git-apply-info-uncommitted">{{ uncommittedText }}</dd>
        <template v-if="graphText">
          <dt>{{ t('gui.git.apply.info.graph') }}</dt>
          <dd data-test="git-apply-info-graph">{{ graphText }}</dd>
        </template>
        <template v-if="merge.approval.acDocId">
          <dt>{{ t('gui.git.apply.info.approval') }}</dt>
          <dd data-test="git-apply-info-approval">{{ approvalText }}</dd>
        </template>
        <template v-if="merge.floorError">
          <dt>{{ t('gui.git.apply.info.floor') }}</dt>
          <dd data-test="git-apply-info-floor" :title="merge.floorError.code">{{ t('gui.git.apply.info.floor_error') }}</dd>
        </template>
        <template v-if="baseRemote">
          <dt>{{ t('gui.git.apply.info.base_remote') }}</dt>
          <dd data-test="git-apply-info-base">{{ baseRemote }}</dd>
        </template>
      </dl>
      <ul v-if="merge && target && baseMerges.length" class="gw-info-merges" data-test="git-apply-info-unpushed">
        <li
          v-for="row in baseMerges"
          :key="row.mergeCommit"
          :title="row.groupId ? undefined : t('gui.git.apply.info.unmapped_tip')"
          :data-unmapped="row.groupId ? 'false' : 'true'"
        >
          <code>{{ row.mergeCommit }}</code>
          <span>{{ row.groupId ?? t('gui.git.apply.info.unmapped') }}</span>
        </li>
      </ul>
    </template>

    <template v-else>
      <p v-if="!wt.state.mergeTarget" class="gw-line is-muted" data-test="git-apply-diff-none">{{ t('gui.git.apply.diff_no_target') }}</p>
      <p v-else-if="!list && wt.state.targetListLoad === 'error'" class="gw-line is-error" data-test="git-apply-diff-failed">
        <span>{{ t('gui.git.apply.diff_failed') }}</span>
        <button type="button" class="gw-link" data-test="git-apply-diff-retry" @click="wt.refresh()">{{ t('gui.git.worktree.retry') }}</button>
      </p>
      <p v-else-if="!list" class="gw-line is-muted" data-test="git-apply-diff-loading">{{ t('gui.git.apply.info_loading') }}</p>
      <template v-else>
        <p class="gw-line is-muted" data-test="git-apply-diff-title">
          <span>{{ t('gui.git.apply.diff_title', { target: list.target, n: list.changes.length }) }}</span>
          <span v-if="wt.state.targetListLoad === 'loading'" data-test="git-apply-diff-refreshing">{{ t('gui.git.apply.diff_refreshing') }}</span>
        </p>
        <p v-if="!list.changes.length" class="gw-line is-muted" data-test="git-apply-diff-empty">{{ t('gui.git.apply.diff_empty') }}</p>
        <ul v-else class="gw-info-files" data-test="git-apply-diff-list">
          <li v-for="change in shown" :key="change.path">
            <button
              type="button"
              class="gw-info-file"
              data-test="git-apply-diff-row"
              :data-path="change.path"
              :title="change.path"
              @click="openTargetDiff(change.path, change.status, list.target)"
            >
              <span class="gw-mark">{{ change.status }}</span>
              <span class="gw-row-name">{{ change.path }}</span>
            </button>
          </li>
        </ul>
        <button v-if="rest > 0" type="button" class="gw-more" data-test="git-apply-diff-more" @click="wt.showMoreTargetList()">
          {{ t('gui.git.apply.diff_more', { n: rest, page: WORKTREE_FOLDER_PAGE }) }}
        </button>
      </template>
    </template>
  </div>
</template>

<script setup lang="ts">
// flowgate.v02.0066 T#7 — the `상태` / `Diff` panes of the Base apply body (0066 D#2 §3-4(7)).
// `상태` is the applied E8 judgment of the chosen target only (a voided one shows "읽는 중", never
// the old target's values): commits ahead, merge need, recovery, the uncommitted summary (hidden
// entries as a count), Graph and approval as information that never blocks, the floor note and,
// for the project Base, git/status remote state with the unmapped merges. `Diff` is the
// `/changes?base=<target>` list of the target it was read for; a row opens the Center Diff with
// that target as compareBase (⑦). Neither pane decides a merge.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useGitWorktree } from './gitWorktreeSession'
import { WORKTREE_FOLDER_PAGE } from './worktreeList'

const { t } = useI18n()
const { worktree: wt, openTargetDiff } = useGitWorktree()

const pane = computed(() => wt.state.infoPane)
const target = computed(() => wt.state.mergeTarget)
const merge = computed(() => (wt.state.mergeLoad === 'ready' ? wt.state.merge : null))
const statusLoading = computed(() => wt.state.mergeLoad === 'loading' || wt.state.mergeLoad === 'idle')
const targetIsDefault = computed(() => wt.state.mergeTargets.some((x) => x.name === target.value && x.isWorkBase))

const COUNT_KEYS: Record<string, string> = {
  staged: 'gui.git.apply.info.count.staged',
  unstaged: 'gui.git.apply.info.count.unstaged',
  new: 'gui.git.apply.info.count.new',
  hidden: 'gui.git.apply.info.count.hidden',
  managed: 'gui.git.apply.info.count.managed',
  unverified: 'gui.git.apply.info.count.unverified',
  unsupported: 'gui.git.apply.info.count.unsupported',
  excluded_new_untracked: 'gui.git.apply.info.count.excluded_new_untracked',
}

// Blocking total, then each non-zero class (hidden ones as a number only — no path anywhere).
const uncommittedText = computed(() => {
  const u = merge.value?.uncommitted
  if (!u) return '—'
  const parts = Object.entries(COUNT_KEYS)
    .filter(([key]) => (u.counts[key] ?? 0) > 0)
    .map(([key, i18nKey]) => t(i18nKey, { n: u.counts[key] }))
  const head = u.blocking ? t('gui.git.apply.info.blocking', { n: u.blocking }) : t('gui.git.apply.info.uncommitted_none')
  return [head, ...parts].join(' · ')
})

// An open approval node is information: it never blocks a merge (D#1 Q1).
const graphText = computed(() => {
  const g = merge.value?.graph
  if (!g?.runPresent) return null
  return g.gateOpen ? t('gui.git.apply.info.graph_gate') : t('gui.git.apply.info.graph_run')
})

const approvalText = computed(() => {
  const a = merge.value?.approval
  if (!a?.acDocId) return null
  return t('gui.git.apply.info.approval_value', { doc: a.acDocId, status: a.status ?? '—' })
})

// The project Base only: git/status ahead / behind and the unpushed merges (§3-4(7)).
const isBase = computed(() => {
  const base = wt.projectGit.value?.baseBranch ?? null
  return !!target.value && (merge.value?.preview?.isProjectBase === true || (!!base && base === target.value))
})

const baseRemote = computed(() => {
  const status = wt.projectGit.value
  if (!isBase.value || !status) return null
  const parts: string[] = []
  if (typeof status.aheadCount === 'number' || typeof status.behindCount === 'number') {
    parts.push(t('gui.git.apply.info.ahead_behind', { ahead: status.aheadCount ?? '—', behind: status.behindCount ?? '—' }))
  }
  const unpushed = status.unpushed
  if (unpushed) {
    if (unpushed.remoteBranchMissing) parts.push(t('gui.git.apply.info.remote_missing'))
    if (!unpushed.measured) parts.push(t('gui.git.apply.info.unmeasured'))
    else parts.push(t('gui.git.apply.info.unpushed', { n: unpushed.count ?? 0 }))
  }
  return parts.length ? parts.join(' · ') : null
})

const baseMerges = computed(() => (isBase.value ? wt.projectGit.value?.unpushed?.merges ?? [] : []))

const list = computed(() => {
  const l = wt.state.targetList
  // Only the chosen target's list is ever drawn (⑦).
  return l && l.target === wt.state.mergeTarget ? l : null
})
const shown = computed(() => list.value?.changes.slice(0, wt.state.targetListLimit) ?? [])
const rest = computed(() => Math.max(0, (list.value?.changes.length ?? 0) - shown.value.length))
</script>
