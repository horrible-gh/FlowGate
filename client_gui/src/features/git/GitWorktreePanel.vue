<template>
  <section class="gw-panel" data-test="git-worktree" :data-state="areaState">
    <p v-if="areaState === 'loading'" class="gw-summary is-muted" data-test="git-worktree-loading">{{ t('gui.git.worktree.loading') }}</p>
    <p v-else-if="areaState === 'failed'" class="gw-line is-error" data-test="git-worktree-error">
      <span>{{ t(`gui.git.worktree.read_${wt.state.statusError ?? 'failed'}`) }}</span>
      <button type="button" class="gw-link" data-test="git-worktree-retry" @click="wt.refresh()">{{ t('gui.git.worktree.retry') }}</button>
    </p>
    <template v-else-if="areaState === 'unavailable'">
      <p class="gw-summary is-muted" data-test="git-worktree-unavailable">{{ t('gui.git.worktree.unavailable') }}</p>
      <GitApplyAccordion />
    </template>
    <template v-else-if="status">
      <p v-if="wt.state.statusError === 'network'" class="gw-line is-error" data-test="git-worktree-stale">
        {{ t('gui.git.worktree.read_network') }}
      </p>
      <p v-else-if="wt.state.statusError === 'forbidden'" class="gw-line is-error" data-test="git-worktree-forbidden">
        {{ t('gui.git.worktree.read_forbidden') }}
      </p>
      <p class="gw-summary" data-test="git-worktree-summary">
        <span>{{ t('gui.git.worktree.summary_changed', { n: status.counts.changed ?? 0 }) }}</span>
        <template v-if="mergeLine">
          <span class="gw-dot" aria-hidden="true">·</span>
          <span data-test="git-worktree-summary-merge">{{ mergeLine }}</span>
        </template>
        <span v-if="stateChip" class="gw-chip" data-test="git-worktree-chip">{{ stateChip }}</span>
      </p>
      <GitChangesAccordion />
      <GitCommitBar />
      <GitApplyAccordion />
    </template>
  </section>
</template>

<script setup lang="ts">
// flowgate.v02.0066 — the worktree area of the Git tab (0066 D#2 §6-1): the summary line, the
// changed-files accordion (folded at first), the commit line that is always in view and the folded
// Base apply accordion below it (T#7). Without a workspace (W2) only the Base apply accordion
// stays, for Fetch and the follow-up Push. The work branch, Base and refresh stay the GitHeader's.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import GitApplyAccordion from './GitApplyAccordion.vue'
import GitChangesAccordion from './GitChangesAccordion.vue'
import GitCommitBar from './GitCommitBar.vue'
import { useGitWorktree } from './gitWorktreeSession'

const { t } = useI18n()
const { worktree: wt } = useGitWorktree()

const status = computed(() => wt.state.status)

// W1 reading / a read failure with nothing to show / W2 no workspace / the area itself.
const areaState = computed(() => {
  const s = status.value
  if (!s) return wt.state.statusError ? 'failed' : 'loading'
  if (!s.worktreeAvailable) return 'unavailable'
  return 'ready'
})

// "{선택 대상} 미반영 M" from the E8 judgment of the chosen target; "확인 중" while it is void
// (0066 D#2 §6-1). Never a fixed "Base", never the display-only committed range.
const mergeLine = computed(() => {
  const target = wt.state.mergeTarget
  if (wt.state.mergeLoad === 'loading') return t('gui.git.worktree.summary_checking', { target: target ?? '' }).trim()
  const merge = wt.state.merge
  if (!merge || !target || merge.aheadOfTarget === null) return null
  return t('gui.git.worktree.summary_merge', { target, n: merge.aheadOfTarget })
})

const stateChip = computed(() => {
  const value = status.value?.gitState.status
  if (value === 'merging') return t('gui.git.worktree.chip_merging')
  if (value === 'conflict') return t('gui.git.worktree.chip_conflict')
  return null
})
</script>
