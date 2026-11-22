<template>
  <section v-if="wt.gateOpen.value" class="gw-panel" data-test="git-worktree" :data-state="areaState">
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
      <GitQuickActions v-if="areaState === 'ready'" @details="openWindow" />
      <nav class="gq-footer">
        <button v-if="areaState === 'unavailable'" data-test="git-worktree-entry" data-tab="merge" :title="t('gui.git.worktree.nav.merge')" :aria-label="t('gui.git.worktree.nav.merge')" @click="openWindow('merge')">⑂</button>
        <button type="button" data-test="git-worktree-entry" data-tab="diff" :title="t('gui.git.worktree.nav.diff')" :aria-label="t('gui.git.worktree.nav.diff')" @click="openWindow('diff')">◫</button>
        <button type="button" data-test="git-worktree-entry" data-tab="status" :title="t('gui.git.worktree.nav.status')" :aria-label="t('gui.git.worktree.nav.status')" @click="openWindow('status')">☷</button>
      </nav>
    </template>
    <GitWorktreeWindow
      v-if="wt.gateOpen.value"
      ref="windowRef"
      v-model:tab="tab"
      v-model:minimized="windowMinimized"
      :open="windowOpen"
      @close="closeWindow"
    />
  </section>
</template>

<script setup lang="ts">
// the worktree area of the Git tab. The work branch, Base and
// refresh stay the GitHeader's.
//
// the tab keeps only a summary and four entries (R, v27): the
// `변경 / Stage / 미반영` counts and `파일 관리` / `Base 반영` / `Diff` / `상태`, four equal rows.
// The changed-files list, the commit line, the merge target and the panes moved into one Git
// modeless (GitWorktreeWindow) every entry opens on its own tab. Without a workspace (W2) the
// entries stay; the window shows what is left there (Fetch and the follow-up Push).
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import GitWorktreeWindow from './GitWorktreeWindow.vue'
import GitQuickActions from './GitQuickActions.vue'
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

// One window for the four entries: an entry opens it on its tab, or brings it back there — restored
// and in front of the other modeless windows, which the entry (outside the window) does not do by
// itself.
const windowOpen = ref(false)
const windowMinimized = ref(false)
const tab = ref<GitWorktreeTab>('files')
const windowRef = ref<InstanceType<typeof GitWorktreeWindow> | null>(null)

function openWindow(next: GitWorktreeTab): void {
  if (!wt.gateOpen.value) return
  const wasOpen = windowOpen.value
  tab.value = next
  windowMinimized.value = false
  windowOpen.value = true
  if (wasOpen) windowRef.value?.bringToFront()
}

function closeWindow(): void {
  windowOpen.value = false
  windowMinimized.value = false
  tab.value = 'files'
}

// Another project / Flow is another worktree: the window closes, as its dialogs do (§3-2-2 ①).
const context = computed(() => {
  const ticket = wt.ticket()
  return ticket ? `${ticket.projectId}\n${ticket.groupId}` : null
})
// Reset synchronously even when a gate closes and reopens within one Vue render batch.
watch(context, (next, previous) => {
  if (next !== previous) closeWindow()
}, { flush: 'sync' })
</script>

<style scoped>
.gq-footer { display:flex; justify-content:flex-end; gap:6px; border-top:1px solid #22344d; padding-top:8px }
.gq-footer button { width:28px; height:28px; border:1px solid #30445f; border-radius:5px; background:transparent; color:#adc1d9; cursor:pointer }
</style>
