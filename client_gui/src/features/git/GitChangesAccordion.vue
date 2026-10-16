<template>
  <div class="gw-acc" data-test="git-changes" :data-open="open ? 'true' : 'false'">
    <div class="gw-acc-head">
      <button
        type="button"
        class="gw-acc-toggle"
        data-test="git-changes-toggle"
        :aria-expanded="open ? 'true' : 'false'"
        :disabled="empty"
        @click="wt.toggleFiles()"
      >
        <span class="gw-caret" aria-hidden="true">{{ open ? '▾' : '▸' }}</span>
        <b v-if="empty" data-test="git-changes-empty">{{ t('gui.git.worktree.no_changes') }}</b>
        <template v-else>
          <b>{{ t('gui.git.worktree.files_title') }}</b>
          <span class="gw-num" data-test="git-changes-count">{{ counts.changed ?? 0 }}</span>
          <span v-if="counts.staged" class="gw-staged" data-test="git-changes-staged">{{ t('gui.git.worktree.staged_count', { n: counts.staged }) }}</span>
        </template>
      </button>
      <button
        v-if="hidden > 0"
        type="button"
        class="gw-lock"
        data-test="git-changes-lock"
        :aria-expanded="wt.state.lockOpen ? 'true' : 'false'"
        :title="t('gui.git.worktree.lock_title')"
        @click="wt.toggleLock()"
      >🔒{{ hidden }}</button>
      <button
        v-if="quick === 'stage'"
        type="button"
        class="gw-quick"
        data-test="git-changes-stage-all"
        :disabled="reasons.stage.disabled"
        :title="reasonTip('stage', 'gui.git.worktree.all_tip')"
        @click="wt.stageAll()"
      >{{ t('gui.git.worktree.stage_all') }}</button>
      <button
        v-else-if="quick === 'unstage'"
        type="button"
        class="gw-quick"
        data-test="git-changes-unstage-all"
        :disabled="reasons.unstageAll.disabled"
        :title="reasonTip('unstageAll', 'gui.git.worktree.unstage_all_tip')"
        @click="wt.unstageAll()"
      >{{ t('gui.git.worktree.unstage_all') }}</button>
      <template v-if="open">
        <button
          type="button"
          class="gw-iconbtn"
          :class="{ 'is-on': wt.state.searchOpen }"
          data-test="git-changes-search-toggle"
          :aria-label="t('gui.git.worktree.search_toggle')"
          :title="t('gui.git.worktree.search_toggle')"
          @click="wt.toggleSearch()"
        >⌕</button>
        <button
          type="button"
          class="gw-iconbtn"
          :class="{ 'is-on': wt.state.menuOpen }"
          data-test="git-changes-menu-toggle"
          :aria-label="t('gui.git.worktree.menu_toggle')"
          :title="t('gui.git.worktree.menu_toggle')"
          @click="wt.toggleMenu()"
        >⋯</button>
      </template>
    </div>

    <div v-if="wt.state.lockOpen && hidden > 0" class="gw-lockbox" data-test="git-changes-lockbox">
      <p>{{ t('gui.git.worktree.lock_hidden', { n: hidden }) }}</p>
      <p v-if="hiddenStaged > 0" data-test="git-changes-lock-staged">{{ t('gui.git.worktree.lock_staged', { n: hiddenStaged }) }}</p>
      <button
        type="button"
        class="gw-act"
        data-test="git-changes-lock-unstage-all"
        :disabled="reasons.unstageAll.disabled"
        :title="reasonTip('unstageAll', 'gui.git.worktree.unstage_all_tip')"
        @click="wt.unstageAll()"
      >{{ t('gui.git.worktree.unstage_all_full') }}</button>
    </div>

    <p v-if="notice" class="gw-line" :class="{ 'is-error': notice.tone === 'error' }" data-test="git-changes-notice">
      <span>{{ t(`gui.git.worktree.notice.${notice.code}`, { ...(notice.params ?? {}) }) }}</span>
      <button type="button" class="gw-link" :aria-label="t('gui.git.worktree.dismiss')" @click="wt.dismissNotice('files')">×</button>
    </p>

    <div v-if="open" class="gw-acc-body">
      <div v-if="wt.state.searchOpen" class="gw-search" data-test="git-changes-search">
        <input
          type="search"
          class="gw-input"
          data-test="git-changes-search-input"
          :placeholder="t('gui.git.worktree.search_placeholder')"
          :aria-label="t('gui.git.worktree.search_placeholder')"
          :value="wt.state.search"
          @input="wt.setSearch(($event.target as HTMLInputElement).value)"
        />
        <select
          class="gw-select"
          data-test="git-changes-filter"
          :aria-label="t('gui.git.worktree.filter_label')"
          :value="wt.state.filter"
          @change="onFilter(($event.target as HTMLSelectElement).value)"
        >
          <option v-for="f in WORKTREE_FILTERS" :key="f" :value="f">{{ t(`gui.git.worktree.filter.${filterKey(f)}`) }}</option>
        </select>
      </div>
      <div v-if="wt.state.menuOpen" class="gw-menu" data-test="git-changes-menu">
        <button type="button" class="gw-act" data-test="git-folders-open" @click="wt.setFolderMode('open')">{{ t('gui.git.worktree.folders_open') }}</button>
        <button type="button" class="gw-act" data-test="git-folders-close" @click="wt.setFolderMode('closed')">{{ t('gui.git.worktree.folders_close') }}</button>
      </div>
      <div class="gw-bulk" data-test="git-changes-bulk">
        <button type="button" class="gw-act" data-test="git-select-all" :disabled="!selectTargets.length" @click="wt.toggleSelectAll()">
          {{ allSelected ? t('gui.git.worktree.select_none') : t('gui.git.worktree.select_all') }}
        </button>
        <span v-if="selectedCount" class="gw-bulk-count" data-test="git-selected-count">{{ t('gui.git.worktree.selected', { n: selectedCount }) }}</span>
        <button
          v-if="stageable.length"
          type="button"
          class="gw-act primary"
          data-test="git-stage-selected"
          :disabled="reasons.stage.disabled"
          :title="reasonTip('stage', null)"
          @click="wt.stageSelected()"
        >{{ t('gui.git.worktree.stage_selected') }}</button>
        <button
          v-if="unstageable.length"
          type="button"
          class="gw-act"
          data-test="git-unstage-selected"
          :disabled="reasons.unstage.disabled"
          :title="reasonTip('unstage', null)"
          @click="wt.unstageSelected()"
        >{{ t('gui.git.worktree.unstage_selected') }}</button>
        <button v-if="selectedCount" type="button" class="gw-link" data-test="git-selection-clear" @click="wt.clearSelection()">
          {{ t('gui.git.worktree.selection_clear') }}
        </button>
      </div>
      <div class="gw-viewport" data-test="git-changes-viewport">
        <GitChangeSection v-for="section in wt.sections.value" :key="section.key" :section="section" />
      </div>
      <p v-if="hidden > 0" class="gw-count-line" data-test="git-changes-hidden-line">
        <span>{{ t('gui.git.worktree.hidden_line', { n: hidden }) }}</span>
        <button
          type="button"
          class="gw-link"
          data-test="git-changes-hidden-unstage-all"
          :disabled="reasons.unstageAll.disabled"
          :title="reasonTip('unstageAll', 'gui.git.worktree.unstage_all_tip')"
          @click="wt.unstageAll()"
        >{{ t('gui.git.worktree.unstage_all_full') }}</button>
      </p>
      <p v-if="counts.excludedNewUntracked" class="gw-count-line" data-test="git-changes-artifacts-line">
        {{ t('gui.git.worktree.artifacts_line', { n: counts.excludedNewUntracked }) }}
      </p>
      <p v-if="status && !status.ownership.available" class="gw-count-line" data-test="git-changes-ownership-line">
        {{ t('gui.git.worktree.ownership_line') }}
      </p>
    </div>
  </div>
</template>

<script setup lang="ts">
// flowgate.v02.0066 — the changed-files accordion (0066 D#2 §3-6, §6-2). Folded at first; its
// header carries `변경 파일 N`, `Stage M`, the 🔒K hidden-files box and a quick full Stage / full
// unstage, all visible while folded. Opened, it adds search / filter, the folder menu, the
// selection bar and the two sections in a viewport of limited height (the commit line below never
// moves). Every number is the E1 answer; every button only asks the session.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import GitChangeSection from './GitChangeSection.vue'
import { useGitWorktree } from './gitWorktreeSession'
import type { WorktreeStatus } from './types'
import { isWorktreeEmpty, selectAllTargets, stageTargets, unstageTargets, WORKTREE_FILTERS, type WorktreeFilter } from './worktreeList'
import { reasonMessage, type WorktreeButton } from './worktreeReasons'

const { t } = useI18n()
const { worktree: wt } = useGitWorktree()

const status = computed(() => wt.state.status as WorktreeStatus | null)
const counts = computed(() => status.value?.counts ?? { changed: 0, staged: 0, hidden: 0, excludedNewUntracked: 0 })
const empty = computed(() => isWorktreeEmpty(status.value))
const open = computed(() => wt.state.filesOpen && !empty.value)
const hidden = computed(() => status.value?.counts.hidden ?? 0)
const hiddenStaged = computed(() => wt.hiddenStaged.value)
const reasons = computed(() => wt.reasons.value)
const notice = computed(() => wt.state.notices.files ?? null)

// + 전체 Stage while a listed row can still be staged; otherwise − 전체 해제 while anything is
// staged — hidden entries included, so a hidden-only Stage keeps its way out (§3-6(1)).
const quick = computed(() => {
  const s = status.value
  if (!s) return null
  if (s.files.some((f) => f.canStage)) return 'stage'
  if ((s.counts.staged ?? 0) > 0) return 'unstage'
  return null
})

const selectTargets = computed(() => selectAllTargets(status.value, wt.state.search, wt.state.filter))
const allSelected = computed(
  () => selectTargets.value.length > 0 && selectTargets.value.every((p) => wt.state.selected.includes(p)),
)
const selectedCount = computed(() => wt.state.selected.length)
const stageable = computed(() => stageTargets(wt.state.selected, status.value))
const unstageable = computed(() => unstageTargets(wt.state.selected, status.value))

const FILTER_KEYS: Record<WorktreeFilter, string> = { all: 'all', M: 'modified', A: 'added', '?': 'new', D: 'deleted', locked: 'locked' }
const filterKey = (filter: WorktreeFilter) => FILTER_KEYS[filter]

function onFilter(value: string): void {
  const filter = WORKTREE_FILTERS.find((f) => f === value)
  if (filter) wt.setFilter(filter)
}

// The disabled reason as the button's tooltip, else its own help text.
function reasonTip(button: WorktreeButton, help: string | null): string | undefined {
  const message = reasonMessage(reasons.value[button])
  if (message) return t(message.key, message.params)
  return help ? t(help) : undefined
}
</script>
