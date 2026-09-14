<template>
  <!-- flowgate.default.0325 TR0007 rev1 — the screen that [변경사항 열기] opens.
       R0001 was "승인 후 머지 할까 말까 고민되는데 관련 소스가 잘 됐는지 볼 길이 없다"
       and the sidebar summary only answers with "몇 파일 · 몇 줄". This screen is where
       the actual reading happens — it carries over verbatim the layout of mockup
       (TR0003) candidate ③ "그룹 변경사항 전체 화면": a file list on the left (status
       badge · per-file +/− · path search · status filter), a unified/split diff on the
       right, navigation between files, and a return to [승인 화면으로].
       N0004 §1 rejected "승인 화면 본문에 항상 펼쳐진 diff 카드", so this opens as a
       modal shell like the document editor — closing it explicitly returns to the
       original spot (the approval screen stays as it was). -->
  <!-- flowgate.default.0560 T0022 §2.7 (5순위) - migrated onto the common dialog layer.
       D0008 §6 maps this instance to `workflow-large` (a sheet surface whose body stays
       feature-owned). Two things are deliberate here:

       - `:open="true"` is a literal. This component has never had a `visible`/`open` prop:
         `DocInfoPanel.vue` mounts it behind `v-if="changesDialogOpen && props.groupId"`, so
         "open" and "mounted" have always been the same fact. Adding a prop now would give the
         same state two owners; the shell is told the truth instead, and unmounting still runs
         `force_cleanup` through `DialogShell`'s `onBeforeUnmount` (L0009 §2 "teardown").
       - the branch/file-count/line summary moves into `DialogHeader`'s `subtitle` slot rather
         than being packed into `title`. It is one secondary line under the title - exactly
         what the slot renders (`.fg-dialog-header__subtitle`) - and keeping it out of `title`
         leaves `aria-labelledby` pointing at the title text alone.

       `closeOnBackdrop` is not overridden: `workflow-large` defaults to `false` and the old
       `.modal-bg` carried no backdrop handler. -->
  <DialogShell
    ref="shellRef"
    :open="true"
    variant="workflow-large"
    surface-class="gcd-changes-dialog"
    @request-close="emit('close')"
  >
    <template #header>
      <DialogHeader :title="t('main.group_changes.title')" icon="git-diff" @close="onHeaderClose">
        <template #subtitle>
          <span class="gcd-mono">{{ branch || '-' }}</span>
          ↔
          <span class="gcd-mono">{{ baseBranch || 'main' }}</span>
          <span class="gcd-dot">·</span>
          {{ t('main.group_changes.file_count', { n: changes.length }) }}
          <span v-if="totals.known" class="gcd-hd-lines">
            <span class="gcd-add">+{{ totals.insertions.toLocaleString() }}</span>
            <span class="gcd-del">−{{ totals.deletions.toLocaleString() }}</span>
          </span>
        </template>
      </DialogHeader>
    </template>

    <template #default>
        <div class="gcd-modal-body">
          <!-- 0382 NR0003 proposal 3: exclude artifacts from the list, but **don't treat them as
               nonexistent**. 261 of them never showing on any screen while still being approved
               and merged was the core of that incident. Keep this outside the list branch so it's
               visible even when there are zero changes. -->
          <section v-if="artifactPaths.length" class="gcd-artifacts">
            <button
              type="button"
              class="gcd-artifacts-toggle"
              :aria-expanded="artifactsOpen"
              @click="artifactsOpen = !artifactsOpen"
            >
              <AppIcon :name="artifactsOpen ? 'caret-down' : 'caret-right'" />
              {{ t('main.group_changes.tool_artifacts', { n: artifactPaths.length }) }}
            </button>
            <span class="gcd-artifacts-note">{{ t('main.group_changes.tool_artifacts_note') }}</span>
            <ul v-if="artifactsOpen" class="gcd-artifacts-list">
              <li v-for="path in artifactPaths" :key="path" class="gcd-mono">{{ path }}</li>
            </ul>
          </section>

          <div v-if="!changes.length" class="gcd-blank">
            <AppIcon name="check-circle" />
            <span>{{ t('main.doc_info_panel.changes_empty') }}</span>
          </div>

          <template v-else>
            <div class="gcd-toolbar">
              <label class="gcd-search">
                <AppIcon name="magnifying-glass" />
                <input
                  v-model="query"
                  type="search"
                  :placeholder="t('main.group_changes.search_placeholder')"
                  :aria-label="t('main.group_changes.search_placeholder')"
                />
              </label>
              <div class="gcd-chips">
                <button
                  v-for="chip in filterChips"
                  :key="chip.key"
                  type="button"
                  class="gcd-chip"
                  :class="{ active: filter === chip.key }"
                  @click="filter = chip.key"
                >
                  {{ chip.label }} <span class="gcd-chip-count">{{ chip.count }}</span>
                </button>
              </div>
              <div class="gcd-tb-spacer"></div>
              <div class="gcd-seg" role="group" :aria-label="t('main.group_changes.view_mode')">
                <button type="button" :class="{ active: viewMode === 'unified' }" @click="viewMode = 'unified'">
                  {{ t('main.group_changes.view_unified') }}
                </button>
                <button type="button" :class="{ active: viewMode === 'split' }" @click="viewMode = 'split'">
                  {{ t('main.group_changes.view_split') }}
                </button>
              </div>
            </div>

            <div class="gcd-bd">
              <aside class="gcd-filelist" :aria-label="t('main.group_changes.file_list')">
                <p v-if="!visibleFiles.length" class="gcd-nomatch">{{ t('main.group_changes.no_match') }}</p>
                <button
                  v-for="file in visibleFiles"
                  :key="file.path"
                  type="button"
                  class="gcd-file"
                  :class="{ active: file.path === selectedPath }"
                  @click="select(file.path)"
                >
                  <span class="gcd-file-top">
                    <span class="gcd-badge" :class="`gcd-badge-${statusKind(file.status)}`">{{ statusBadge(file.status) }}</span>
                    <span class="gcd-file-name">{{ baseName(file.path) }}</span>
                  </span>
                  <span class="gcd-file-dir">{{ dirName(file.path) }}</span>
                  <span class="gcd-file-stats">
                    <template v-if="hasLineStats(file)">
                      <span class="gcd-add">+{{ file.insertions ?? 0 }}</span>
                      <span class="gcd-del">−{{ file.deletions ?? 0 }}</span>
                      <span class="gcd-bar" aria-hidden="true">
                        <i v-for="(cell, idx) in barCells(file)" :key="idx" :class="cell"></i>
                      </span>
                    </template>
                    <span v-else class="gcd-file-nostat">{{ t('main.group_changes.stats_unknown') }}</span>
                  </span>
                </button>
              </aside>

              <section class="gcd-diffwrap">
                <div class="gcd-diff-hd">
                  <span class="gcd-diff-path" :title="selectedPath || ''">{{ selectedPath || '-' }}</span>
                  <span v-if="selectedChange && hasLineStats(selectedChange)" class="gcd-diff-lines">
                    <span class="gcd-add">+{{ selectedChange.insertions ?? 0 }}</span>
                    <span class="gcd-del">−{{ selectedChange.deletions ?? 0 }}</span>
                  </span>
                  <div class="gcd-diff-nav">
                    <button type="button" :disabled="!canMove(-1)" @click="move(-1)">
                      <AppIcon name="caret-up" /> {{ t('main.group_changes.prev_file') }}
                    </button>
                    <button type="button" :disabled="!canMove(1)" @click="move(1)">
                      <AppIcon name="caret-down" /> {{ t('main.group_changes.next_file') }}
                    </button>
                  </div>
                </div>

                <div v-if="diffLoading" class="gcd-diff-state">
                  <AppIcon name="spinner" spin /> {{ t('common.loading') }}
                </div>
                <div v-else-if="diffError" class="gcd-diff-state gcd-diff-error">
                  <span>{{ t('main.group_changes.diff_failed') }}</span>
                  <button type="button" class="gcd-retry" @click="loadDiff(selectedPath)">
                    <AppIcon name="arrows-clockwise" /> {{ t('main.group_changes.retry') }}
                  </button>
                </div>
                <div v-else-if="diffBinary" class="gcd-diff-state">{{ t('main.group_changes.binary') }}</div>
                <div v-else-if="diff && !hasDiffChanges" class="gcd-diff-state">{{ t('main.group_changes.no_diff') }}</div>
                <template v-else-if="diff">
                  <p v-if="diff.status === 'A'" class="gcd-notice">{{ t('main.group_changes.added_note') }}</p>
                  <p v-if="diffRows.approximate" class="gcd-notice gcd-notice-warn">
                    {{ t('main.file_diff.approximate') }}
                  </p>
                  <p v-else-if="diffTruncated" class="gcd-notice gcd-notice-warn">
                    {{ t('main.group_changes.truncated', { n: shownLineCount }) }}
                  </p>
                  <div class="gcd-diff" :class="`gcd-diff-${viewMode}`">
                    <template v-if="viewMode === 'unified'">
                      <template v-for="(section, sIdx) in unifiedDiffSections" :key="`u${sIdx}`">
                        <div v-if="section.kind === 'gap'" class="gcd-gap">
                          {{ t('main.file_diff.skipped_lines', { n: section.count }) }}
                        </div>
                        <template v-else>
                          <div
                            v-for="(row, rIdx) in section.rows"
                            :key="`u${sIdx}-${rIdx}`"
                            class="gcd-line"
                            :class="unifiedClass(row.status)"
                          >
                            <span class="gcd-ln">{{ row.leftNumber ?? '' }}</span>
                            <span class="gcd-ln">{{ row.rightNumber ?? '' }}</span>
                            <span class="gcd-sign">{{ row.sign }}</span>
                            <span class="gcd-text">{{ row.line.line }}</span>
                          </div>
                        </template>
                      </template>
                    </template>
                    <template v-else>
                      <template v-for="(section, sIdx) in diffSections" :key="`s${sIdx}`">
                        <div v-if="section.kind === 'gap'" class="gcd-gap">
                          {{ t('main.file_diff.skipped_lines', { n: section.count }) }}
                        </div>
                        <template v-else>
                          <div
                            v-for="(row, rIdx) in section.rows"
                            :key="`s${sIdx}-${rIdx}`"
                            class="gcd-srow"
                          >
                            <span class="gcd-ln">{{ row.leftNumber ?? '' }}</span>
                            <span class="gcd-text" :class="sideClass(row.left ? row.status : null, 'left')">{{ row.left?.line ?? '' }}</span>
                            <span class="gcd-ln">{{ row.rightNumber ?? '' }}</span>
                            <span class="gcd-text" :class="sideClass(row.right ? row.status : null, 'right')">{{ row.right?.line ?? '' }}</span>
                          </div>
                        </template>
                      </template>
                    </template>
                  </div>
                </template>
              </section>
            </div>
          </template>
        </div>
    </template>

    <template #footer>
      <DialogFooter :actions="actions">
        <template #action-back>
          <AppIcon name="arrow-bend-up-left" /> {{ t('main.group_changes.back_to_approval') }}
        </template>
      </DialogFooter>
    </template>
  </DialogShell>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import AppIcon from '@shared/AppIcon.vue'
import DialogFooter from './dialogs/DialogFooter.vue'
import DialogHeader from './dialogs/DialogHeader.vue'
import DialogShell from './dialogs/DialogShell.vue'
import type { DialogAction } from './dialogs/dialogTypes'
import { useExplorerStore, type GroupChangeData, type GroupFileDiffData } from '../stores/explorer'
import {
  buildDiffRows,
  collapseCommonRows,
  splitTextLines,
  toUnifiedRows,
  type DiffRow,
  type DiffSection,
  type UnifiedRow,
} from '../composables/useFileDiff'

const { t } = useI18n()
const explorerStore = useExplorerStore()

const props = defineProps<{
  projectId: string
  groupId: string
  branch?: string | null
  baseBranch?: string | null
  // The already-loaded /changes list from the sidebar summary: opening the viewer must
  // not re-ask for what the caller just fetched, and both must agree on the file set.
  changes: GroupChangeData[]
  // 0382 proposal 3: "도구가 남긴 흔적" filtered out of the change list. Comes back empty on servers with no channel.
  toolArtifacts?: string[]
}>()

const emit = defineEmits<{ (e: 'close'): void }>()

const shellRef = ref<InstanceType<typeof DialogShell> | null>(null)

function onHeaderClose() {
  shellRef.value?.requestClose('header')
}

/**
 * A single-button footer. [변경 화면으로] is the only way out besides the header X, and it
 * takes the `cancel` role rather than `primary`: it completes nothing, it returns the reviewer
 * to where they came from. The same shape the command/info dialogs took in 3순위 / 4.5순위.
 */
const actions = computed<DialogAction[]>(() => [
  {
    id: 'back',
    label: t('main.group_changes.back_to_approval'),
    role: 'cancel',
    onSelect: () => emit('close'),
  },
])

const artifactsOpen = ref(false)
const artifactPaths = computed(() => props.toolArtifacts ?? [])

type FilterKey = 'all' | 'M' | 'A' | 'D'
const filter = ref<FilterKey>('all')
const query = ref('')
const viewMode = ref<'unified' | 'split'>('unified')
const selectedPath = ref<string>('')
const diff = ref<GroupFileDiffData | null>(null)
const diffLoading = ref(false)
const diffError = ref(false)
// Diffs are read once per open dialog: the worktree can change under us, but within
// one review pass re-reading the same file on every back-and-forth is pure latency.
const diffCache = new Map<string, GroupFileDiffData>()

function statusKind(status: string): 'added' | 'modified' | 'deleted' {
  if (status === 'D') return 'deleted'
  if (status === 'A' || status === '?') return 'added'
  return 'modified'
}

function statusBadge(status: string): string {
  return status === '?' ? 'A' : status.slice(0, 1)
}

// '?' (untracked) counts as added here, exactly as the sidebar summary counts it.
function matchesFilter(change: GroupChangeData, key: FilterKey): boolean {
  if (key === 'all') return true
  if (key === 'A') return change.status === 'A' || change.status === '?'
  return change.status === key
}

const filterChips = computed(() =>
  ([
    { key: 'all', label: t('main.group_changes.filter_all') },
    { key: 'M', label: t('main.doc_info_panel.changes_kind_modified') },
    { key: 'A', label: t('main.doc_info_panel.changes_kind_added') },
    { key: 'D', label: t('main.doc_info_panel.changes_kind_deleted') },
  ] as const).map((chip) => ({
    key: chip.key as FilterKey,
    label: chip.label,
    count: props.changes.filter((change) => matchesFilter(change, chip.key as FilterKey)).length,
  })),
)

const visibleFiles = computed(() => {
  const needle = query.value.trim().toLowerCase()
  return props.changes.filter(
    (change) =>
      matchesFilter(change, filter.value) &&
      (!needle || change.path.toLowerCase().includes(needle)),
  )
})

const totals = computed(() => {
  let insertions = 0
  let deletions = 0
  let known = false
  for (const change of props.changes) {
    if (typeof change.insertions === 'number') { insertions += change.insertions; known = true }
    if (typeof change.deletions === 'number') { deletions += change.deletions; known = true }
  }
  return { insertions, deletions, known }
})

function hasLineStats(change: GroupChangeData): boolean {
  return typeof change.insertions === 'number' || typeof change.deletions === 'number'
}

function baseName(path: string): string {
  const idx = path.lastIndexOf('/')
  return idx === -1 ? path : path.slice(idx + 1)
}

function dirName(path: string): string {
  const idx = path.lastIndexOf('/')
  return idx === -1 ? '' : path.slice(0, idx)
}

// Five-cell added/deleted proportion bar (mockup file list). Any non-zero side keeps at
// least one cell so a 1-line change is still visible.
function barCells(change: GroupChangeData): string[] {
  const added = change.insertions ?? 0
  const deleted = change.deletions ?? 0
  const total = added + deleted || 1
  const green = Math.max(added ? 1 : 0, Math.round((added / total) * 5))
  const red = Math.max(deleted ? 1 : 0, Math.min(5 - green, Math.round((deleted / total) * 5)))
  return Array.from({ length: 5 }, (_, i) => (i < green ? 'p' : i < green + red ? 'm' : ''))
}

// NR0003 (0329) — the diff pane now reads through the same old/new-content contract
// FileDiffViewer.vue uses (0326 NR0005 §4): the server ships two full file bodies and
// the line diff is computed here with the engine the merge-conflict resolver already
// uses (useFileDiff.ts / useConflictChunks.ts), so no server-side hunk parser exists.
const selectedChange = computed(() =>
  props.changes.find((change) => change.path === selectedPath.value) ?? null,
)

const diffBinary = computed(() => !!diff.value?.old.binary || !!diff.value?.new.binary)
const diffTruncated = computed(() => !!diff.value?.old.truncated || !!diff.value?.new.truncated)

const diffRows = computed(() => {
  if (!diff.value || diffBinary.value) return { rows: [] as DiffRow[], approximate: false }
  return buildDiffRows(
    splitTextLines(diff.value.old.content ?? ''),
    splitTextLines(diff.value.new.content ?? ''),
  )
})
const hasDiffChanges = computed(() => diffRows.value.rows.some((row) => row.status !== 'common'))
const diffSections = computed<DiffSection[]>(() => collapseCommonRows(diffRows.value.rows))
const unifiedDiffSections = computed(() =>
  diffSections.value.map((section) =>
    section.kind === 'gap'
      ? section
      : { kind: 'rows' as const, rows: toUnifiedRows(section.rows) as UnifiedRow[] },
  ),
)

const shownLineCount = computed(() => {
  if (!diff.value) return 0
  const side = diff.value.new.truncated ? diff.value.new : diff.value.old
  return splitTextLines(side.content ?? '').length
})

// Split view: a row's left/right half needs its OWN class — a 'changed' row shows
// removed on the left and added on the right, never the same colour on both sides.
function sideClass(rowStatus: string | null, side: 'left' | 'right'): string {
  if (!rowStatus) return 'gcd-line-blank'
  if (rowStatus === 'common') return ''
  if (rowStatus === 'changed') return 'gcd-line-changed'
  return side === 'left' ? 'gcd-line-del' : 'gcd-line-add'
}

// Unified view: toUnifiedRows() only ever emits 'common' | 'removed' | 'added', and
// the sign (-/+) already tells the direction, so the status maps to a class directly.
function unifiedClass(rowStatus: string): string {
  if (rowStatus === 'removed') return 'gcd-line-del'
  if (rowStatus === 'added') return 'gcd-line-add'
  return ''
}

function canMove(delta: number): boolean {
  const list = visibleFiles.value
  const at = list.findIndex((file) => file.path === selectedPath.value)
  if (at === -1) return list.length > 0
  const next = at + delta
  return next >= 0 && next < list.length
}

function move(delta: number) {
  const list = visibleFiles.value
  const at = list.findIndex((file) => file.path === selectedPath.value)
  const next = at === -1 ? 0 : at + delta
  if (next < 0 || next >= list.length) return
  select(list[next].path)
}

function select(path: string) {
  if (path === selectedPath.value) return
  selectedPath.value = path
  void loadDiff(path)
}

async function loadDiff(path: string) {
  if (!path) return
  const cached = diffCache.get(path)
  if (cached) {
    diff.value = cached
    diffError.value = false
    return
  }
  diffLoading.value = true
  diffError.value = false
  try {
    const data = await explorerStore.fetchGroupBranchDiff(props.projectId, props.groupId, path)
    // A slow response for a file the reviewer already navigated away from must not
    // overwrite what is on screen.
    diffCache.set(path, data)
    if (selectedPath.value === path) diff.value = data
  } catch {
    if (selectedPath.value === path) {
      diff.value = null
      diffError.value = true
    }
  } finally {
    if (selectedPath.value === path) diffLoading.value = false
  }
}

// Selection follows the filter/search: hiding the open file would leave the diff pane
// showing something the list no longer offers.
watch(visibleFiles, (list) => {
  if (!list.length) {
    selectedPath.value = ''
    diff.value = null
    return
  }
  if (!list.some((file) => file.path === selectedPath.value)) select(list[0].path)
})

/*
 * T0022 §2.7 - the hand-wired ESC listener is gone. L0009 §2 "ESC" puts exactly ONE document
 * keydown listener in the stack manager, and it fires for the top dialog regardless of what
 * holds focus; the window listener here would be a second handler for the same key, so a
 * single ESC would emit `close` twice (the problem T0018 §2.3-6 warned about). The observable
 * behaviour - ESC closes this screen - is unchanged, it just arrives as `request-close`.
 */
onMounted(() => {
  const first = visibleFiles.value[0]
  if (first) select(first.path)
})
</script>

<style scoped>
@import './dialogs/gitDiffFileList.css';

/* `flex: 1 1 auto; min-height: 0` used to come from `.modal-bd`; on the common layer the
   sheet body (`.fg-dialog-surface--sheet .fg-dialog-body`) is a bare padding-0 flex column, so
   this box states its own share of the track. */
.gcd-modal-body {
  flex: 1 1 auto;
  display: flex;
  flex-direction: column;
  min-height: 0;
  padding: 0;
  overflow: hidden;
}
/* `.gcd-hd-text` and its two child rules left with the old header markup - the title and the
   summary line are `DialogHeader`'s `title`/`subtitle` elements now. */
.gcd-hd-lines { margin-left: 7px; display: inline-flex; gap: 6px; font-variant-numeric: tabular-nums; }
.gcd-add { color: var(--success, #15803d); font-weight: 600; }
.gcd-del { color: var(--danger, #b91c1c); font-weight: 600; }
.gcd-blank {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  font-size: 0.84rem;
  color: var(--text-m, #64748b);
}
.gcd-toolbar {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 14px;
  border-bottom: 1px solid var(--border, #e2e8f0);
  background: #f8fafc;
  flex-wrap: wrap;
}
.gcd-search {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 9px;
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 8px;
  background: #fff;
  color: var(--text-m, #64748b);
}
.gcd-search input {
  width: 170px;
  border: none;
  outline: none;
  font-size: 0.74rem;
  color: var(--text, #0f172a);
  background: transparent;
}
.gcd-chips { display: inline-flex; gap: 6px; flex-wrap: wrap; }
.gcd-chip {
  padding: 5px 9px;
  font-size: 0.72rem;
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 999px;
  background: #fff;
  color: inherit;
  cursor: pointer;
}
.gcd-chip.active { border-color: #bfdbfe; background: #dbeafe; color: #1d4ed8; font-weight: 700; }
.gcd-chip-count { font-variant-numeric: tabular-nums; opacity: 0.75; }
.gcd-tb-spacer { flex: 1 1 auto; }
.gcd-seg {
  display: inline-flex;
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 8px;
  overflow: hidden;
}
.gcd-seg button {
  border: none;
  background: #fff;
  padding: 6px 11px;
  font-size: 0.73rem;
  color: inherit;
  cursor: pointer;
}
.gcd-seg button + button { border-left: 1px solid var(--border, #e2e8f0); }
.gcd-seg button.active { background: #dbeafe; color: #1d4ed8; font-weight: 700; }
.gcd-file-stats {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.68rem;
  font-variant-numeric: tabular-nums;
}
.gcd-file-nostat { color: var(--text-m, #64748b); }
.gcd-bar { display: inline-flex; gap: 1px; }
.gcd-bar i {
  width: 5px;
  height: 9px;
  border-radius: 1px;
  background: var(--border, #e2e8f0);
}
.gcd-bar i.p { background: var(--success, #15803d); }
.gcd-bar i.m { background: var(--danger, #b91c1c); }
.gcd-diff-lines { flex: 0 0 auto; display: inline-flex; gap: 6px; font-size: 0.72rem; font-variant-numeric: tabular-nums; }
.gcd-diff-nav { flex: 0 0 auto; display: inline-flex; gap: 6px; }
.gcd-diff-nav button {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 5px 9px;
  font-size: 0.71rem;
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 7px;
  background: #fff;
  color: inherit;
  cursor: pointer;
}
.gcd-diff-nav button:disabled { opacity: 0.45; cursor: default; }
.gcd-notice {
  flex: 0 0 auto;
  margin: 0;
  padding: 7px 12px;
  font-size: 0.72rem;
  color: #1d4ed8;
  background: #eff6ff;
  border-bottom: 1px solid #bfdbfe;
}
.gcd-notice-warn { color: #92400e; background: #fffbeb; border-bottom-color: #fde68a; }
.gcd-srow { display: grid; grid-template-columns: 46px minmax(0, 1fr) 46px minmax(0, 1fr); }
.gcd-srow > .gcd-text:nth-child(2) { border-right: 1px solid var(--border, #e2e8f0); }
.gcd-line-changed, .gcd-text.gcd-line-changed { background: #fff7ed; }
.gcd-line-blank { background: #f8fafc; }

/* 0382 proposal 3 — artifact row. A visible but achromatic single line that doesn't push the change list down. */
.gcd-artifacts {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 10px;
  padding: 6px 12px;
  border-bottom: 1px solid var(--border, #e2e8f0);
  background: #f8fafc;
  font-size: 0.78rem;
}
.gcd-artifacts-toggle {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 0;
  border: 0;
  background: none;
  color: var(--text-d, #334155);
  cursor: pointer;
}
.gcd-artifacts-note { color: var(--text-m, #64748b); }
.gcd-artifacts-list {
  flex: 1 1 100%;
  margin: 2px 0 0;
  padding-left: 18px;
  max-height: 160px;
  overflow: auto;
  color: var(--text-m, #64748b);
  font-size: 0.74rem;
}
</style>

<!--
  Unscoped: `surface-class` lands on the dialog surface, which `DialogShell` renders and
  teleports out of this component's subtree, so a scoped rule could never reach it. This is the
  width `.document-modal--edit` measured, kept rather than widened to the `xl` 1180px the
  variant would otherwise give - the same choice T0018 made for `DocumentEditDialog`, which sat
  on that same app.css track.
-->
<style>
.fg-dialog-surface.gcd-changes-dialog {
  width: min(1120px, 94vw);
}
</style>