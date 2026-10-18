<template>
  <div class="gw-files" data-test="git-changes">
    <div class="gw-files-stats" data-test="git-changes-stats">
      <span>{{ t('gui.git.worktree.overview.changed') }}<strong data-test="git-changes-count">{{ counts.changed ?? 0 }}</strong></span>
      <i class="gw-overview-sep" aria-hidden="true" />
      <span>{{ t('gui.git.worktree.overview.staged') }}<strong data-test="git-changes-staged">{{ counts.staged ?? 0 }}</strong></span>
    </div>

    <p v-if="notice" class="gw-line" :class="{ 'is-error': notice.tone === 'error' }" data-test="git-changes-notice">
      <span>{{ t(`gui.git.worktree.notice.${notice.code}`, { ...(notice.params ?? {}) }) }}</span>
      <button type="button" class="gw-link" :aria-label="t('gui.git.worktree.dismiss')" @click="wt.dismissNotice('files')">×</button>
    </p>

    <template v-if="!empty">
      <div class="gw-files-search" data-test="git-changes-search">
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
      <!-- v27 `gw-dialog-bulk`: 전체 선택 / 선택 해제 → Stage → Stage 해제 → 전체 Stage / 전체 해제, side by
           side on the left; only what applies is there, each in its own colour; the count is aside. -->
      <div class="gw-files-bulk" data-test="git-changes-bulk">
        <button type="button" class="gw-act gw-bulk-reset" data-test="git-select-all" :disabled="!selectTargets.length" @click="wt.toggleSelectAll()">
          {{ allSelected ? t('gui.git.worktree.select_none') : t('gui.git.worktree.select_all') }}
        </button>
        <button
          v-if="stageable.length"
          type="button"
          class="gw-act gw-bulk-stage"
          data-test="git-stage-selected"
          :disabled="reasons.stage.disabled"
          :title="reasonTip('stage', null)"
          @click="wt.stageSelected()"
        >{{ t('gui.git.worktree.stage_selected') }}</button>
        <button
          v-if="unstageable.length"
          type="button"
          class="gw-act gw-bulk-unstage"
          data-test="git-unstage-selected"
          :disabled="reasons.unstage.disabled"
          :title="reasonTip('unstage', null)"
          @click="wt.unstageSelected()"
        >{{ t('gui.git.worktree.unstage_selected') }}</button>
        <button
          v-if="quick === 'stage'"
          type="button"
          class="gw-act gw-bulk-all"
          data-test="git-changes-stage-all"
          :disabled="reasons.stage.disabled"
          :title="reasonTip('stage', 'gui.git.worktree.all_tip')"
          @click="wt.stageAll()"
        >{{ t('gui.git.worktree.stage_all') }}</button>
        <button
          v-else-if="quick === 'unstage'"
          type="button"
          class="gw-act gw-bulk-all"
          data-test="git-changes-unstage-all"
          :disabled="reasons.unstageAll.disabled"
          :title="reasonTip('unstageAll', 'gui.git.worktree.unstage_all_tip')"
          @click="wt.unstageAll()"
        >{{ t('gui.git.worktree.unstage_all') }}</button>
        <span v-if="selectedCount" class="gw-files-count" data-test="git-selected-count">{{ t('gui.git.worktree.selected', { n: selectedCount }) }}</span>
      </div>
    </template>

    <div class="gw-viewport" data-test="git-changes-viewport">
      <p v-if="empty" class="gw-files-empty" data-test="git-changes-empty">{{ t('gui.git.worktree.no_changes') }}</p>
      <GitChangeSection v-for="section in wt.sections.value" :key="section.key" :section="section" />
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
// flowgate.v02.0066 — the changed-files list; flowgate.v02.0067 T#2 — the `파일 관리` tab body of the
// Git modeless (0067-R §4, v27 `modalFilesHTML`). No fold of its own any more: the `변경 / Stage`
// numbers, search and change-type filter, the selection bar, then the sections in a viewport that
// takes the rest of the window and scrolls inside it — 100+ files never push the commit line below
// out of the window. Folders fold and select on their own (GitChangeFolder); every number is the E1
// answer and every button only asks the session.
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
const hidden = computed(() => status.value?.counts.hidden ?? 0)
const reasons = computed(() => wt.reasons.value)
const notice = computed(() => wt.state.notices.files ?? null)

// 전체 Stage while a listed row can still be staged; otherwise 전체 해제 while anything is staged —
// hidden entries included, so a hidden-only Stage keeps its way out (0066 §3-6(1)).
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
