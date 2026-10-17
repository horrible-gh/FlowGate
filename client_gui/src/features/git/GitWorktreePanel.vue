<template>
  <section class="gw-panel" data-test="git-worktree" :data-state="areaState">
    <p v-if="areaState === 'loading'" class="gw-summary is-muted" data-test="git-worktree-loading">{{ t('gui.git.worktree.loading') }}</p>
    <p v-else-if="areaState === 'failed'" class="gw-line is-error" data-test="git-worktree-error">
      <span>{{ t(`gui.git.worktree.read_${wt.state.statusError ?? 'failed'}`) }}</span>
      <button type="button" class="gw-link" data-test="git-worktree-retry" @click="wt.refresh()">{{ t('gui.git.worktree.retry') }}</button>
    </p>
    <template v-else>
      <p v-if="wt.state.statusError === 'network'" class="gw-line is-error" data-test="git-worktree-stale">
        {{ t('gui.git.worktree.read_network') }}
      </p>
      <p v-else-if="wt.state.statusError === 'forbidden'" class="gw-line is-error" data-test="git-worktree-forbidden">
        {{ t('gui.git.worktree.read_forbidden') }}
      </p>
      <p v-if="areaState === 'unavailable'" class="gw-summary is-muted" data-test="git-worktree-unavailable">{{ t('gui.git.worktree.unavailable') }}</p>
      <p v-else-if="status" class="gw-overview" data-test="git-worktree-summary">
        <span>{{ t('gui.git.worktree.overview.changed') }} <strong data-test="git-worktree-summary-changed">{{ status.counts.changed ?? 0 }}</strong></span>
        <i class="gw-overview-sep" aria-hidden="true" />
        <span>{{ t('gui.git.worktree.overview.staged') }} <strong data-test="git-worktree-summary-staged">{{ status.counts.staged ?? 0 }}</strong></span>
        <i class="gw-overview-sep" aria-hidden="true" />
        <span :title="aheadTip">{{ t('gui.git.worktree.overview.ahead') }} <strong data-test="git-worktree-summary-merge">{{ ahead ?? '—' }}</strong></span>
        <span v-if="stateChip" class="gw-chip" data-test="git-worktree-chip">{{ stateChip }}</span>
      </p>
      <nav class="gw-entries" data-test="git-worktree-entries" :aria-label="t('gui.git.worktree.nav.aria')">
        <button
          v-for="entry in entries"
          :key="entry.tab"
          type="button"
          class="gw-entry"
          data-test="git-worktree-entry"
          :data-tab="entry.tab"
          :aria-pressed="windowOpen && tab === entry.tab ? 'true' : 'false'"
          @click="openWindow(entry.tab)"
        >
          <svg class="gw-entry-icon" viewBox="0 0 24 24" aria-hidden="true"><path :d="ICONS[entry.tab]" /></svg>
          <span class="gw-entry-title">{{ t(`gui.git.worktree.nav.${entry.tab}`) }}</span>
          <span class="gw-entry-count" data-test="git-worktree-entry-count">{{ entry.count }}</span>
          <span class="gw-entry-arrow" aria-hidden="true">›</span>
        </button>
      </nav>
    </template>
    <GitWorktreeWindow
      ref="windowRef"
      v-model:tab="tab"
      v-model:minimized="windowMinimized"
      :open="windowOpen"
      @close="closeWindow"
    />
  </section>
</template>

<script setup lang="ts">
// flowgate.v02.0066 — the worktree area of the Git tab (0066 D#2 §6-1). The work branch, Base and
// refresh stay the GitHeader's.
//
// flowgate.v02.0067 T#1 — the tab keeps only a summary and four entries (0067-R §2, v27): the
// `변경 / Stage / 미반영` counts and `파일 관리` / `Base 반영` / `Diff` / `상태`, four equal rows.
// The changed-files list, the commit line, the merge target and the panes moved into one Git
// modeless (GitWorktreeWindow) every entry opens on its own tab. Without a workspace (W2) the
// entries stay; the window shows what is left there (Fetch and the follow-up Push).
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import GitWorktreeWindow from './GitWorktreeWindow.vue'
import { useGitWorktree } from './gitWorktreeSession'
import type { GitWorktreeTab } from './types'

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

// The E8 judgment of the chosen target (0066 D#2 §6-1): never a fixed "Base", never the
// display-only committed range; unknown while it is void or being checked.
const ahead = computed(() => {
  if (wt.state.mergeLoad === 'loading') return null
  const merge = wt.state.merge
  if (!merge || !wt.state.mergeTarget) return null
  return merge.aheadOfTarget
})
const aheadTip = computed(() => wt.state.mergeTarget ?? undefined)

const stateChip = computed(() => {
  const value = status.value?.gitState.status
  if (value === 'merging') return t('gui.git.worktree.chip_merging')
  if (value === 'conflict') return t('gui.git.worktree.chip_conflict')
  return null
})

// v27 icons: folder, merge, two columns, three lines.
const ICONS: Record<GitWorktreeTab, string> = {
  files: 'M4 4h9l3 3h4v13H4zM8 12h9M8 16h6',
  merge: 'M6 4v10a5 5 0 0 0 5 5h7M12 12l6 7-6 4M18 4v10',
  diff: 'M7 3v18M17 3v18M3 8h8M13 15h8',
  status: 'M5 6h14M5 12h14M5 18h14',
}

// `파일 관리` the changed files, `Base 반영` the commits not yet in the target; `Diff` and `상태`
// carry no count (0067-R §2-2).
const entries = computed(() => {
  // Without a workspace (W2) there is nothing to count: no summary, no numbers.
  const workspace = areaState.value === 'ready'
  const changed = workspace ? status.value?.counts.changed ?? null : null
  const pending = workspace ? ahead.value : null
  return [
    { tab: 'files' as const, count: changed === null ? '' : t('gui.git.worktree.nav.files_count', { n: changed }) },
    { tab: 'merge' as const, count: pending === null ? '' : t('gui.git.worktree.nav.merge_count', { n: pending }) },
    { tab: 'diff' as const, count: '' },
    { tab: 'status' as const, count: '' },
  ]
})

// One window for the four entries: an entry opens it on its tab, or brings it back there — restored
// and in front of the other modeless windows, which the entry (outside the window) does not do by
// itself.
const windowOpen = ref(false)
const windowMinimized = ref(false)
const tab = ref<GitWorktreeTab>('files')
const windowRef = ref<InstanceType<typeof GitWorktreeWindow> | null>(null)

function openWindow(next: GitWorktreeTab): void {
  const wasOpen = windowOpen.value
  tab.value = next
  windowMinimized.value = false
  windowOpen.value = true
  if (wasOpen) windowRef.value?.bringToFront()
}

function closeWindow(): void {
  windowOpen.value = false
  windowMinimized.value = false
}

// Another project / Flow is another worktree: the window closes, as its dialogs do (§3-2-2 ①).
const context = computed(() => {
  const ticket = wt.ticket()
  return ticket ? `${ticket.projectId}\n${ticket.groupId}` : null
})
watch(context, (next, previous) => {
  if (next !== previous) closeWindow()
})
</script>
