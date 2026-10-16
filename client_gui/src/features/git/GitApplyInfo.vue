<template>
  <div class="gw-info" data-test="git-apply-info" data-pane="diff">
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
  </div>
</template>

<script setup lang="ts">
// flowgate.v02.0066 T#7 — the `Diff` pane of the Base apply body (0066 D#2 §3-4(7)): the
// `/changes?base=<target>` list of the target it was read for; a row opens the Center Diff with
// that target as compareBase (⑦). It never decides a merge.
// flowgate.v02.0067 T#1 — it is the `Diff` tab body of the Git modeless until T#4 moves the Diff
// into the window; the `상태` pane became the window's `상태` tab (GitWorktreeStatus).
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useGitWorktree } from './gitWorktreeSession'
import { WORKTREE_FOLDER_PAGE } from './worktreeList'

const { t } = useI18n()
const { worktree: wt, openTargetDiff } = useGitWorktree()

const list = computed(() => {
  const l = wt.state.targetList
  // Only the chosen target's list is ever drawn (⑦).
  return l && l.target === wt.state.mergeTarget ? l : null
})
const shown = computed(() => list.value?.changes.slice(0, wt.state.targetListLimit) ?? [])
const rest = computed(() => Math.max(0, (list.value?.changes.length ?? 0) - shown.value.length))
</script>
