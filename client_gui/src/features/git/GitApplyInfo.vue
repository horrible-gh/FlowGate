<template>
  <div class="gw-diff-list" data-test="git-apply-info" data-pane="diff">
    <p v-if="noTarget" class="gw-line is-muted" data-test="git-apply-diff-none">{{ t('gui.git.apply.diff_no_target') }}</p>
    <p v-else-if="!list && wt.state.targetListLoad === 'error'" class="gw-line is-error" data-test="git-apply-diff-failed">
      <span>{{ t('gui.git.apply.diff_failed') }}</span>
      <button type="button" class="gw-link" data-test="git-apply-diff-retry" @click="wt.refresh()">{{ t('gui.git.worktree.retry') }}</button>
    </p>
    <p v-else-if="!list" class="gw-line is-muted" data-test="git-apply-diff-loading">{{ t('gui.git.apply.info_loading') }}</p>
    <template v-else>
      <p class="gw-files-stats" data-test="git-apply-diff-title">
        <span>{{ t('gui.git.apply.diff_title', { target: list.target, n: list.changes.length }) }}</span>
        <span v-if="wt.state.targetListLoad === 'loading'" class="gw-files-count" data-test="git-apply-diff-refreshing">{{ t('gui.git.apply.diff_refreshing') }}</span>
      </p>
      <p v-if="!list.changes.length" class="gw-files-empty" data-test="git-apply-diff-empty">{{ t('gui.git.apply.diff_empty') }}</p>
      <template v-else>
        <input
          v-model="search"
          type="search"
          class="gw-input gw-diff-search"
          data-test="git-apply-diff-search"
          :placeholder="t('gui.git.worktree.search_placeholder')"
          :aria-label="t('gui.git.apply.diff_search')"
        />
        <div class="gw-viewport gw-diff-files" data-test="git-apply-diff-list">
          <button
            v-for="change in view.rows"
            :key="change.path"
            type="button"
            class="gw-diff-file"
            data-test="git-apply-diff-row"
            :data-path="change.path"
            :title="change.path"
            @click="openTargetDiff(change.path, change.status, list.target)"
          >
            <span class="gw-mark" :class="`mk-${markClass(change.status)}`">{{ change.status }}</span>
            <span class="gw-row-name">{{ change.path }}</span>
          </button>
          <p v-if="!view.matched" class="gw-files-empty" data-test="git-apply-diff-no-match">{{ t('gui.git.worktree.no_match') }}</p>
        </div>
        <div class="gw-diff-pages" data-test="git-apply-diff-pages">
          <span data-test="git-apply-diff-range">{{ t('gui.git.apply.diff_range', { from: view.from, to: view.to, n: view.matched }) }}</span>
          <span class="gw-diff-pages-btns">
            <button type="button" class="gw-act" data-test="git-apply-diff-prev" :disabled="view.page === 0" @click="page = view.page - 1">
              {{ t('gui.git.apply.diff_prev') }}
            </button>
            <button type="button" class="gw-act" data-test="git-apply-diff-next" :disabled="view.page >= view.pages - 1" @click="page = view.page + 1">
              {{ t('gui.git.apply.diff_next') }}
            </button>
          </span>
        </div>
      </template>
    </template>
  </div>
</template>

<script setup lang="ts">
// flowgate.v02.0066 T#7 — the `Diff` pane of the Base apply body (0066 D#2 §3-4(7)): the
// `/changes?base=<target>` list of the target it was read for; a row opens the Center Diff with
// that target as compareBase (⑦). It never decides a merge.
// flowgate.v02.0067 T#4 — the list of the Git modeless `Diff` tab (0067-R §6-1, v27 `compareBase()`):
// the comparison files come first, with a path search and pages (`이전` / `다음`) instead of `더 보기`.
// A row opens its diff as the tab's detail; the window keeps this list mounted meanwhile, so
// `목록으로` comes back to the same search and page. Search and page are screen state only.
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useGitWorktree } from './gitWorktreeSession'
import { diffListPage } from './worktreeList'

const { t } = useI18n()
const { worktree: wt, openTargetDiff } = useGitWorktree()

const list = computed(() => {
  const l = wt.state.targetList
  // Only the chosen target's list is ever drawn (⑦).
  return l && l.target === wt.state.mergeTarget ? l : null
})

// No target is known only once E8 has answered (or failed); before that the list is still coming.
const noTarget = computed(() => !wt.state.mergeTarget && (wt.state.mergeSeq > 0 || wt.state.mergeLoad === 'error'))

const search = ref('')
const page = ref(0)
// A new search starts on its first page; another target is another list.
watch(search, () => (page.value = 0))
watch(
  () => wt.state.mergeTarget,
  () => (page.value = 0),
)
// The page is kept inside the pages the list has now (a re-read may shorten it).
const view = computed(() => diffListPage(list.value?.changes ?? [], search.value, page.value))

const markClass = (letter: string) => (letter === '?' ? 'u' : letter.toLowerCase() || 'none')
</script>
