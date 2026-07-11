<template>
  <!-- 0115 R0001-4: git finalize panel - visible only for git-integrated groups
       whose worktree exists (status !== 'none'); everyone else sees nothing. -->
  <div v-if="state && state.status !== 'none'" class="card git-fin-card">
    <div class="card-hd">
      <span class="card-title">
        <AppIcon name="git-branch" style="color:var(--text-m);" />
        {{ t('main.git_finalize.title') }}
      </span>
      <span class="git-branch-badge">
        <AppIcon name="git-commit" /> {{ state.branch }}
      </span>
      <span class="badge" :class="statusBadgeClass">{{ statusLabel }}</span>
      <button class="git-refresh-btn" :disabled="busy" @click="fetchState" :title="t('main.git_finalize.refresh')">
        <AppIcon name="arrows-clockwise" />
      </button>
    </div>
    <div class="card-bd pad">
      <p v-if="aheadBehindText" class="git-fin-meta">{{ aheadBehindText }}</p>

      <template v-if="state.status === 'awaiting_choice' || state.status === 'waiting'">
        <div class="git-choice-row">
          <label v-for="c in state.choices" :key="c" class="git-choice" :class="{ sel: chosen === c }">
            <input type="radio" name="git-fin-action" :value="c" v-model="chosen" />
            <span class="git-choice-label">{{ actionLabel(c) }}</span>
            <span class="git-choice-desc">{{ actionDesc(c) }}</span>
          </label>
        </div>
        <div v-if="showCommitInput" class="git-commit-msg">
          <div class="git-commit-msg-hd">
            <label class="git-commit-msg-label" for="git-commit-subject">
              {{ t('main.git_finalize.commit_message_label') }}
            </label>
            <span v-if="commitSourceLabel" class="badge git-commit-src-badge">{{ commitSourceLabel }}</span>
          </div>
          <input
            id="git-commit-subject"
            class="form-ctrl git-commit-msg-input"
            type="text"
            v-model="commitMessage"
            :placeholder="commitSuggested"
            maxlength="200"
          />
          <p class="git-commit-msg-hint">
            {{ t('main.git_finalize.commit_message_hint') }}
            <a v-if="commitMessageBlank && commitSuggested" href="#" @click.prevent="restoreSuggested">
              {{ t('main.git_finalize.commit_message_restore') }}
            </a>
          </p>
        </div>
        <div class="flex" style="justify-content:flex-end; margin-top:10px;">
          <button class="btn btn-primary" :disabled="runDisabled" @click="runFinalize">
            <AppIcon name="play" />
            {{ busy ? t('main.git_finalize.running') : t('main.git_finalize.execute') }}
          </button>
        </div>
      </template>

      <template v-else-if="state.status === 'merged'">
        <p class="git-fin-done">
          <AppIcon name="check-circle" />
          {{ t('main.git_finalize.merged_msg', { base: state.base_branch, commit: mergeCommit || '-' }) }}
        </p>
      </template>
      <template v-else-if="state.status === 'pushed'">
        <p class="git-fin-done">
          <AppIcon name="check-circle" />
          {{ t('main.git_finalize.pushed_msg', { branch: state.branch }) }}
        </p>
      </template>
      <template v-else-if="state.status === 'merging'">
        <p class="git-fin-meta"><AppIcon name="spinner" spin /> {{ t('main.git_finalize.merging_msg') }}</p>
      </template>

      <template v-else-if="state.status === 'conflict'">
        <p class="git-fin-conflict-msg">
          <AppIcon name="warning" />
          {{ t('main.git_finalize.conflict_msg', { n: conflictFiles.length }) }}
        </p>
        <p v-if="conflictError" class="git-fin-conflict-msg">{{ conflictError }}</p>
        <div class="git-conflict-summary">
          <span>
            <AppIcon name="file-code" />
            {{ t('main.git_finalize.conflict_files_summary', { resolved: resolvedFileCount, total: conflictFiles.length }) }}
          </span>
          <span v-if="firstResidualMarker" class="git-marker-warning">{{ firstResidualMarker }}</span>
        </div>
        <div class="flex" style="justify-content:flex-end; gap:10px; margin-top:10px;">
          <button class="btn btn-secondary" :disabled="busy" @click="abortMerge">
            <AppIcon name="prohibit" /> {{ t('main.git_finalize.abort') }}
          </button>
          <button v-if="!props.inlineConflicts" class="btn btn-primary" :disabled="busy" @click="openConflictDialog">
            <AppIcon name="git-diff" /> {{ t('main.git_finalize.open_resolver') }}
          </button>
        </div>
      </template>
    </div>
  </div>

  <div
    v-if="conflictDialogOpen || (props.inlineConflicts && state?.status === 'conflict')"
    :class="props.inlineConflicts ? 'git-conflict-inline' : 'git-conflict-overlay'"
    :tabindex="props.inlineConflicts ? 0 : undefined"
    @click.self="closeConflictDialog"
    @keydown="onResolverKeydown"
  >
    <div :class="props.inlineConflicts ? 'git-conflict-dialog git-conflict-dialog--inline' : 'git-conflict-dialog'" role="dialog" :aria-modal="props.inlineConflicts ? undefined : 'true'">
      <div class="git-conflict-dialog-hd">
        <div>
          <h2>{{ t('main.git_finalize.dialog_title', { branch: state?.branch || '-', base: state?.base_branch || '-' }) }}</h2>
          <p>{{ t('main.git_finalize.dialog_subtitle', { n: conflictFiles.length }) }}</p>
        </div>
        <button v-if="!props.inlineConflicts" class="git-dialog-close" :title="t('main.git_finalize.close_dialog')" @click="closeConflictDialog">
          <AppIcon name="x" />
        </button>
      </div>

      <div v-if="conflictLoadStatus === 'loading'" class="git-conflict-loading">
        <AppIcon name="spinner" spin />
        {{ t('main.git_finalize.loading_conflicts') }}
      </div>
      <div v-else-if="conflictLoadStatus === 'error'" class="git-conflict-loading git-conflict-load-error">
        <span>{{ conflictError || t('main.git_finalize.load_failed') }}</span>
        <button class="btn btn-secondary" :disabled="busy" @click="retryFetchConflicts">
          <AppIcon name="arrows-clockwise" /> {{ t('main.git_finalize.retry') }}
        </button>
      </div>
      <div v-else-if="!conflictFiles.length" class="git-conflict-loading">
        {{ t('main.git_finalize.no_conflicts') }}
      </div>
      <template v-else>
        <div class="git-ai-assist-strip">
          <div>
            <strong><AppIcon name="sparkle" /> {{ t('main.git_finalize.ai_assist_title') }}</strong>
            <span>{{ t('main.git_finalize.ai_assist_summary', { ready: aiSuggestionTotal, total: totalChunkCount }) }}</span>
          </div>
          <button class="btn btn-secondary btn-sm" :disabled="busy || aiSuggestionRemaining === 0" @click="applyAllSuggestions">
            <AppIcon name="magic-wand" /> {{ t('main.git_finalize.ai_apply_all') }}
          </button>
        </div>
        <div class="git-conflict-dialog-bd">
          <aside class="git-conflict-sidebar" :aria-label="t('main.git_finalize.file_list')">
            <button
              v-for="(f, idx) in conflictFiles"
              :key="f.path"
              class="git-conflict-file-tab"
              :class="{ active: idx === selectedConflictIndex, resolved: isFileResolved(f) }"
              @click="selectConflictFile(idx)"
            >
              <span class="git-conflict-file-path">{{ f.path }}</span>
              <span class="git-conflict-file-meta">
                <span>{{ t('main.git_finalize.conflict_count', { n: f.conflict_count }) }}</span>
                <strong>{{ isFileResolved(f) ? t('main.git_finalize.resolved') : t('main.git_finalize.unresolved') }}</strong>
              </span>
            </button>
          </aside>

          <section v-if="selectedConflictFile" class="git-conflict-workspace">
            <div class="git-conflict-workspace-hd">
              <div class="git-conflict-selected-path">
                <AppIcon name="file-code" />
                <span>{{ selectedConflictFile.path }}</span>
              </div>
              <div class="git-conflict-mode-tabs" v-if="selectedConflictFile.mode !== 'direct_only'">
                <button :class="{ active: selectedConflictFile.mode === 'chunk' }" @click="switchToChunkView(selectedConflictFile)">
                  <AppIcon name="git-diff" /> {{ t('main.git_finalize.chunk_view') }}
                </button>
                <button :class="{ active: selectedConflictFile.mode === 'direct' }" @click="switchToDirectEdit(selectedConflictFile)">
                  <AppIcon name="note-pencil" /> {{ t('main.git_finalize.direct_edit') }}
                </button>
              </div>
              <span v-else class="git-direct-only-badge">
                <AppIcon name="note-pencil" /> {{ t('main.git_finalize.direct_only') }}
              </span>
            </div>

            <div v-if="selectedConflictFile.mode === 'chunk'" class="git-conflict-navigator">
              <div class="git-conflict-nav-summary">
                <strong>{{ t('main.git_finalize.remaining_chunks', { remaining: selectedRemainingCount, total: selectedChunkEntries.length }) }}</strong>
                <span>{{ t('main.git_finalize.nav_shortcut') }}</span>
              </div>
              <div class="git-conflict-nav-actions">
                <button type="button" :disabled="selectedChunkEntries.length === 0" @click="moveChunk(-1)" :title="t('main.git_finalize.previous_conflict')"><AppIcon name="caret-up" /></button>
                <button v-for="entry in selectedChunkEntries" :key="entry.segmentIndex" type="button" class="git-conflict-chip" :class="{ active: currentChunkSegment === entry.segmentIndex, resolved: !!entry.chunk.resolution }" @click="focusChunk(entry.segmentIndex)">{{ entry.number }}</button>
                <button type="button" :disabled="selectedChunkEntries.length === 0" @click="moveChunk(1)" :title="t('main.git_finalize.next_conflict')"><AppIcon name="caret-down" /></button>
              </div>
              <div class="git-code-size-controls" :aria-label="t('main.git_finalize.code_font_size')">
                <button type="button" :disabled="codeFontRem <= 0.72" @click="adjustCodeFont(-0.08)">A−</button>
                <span>{{ Math.round(codeFontRem * 100) }}%</span>
                <button type="button" :disabled="codeFontRem >= 1.18" @click="adjustCodeFont(0.08)">A＋</button>
              </div>
            </div>

            <p v-if="selectedConflictFile.notice" class="git-conflict-notice">{{ selectedConflictFile.notice }}</p>

            <div v-if="selectedConflictFile.mode === 'chunk'" class="git-chunk-scroll" :style="{ '--conflict-code-size': codeFontRem + 'rem' }">
              <template v-for="(seg, idx) in selectedConflictFile.segments" :key="idx">
                <div v-if="seg.kind === 'common' && seg.lines.length" class="git-common-shell">
                  <button v-if="seg.lines.length > COMMON_COLLAPSE_LINES && isCommonCollapsed(idx)" type="button" class="git-common-toggle" @click="toggleCommon(idx)">
                    <AppIcon name="caret-right" /> {{ t('main.git_finalize.common_collapsed', { n: seg.lines.length }) }}
                  </button>
                  <template v-else>
                    <button v-if="seg.lines.length > COMMON_COLLAPSE_LINES" type="button" class="git-common-toggle git-common-toggle--open" @click="toggleCommon(idx)">
                      <AppIcon name="caret-down" /> {{ t('main.git_finalize.common_collapse', { n: seg.lines.length }) }}
                    </button>
                    <pre class="git-common-block"><span v-for="(line, lineIdx) in seg.lines" :key="lineIdx" class="git-code-line"><span class="git-line-number">{{ commonLineNumber(selectedConflictFile, idx, lineIdx) }}</span><span class="git-code-line-text">{{ stripLineEnding(line) }}</span></span></pre>
                  </template>
                </div>
                <article v-else-if="seg.kind === 'chunk'" :id="chunkDomId(idx)" class="git-conflict-chunk" :class="{ resolved: !!seg.resolution, focused: currentChunkSegment === idx }">
                  <div class="git-conflict-chunk-hd">
                    <span>{{ t('main.git_finalize.conflict_chunk', { n: chunkNumber(selectedConflictFile, idx) }) }} <strong v-if="seg.resolution" class="git-resolved-badge">{{ t('main.git_finalize.resolved') }}</strong></span>
                    <button v-if="seg.resolution" type="button" class="git-chunk-undo" @click="undoChunk(seg)"><AppIcon name="arrow-counter-clockwise" /> {{ t('main.git_finalize.undo_choice') }}</button>
                    <div v-else class="git-chunk-actions">
                      <button :class="{ suggested: recommendedChoice(seg) === 'ours' }" @click="chooseChunk(seg, 'ours')">{{ t('main.git_finalize.current') }}</button>
                      <button :class="{ suggested: recommendedChoice(seg) === 'theirs' }" @click="chooseChunk(seg, 'theirs')">{{ t('main.git_finalize.incoming') }}</button>
                      <button :class="{ suggested: recommendedChoice(seg) === 'both' }" @click="chooseChunk(seg, 'both')">{{ t('main.git_finalize.both') }}</button>
                      <button @click="switchToDirectEdit(selectedConflictFile)">{{ t('main.git_finalize.direct_edit') }}</button>
                      <button v-if="recommendedChoice(seg)" class="git-ai-apply" @click="applySuggestion(seg)"><AppIcon name="sparkle" /> {{ t('main.git_finalize.ai_apply_one') }}</button>
                      <span v-else class="git-ai-hold">{{ t('main.git_finalize.ai_hold') }}</span>
                    </div>
                  </div>
                  <div v-if="seg.resolution" class="git-chunk-resolved">
                    <div><strong>{{ t('main.git_finalize.selected_choice', { choice: choiceLabel(seg.choice) }) }}</strong><span>{{ t('main.git_finalize.resolved_preview') }}</span></div>
                    <pre>{{ joinLines(seg.resolution).trim() || t('main.git_finalize.empty_choice') }}</pre>
                  </div>
                  <div v-else class="git-conflict-sides">
                    <div class="git-conflict-side ours">
                      <div class="git-conflict-side-label">{{ chunkLabel(seg.oursLabel, t('main.git_finalize.current')) }} <span v-if="recommendedChoice(seg) === 'ours'" class="git-ai-recommended">{{ t('main.git_finalize.ai_recommended') }}</span></div>
                      <pre><span v-for="(line, lineIdx) in seg.ours" :key="lineIdx" class="git-code-line"><span class="git-line-number">{{ sideLineNumber(selectedConflictFile, idx, 'ours', lineIdx) }}</span><span class="git-code-line-text">{{ stripLineEnding(line) }}</span></span><span v-if="!seg.ours.length" class="git-empty-side">{{ t('main.git_finalize.empty_side') }}</span></pre>
                    </div>
                    <div class="git-conflict-side theirs">
                      <div class="git-conflict-side-label">{{ chunkLabel(seg.theirsLabel, t('main.git_finalize.incoming')) }} <span v-if="recommendedChoice(seg) === 'theirs'" class="git-ai-recommended">{{ t('main.git_finalize.ai_recommended') }}</span></div>
                      <pre><span v-for="(line, lineIdx) in seg.theirs" :key="lineIdx" class="git-code-line"><span class="git-line-number">{{ sideLineNumber(selectedConflictFile, idx, 'theirs', lineIdx) }}</span><span class="git-code-line-text">{{ stripLineEnding(line) }}</span></span><span v-if="!seg.theirs.length" class="git-empty-side">{{ t('main.git_finalize.empty_side') }}</span></pre>
                    </div>
                  </div>
                </article>
              </template>
            </div>
            <textarea
              v-else
              v-model="selectedConflictFile.directText"
              class="git-conflict-direct-editor"
              spellcheck="false"
            ></textarea>
          </section>
        </div>

        <div class="git-conflict-dialog-ft">
          <div class="git-conflict-guard" :class="{ ok: allConflictsResolved }">
            <AppIcon :name="allConflictsResolved ? 'check-circle' : 'warning'" />
            <span>{{ markerGuardText }}</span>
          </div>
          <div class="git-conflict-footer-actions">
            <button class="btn btn-secondary" :disabled="busy" @click="abortMerge">
              <AppIcon name="prohibit" /> {{ t('main.git_finalize.abort') }}
            </button>
            <button class="btn btn-primary" :disabled="busy || !allConflictsResolved" @click="submitResolve">
              <AppIcon name="check" /> {{ t(props.inlineConflicts ? 'main.git_finalize.inline_resolve_submit' : 'main.git_finalize.resolve_submit') }}
            </button>
          </div>
        </div>
      </template>
    </div>
  </div>

  <!-- 0177 0007-CH: base_dirty 409 → operator chooses commit / revert / cancel
       (no silent auto-commit) before the finalize retries. -->
  <GitBaseDirtyDialog ref="baseDirtyDialog" />
</template>

<script setup lang="ts">
import AppIcon from '@shared/AppIcon.vue'
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getRequest, postRequest } from '@shared/api'
import { useProjectStore } from '../stores/project'
import { useToast } from './common/useToast'
// 0182 NR0003 §6: the chunk parser/assembler state machine moved to a shared
// composable so the header Git status panel's inline resolver uses the same
// button-based workflow. Behavior here is unchanged.
import {
  useConflictChunks,
  applyChunkChoice,
  chunkIndexes,
  chunkLabel,
  chunkNumber,
  currentFileContent,
  isFileResolved,
  joinLines,
  recommendChunkChoice,
  resetChunkChoice,
  residualMarkers,
  unresolvedChunkCount,
  type ChunkChoice,
  type ChunkSegment,
  type ConflictFileState,
  type ConflictSegment,
} from '../composables/useConflictChunks'
import GitBaseDirtyDialog from './GitBaseDirtyDialog.vue'

const props = defineProps<{ groupId: string; inlineConflicts?: boolean }>()

const { t } = useI18n()
const { showToast } = useToast()
const projectStore = useProjectStore()
const { initConflictFile, switchToDirectEdit, switchToChunkView } = useConflictChunks()
const baseDirtyDialog = ref<InstanceType<typeof GitBaseDirtyDialog> | null>(null)

interface GitCommitMessage {
  suggested: string
  source: string
}

interface GitFinState {
  group_id: string
  branch: string | null
  base_branch: string | null
  status: string
  default_action: string | null
  choices: string[]
  ahead_count: number | null
  behind_count: number | null
  merge_id: number | null
  merge_commit?: string | null
  commit_message?: GitCommitMessage | null
}

const state = ref<GitFinState | null>(null)
const chosen = ref<string>('')
const busy = ref(false)
const commitMessage = ref('')
const commitSuggested = ref('')
const commitSource = ref<string | null>(null)
const mergeCommit = ref<string | null>(null)
const conflictFiles = ref<ConflictFileState[]>([])
const conflictError = ref('')
const conflictDialogOpen = ref(false)
const conflictLoadStatus = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
const selectedConflictIndex = ref(0)
const currentChunkSegment = ref(-1)
const collapsedCommon = ref<Record<string, boolean>>({})
const codeFontRem = ref(0.86)
const COMMON_COLLAPSE_LINES = 12

const statusLabel = computed(() =>
  state.value ? t(`main.git_finalize.status.${state.value.status}`) : '',
)
const statusBadgeClass = computed(() => {
  switch (state.value?.status) {
    case 'merged':
    case 'pushed':
      return 'badge-blue'
    case 'conflict':
      return 'badge-red'
    default:
      return 'badge-yellow'
  }
})
const aheadBehindText = computed(() => {
  const s = state.value
  if (!s || s.ahead_count == null || s.behind_count == null) return ''
  return t('main.git_finalize.ahead_behind', { ahead: s.ahead_count, behind: s.behind_count })
})
const showCommitInput = computed(() => chosen.value === 'merge' || chosen.value === 'push')
const commitMessageBlank = computed(() => !commitMessage.value.trim())
const commitSourceLabel = computed(() =>
  commitSource.value ? t(`main.git_finalize.commit_source.${commitSource.value}`) : '',
)
const runDisabled = computed(
  () => busy.value || !chosen.value || (showCommitInput.value && commitMessageBlank.value),
)
function restoreSuggested() {
  commitMessage.value = commitSuggested.value
}
const selectedConflictFile = computed(() => conflictFiles.value[selectedConflictIndex.value] || null)
const resolvedFileCount = computed(() => conflictFiles.value.filter(isFileResolved).length)
const allConflictsResolved = computed(
  () => conflictFiles.value.length > 0 && conflictFiles.value.every(isFileResolved),
)
const firstResidualMarker = computed(() => {
  for (const file of conflictFiles.value) {
    const markers = residualMarkers(currentFileContent(file))
    if (markers.length) {
      return t('main.git_finalize.marker_summary_item', {
        path: file.path,
        lines: markers.join(', '),
      })
    }
  }
  return ''
})
const markerGuardText = computed(() => {
  if (allConflictsResolved.value) return t('main.git_finalize.markers_clear')
  const remaining = conflictFiles.value
    .map((file) => {
      const markers = residualMarkers(currentFileContent(file))
      if (!markers.length) return ''
      return t('main.git_finalize.marker_summary_item', {
        path: file.path,
        lines: markers.join(', '),
      })
    })
    .filter(Boolean)
  return remaining.length ? remaining.join(' / ') : t('main.git_finalize.submit_disabled_hint')
})
const selectedChunkEntries = computed(() => {
  const file = selectedConflictFile.value
  if (!file) return []
  return chunkIndexes(file).map((segmentIndex, index) => ({
    segmentIndex,
    number: index + 1,
    chunk: file.segments[segmentIndex] as ChunkSegment,
  }))
})
const selectedRemainingCount = computed(() =>
  selectedConflictFile.value ? unresolvedChunkCount(selectedConflictFile.value) : 0,
)
const totalChunkCount = computed(() =>
  conflictFiles.value.reduce((total, file) => total + chunkIndexes(file).length, 0),
)
const aiSuggestionTotal = computed(() =>
  conflictFiles.value.reduce(
    (total, file) => total + file.segments.filter(
      (seg): seg is ChunkSegment => seg.kind === 'chunk' && recommendChunkChoice(seg) !== null,
    ).length,
    0,
  ),
)
const aiSuggestionRemaining = computed(() =>
  conflictFiles.value.reduce(
    (total, file) => total + file.segments.filter(
      (seg): seg is ChunkSegment =>
        seg.kind === 'chunk' && !seg.resolution && recommendChunkChoice(seg) !== null,
    ).length,
    0,
  ),
)

function recommendedChoice(seg: ChunkSegment): ChunkChoice {
  return recommendChunkChoice(seg)
}
function choiceLabel(choice: ChunkChoice): string {
  if (!choice) return ''
  const key = choice === 'ours' ? 'current' : choice === 'theirs' ? 'incoming' : 'both'
  return t(`main.git_finalize.${key}`)
}
function chooseChunk(seg: ChunkSegment, choice: Exclude<ChunkChoice, null>) {
  applyChunkChoice(seg, choice)
}
function undoChunk(seg: ChunkSegment) {
  resetChunkChoice(seg)
}
function applySuggestion(seg: ChunkSegment) {
  const recommendation = recommendedChoice(seg)
  if (recommendation) applyChunkChoice(seg, recommendation)
}
function applyAllSuggestions() {
  for (const file of conflictFiles.value) {
    for (const seg of file.segments) {
      if (seg.kind !== 'chunk' || seg.resolution) continue
      const recommendation = recommendedChoice(seg)
      if (recommendation) applyChunkChoice(seg, recommendation)
    }
  }
}
function selectConflictFile(index: number) {
  selectedConflictIndex.value = index
  const file = conflictFiles.value[index]
  const first = file ? (chunkIndexes(file)[0] ?? -1) : -1
  currentChunkSegment.value = first
  if (first >= 0) void nextTick(() => focusChunk(first))
}
function chunkDomId(segmentIndex: number): string {
  return 'git-conflict-' + selectedConflictIndex.value + '-' + segmentIndex
}
function focusChunk(segmentIndex: number) {
  currentChunkSegment.value = segmentIndex
  void nextTick(() => {
    if (typeof document === 'undefined') return
    document.getElementById(chunkDomId(segmentIndex))?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  })
}
function moveChunk(delta: number) {
  const indexes = selectedChunkEntries.value.map((entry) => entry.segmentIndex)
  if (!indexes.length) return
  const current = indexes.indexOf(currentChunkSegment.value)
  const next = current < 0 ? 0 : (current + delta + indexes.length) % indexes.length
  focusChunk(indexes[next])
}
function onResolverKeydown(event: KeyboardEvent) {
  if (!event.shiftKey || (event.key !== 'ArrowUp' && event.key !== 'ArrowDown')) return
  event.preventDefault()
  moveChunk(event.key === 'ArrowUp' ? -1 : 1)
}
function adjustCodeFont(delta: number) {
  codeFontRem.value = Math.min(1.18, Math.max(0.72, Number((codeFontRem.value + delta).toFixed(2))))
}
function commonKey(segmentIndex: number): string {
  return selectedConflictIndex.value + ':' + segmentIndex
}
function isCommonCollapsed(segmentIndex: number): boolean {
  return collapsedCommon.value[commonKey(segmentIndex)] === true
}
function toggleCommon(segmentIndex: number) {
  const key = commonKey(segmentIndex)
  collapsedCommon.value = { ...collapsedCommon.value, [key]: !collapsedCommon.value[key] }
}
function resetCommonCollapse() {
  const next: Record<string, boolean> = {}
  conflictFiles.value.forEach((file, fileIndex) => {
    file.segments.forEach((seg, segmentIndex) => {
      if (seg.kind === 'common' && seg.lines.length > COMMON_COLLAPSE_LINES) {
        next[fileIndex + ':' + segmentIndex] = true
      }
    })
  })
  collapsedCommon.value = next
}
function originalSegmentLineCount(seg: ConflictSegment): number {
  if (seg.kind === 'common') return seg.lines.length
  return 1 + seg.ours.length + (seg.baseLine ? 1 + seg.base.length : 0) + 1 + seg.theirs.length + 1
}
function segmentStartLine(file: ConflictFileState, segmentIndex: number): number {
  return 1 + file.segments.slice(0, segmentIndex).reduce(
    (sum, seg) => sum + originalSegmentLineCount(seg),
    0,
  )
}
function commonLineNumber(file: ConflictFileState, segmentIndex: number, lineIndex: number): number {
  return segmentStartLine(file, segmentIndex) + lineIndex
}
function sideLineNumber(
  file: ConflictFileState,
  segmentIndex: number,
  side: 'ours' | 'theirs',
  lineIndex: number,
): number {
  const seg = file.segments[segmentIndex]
  if (!seg || seg.kind !== 'chunk') return lineIndex + 1
  const offset = side === 'ours'
    ? 1
    : 1 + seg.ours.length + (seg.baseLine ? 1 + seg.base.length : 0) + 1
  return segmentStartLine(file, segmentIndex) + offset + lineIndex
}
function stripLineEnding(line: string): string {
  return line.replace(/\r\n$|\n$|\r$/, '')
}

function actionLabel(c: string): string {
  return t(`main.git_finalize.action.${c}`)
}
function actionDesc(c: string): string {
  return t(`main.git_finalize.action_desc.${c}`)
}

async function fetchState() {
  if (!props.groupId) {
    state.value = null
    return
  }
  try {
    const { data } = await getRequest<{ ok: boolean; state: GitFinState }>(
      `/api/v1/groups/${props.groupId}/git/finalize`,
    )
    state.value = data.state
    chosen.value = data.state.default_action || 'wait'
    const cm = data.state.commit_message
    commitSuggested.value = cm?.suggested || ''
    commitSource.value = cm?.source || null
    commitMessage.value = cm?.suggested || ''
    mergeCommit.value = data.state.merge_commit || null
    if (data.state.status === 'conflict' && data.state.merge_id != null) {
      await fetchConflicts(data.state.merge_id)
    } else {
      conflictFiles.value = []
      conflictDialogOpen.value = false
    }
  } catch {
    state.value = null
  }
}

async function fetchConflicts(mergeId: number) {
  conflictError.value = ''
  conflictLoadStatus.value = 'loading'
  try {
    const { data } = await getRequest<{
      ok: boolean
      files: Array<{ path: string; content: string; conflict_count: number }>
    }>(`/api/v1/groups/${props.groupId}/git/merge/${mergeId}/conflicts`)
    conflictFiles.value = (data.files || []).map(initConflictFile)
    selectedConflictIndex.value = 0
    resetCommonCollapse()
    const firstFile = conflictFiles.value[0]
    currentChunkSegment.value = firstFile ? (chunkIndexes(firstFile)[0] ?? -1) : -1
    conflictLoadStatus.value = 'ready'
  } catch (e: any) {
    conflictFiles.value = []
    conflictError.value = e?.response?.data?.error?.message || t('main.git_finalize.load_failed')
    conflictLoadStatus.value = 'error'
  }
}
async function retryFetchConflicts() {
  const mergeId = state.value?.merge_id
  if (mergeId == null) return
  await fetchConflicts(mergeId)
}
async function openConflictDialog() {
  conflictDialogOpen.value = true
  const mergeId = state.value?.merge_id
  if (mergeId != null && (!conflictFiles.value.length || conflictLoadStatus.value === 'error')) {
    await fetchConflicts(mergeId)
  }
}
function closeConflictDialog() {
  conflictDialogOpen.value = false
}

async function runFinalize() {
  if (!props.groupId || !chosen.value) return
  if (showCommitInput.value && commitMessageBlank.value) return
  busy.value = true
  try {
    const payload: { action: string; commit_message?: string } = { action: chosen.value }
    if (showCommitInput.value) payload.commit_message = commitMessage.value.trim()
    await postFinalize(payload, false)
  } finally {
    busy.value = false
    await fetchState()
  }
}

async function postFinalize(
  payload: { action: string; commit_message?: string },
  retried: boolean,
): Promise<void> {
  try {
    const { data } = await postRequest<{ ok: boolean; result?: any; error?: any }>(
      `/api/v1/groups/${props.groupId}/git/finalize`,
      payload,
    )
    if (data.ok === false) {
      if (!retried && (await handleBaseDirty(data.error))) return postFinalize(payload, true)
      showToast(data.error?.message || t('main.git_finalize.failed'), 'danger')
    } else {
      const r = data.result
      mergeCommit.value = r?.merge_commit || null
      if (r?.status === 'conflict') {
        showToast(t('main.git_finalize.conflict_toast', { n: (r.conflict_files || []).length }), 'warning')
      } else if (r?.status === 'merged') {
        showToast(t('main.git_finalize.merged_toast', { commit: r.merge_commit || '' }), 'success')
      } else if (r?.status === 'pushed') {
        showToast(t('main.git_finalize.pushed_toast'), 'success')
      } else if (r?.status === 'waiting') {
        showToast(t('main.git_finalize.waiting_toast'), 'success')
      }
    }
  } catch (e: any) {
    const err = e?.response?.data?.error
    if (!retried && (await handleBaseDirty(err))) return postFinalize(payload, true)
    showToast(err?.message || t('main.git_finalize.failed'), 'danger')
  }
}

// 0177 0007-CH: mirror GitActionMenu — the E3 base_dirty 409 is never auto-
// resolved. Open the commit/revert/cancel dialog; it clears the base checkout
// (and syncs the tree badges) and returns 'proceed' once clean so the merge
// retries with the original payload, or 'cancel' with no error toast.
async function handleBaseDirty(err: any): Promise<boolean> {
  const projectId = projectStore.currentProjectId
  if (err?.code !== 'base_dirty' || !projectId || !baseDirtyDialog.value) return false
  const files = Array.isArray(err.details?.files) ? err.details.files : []
  const outcome = await baseDirtyDialog.value.resolve(projectId, files)
  return outcome === 'proceed'
}

async function submitResolve() {
  const mergeId = state.value?.merge_id
  if (!props.groupId || mergeId == null || !allConflictsResolved.value) return
  busy.value = true
  conflictError.value = ''
  try {
    const { data } = await postRequest<{ ok: boolean; result?: any; error?: any }>(
      `/api/v1/groups/${props.groupId}/git/merge/${mergeId}/resolve`,
      {
        files: conflictFiles.value.map((f) => ({ path: f.path, content: currentFileContent(f) })),
        complete: true,
      },
    )
    if (data.ok === false) {
      conflictError.value = data.error?.message || t('main.git_finalize.failed')
    } else if (data.result?.status === 'merged') {
      mergeCommit.value = data.result.merge_commit || null
      conflictDialogOpen.value = false
      showToast(t('main.git_finalize.merged_toast', { commit: data.result.merge_commit || '' }), 'success')
    } else if (data.result?.status === 'conflict') {
      conflictError.value = data.result?.remaining_conflicts || t('main.git_finalize.failed')
    }
  } catch (e: any) {
    if (e?.response?.status === 404) {
      conflictDialogOpen.value = false
      showToast(e?.response?.data?.error?.message || t('main.git_finalize.failed'), 'danger')
    } else {
      conflictError.value = e?.response?.data?.error?.message || t('main.git_finalize.failed')
    }
  } finally {
    busy.value = false
    await fetchState()
  }
}

async function abortMerge() {
  const mergeId = state.value?.merge_id
  if (!props.groupId || mergeId == null) return
  busy.value = true
  try {
    await postRequest(`/api/v1/groups/${props.groupId}/git/merge/${mergeId}/abort`, {})
    conflictDialogOpen.value = false
    showToast(t('main.git_finalize.aborted_toast'), 'success')
  } catch (e: any) {
    showToast(e?.response?.data?.error?.message || t('main.git_finalize.failed'), 'danger')
  } finally {
    busy.value = false
    await fetchState()
  }
}

function matchesGroup(e: Event): boolean {
  const detail = (e as CustomEvent).detail || {}
  const eventGroup = detail.group_id || detail.groupId
  return !eventGroup || eventGroup === props.groupId
}

function onGitStatusChanged(e: Event) {
  if (matchesGroup(e)) fetchState()
}

onMounted(() => {
  if (typeof window !== 'undefined') {
    window.addEventListener('fg:git_pending_changed', onGitStatusChanged)
    window.addEventListener('fg:git_status_refresh', onGitStatusChanged)
    window.addEventListener('fg:git_status_open', onGitStatusChanged)
  }
})
onUnmounted(() => {
  if (typeof window !== 'undefined') {
    window.removeEventListener('fg:git_pending_changed', onGitStatusChanged)
    window.removeEventListener('fg:git_status_refresh', onGitStatusChanged)
    window.removeEventListener('fg:git_status_open', onGitStatusChanged)
  }
})

watch(() => props.groupId, fetchState, { immediate: true })

defineExpose({ fetchState })
</script>

<style scoped>
.git-fin-card {
  margin-bottom: 12px;
}
.git-fin-card .card-hd {
  display: flex;
  align-items: center;
  gap: 8px;
}
.git-branch-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 8px;
  font-size: 0.72rem;
  font-family: var(--mono, ui-monospace, monospace);
  color: #0369a1;
  background: #e0f2fe;
  border: 1px solid #bae6fd;
  border-radius: 999px;
}
.badge-red {
  background: #fef2f2;
  color: #b91c1c;
}
.git-refresh-btn {
  margin-left: auto;
  border: none;
  background: none;
  color: var(--text-m);
  cursor: pointer;
  padding: 4px 6px;
}
.git-refresh-btn:hover {
  color: var(--primary);
}
.git-fin-meta {
  font-size: 0.78rem;
  color: var(--text-m);
  margin: 0 0 8px;
}
.git-choice-row {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}
.git-choice {
  flex: 1 1 160px;
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 10px 12px;
  border: 1px solid var(--border, #e2e8f0);
  border-radius: var(--r, 8px);
  cursor: pointer;
}
.git-choice.sel {
  border-color: var(--primary);
  background: var(--primary-l, #eff6ff);
}
.git-choice input {
  display: none;
}
.git-choice-label {
  font-weight: 700;
  font-size: 0.82rem;
}
.git-choice-desc {
  font-size: 0.72rem;
  color: var(--text-m);
}
.git-commit-msg {
  margin-top: 12px;
}
.git-commit-msg-hd {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 5px;
}
.git-commit-msg-label {
  font-weight: 700;
  font-size: 0.8rem;
}
.git-commit-src-badge {
  background: #eff6ff;
  color: #1d4ed8;
  border: 1px solid #bfdbfe;
  font-size: 0.68rem;
}
.git-commit-msg-input {
  width: 100%;
  font-family: var(--mono, ui-monospace, monospace);
  font-size: 0.8rem;
}
.git-commit-msg-hint {
  font-size: 0.72rem;
  color: var(--text-m);
  margin: 5px 0 0;
}
.git-fin-done {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 0.85rem;
  color: #166534;
  margin: 0;
}
.git-fin-conflict-msg {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 0.8rem;
  color: #b91c1c;
  margin: 0 0 8px;
}
.git-conflict-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 14px;
  align-items: center;
  font-size: 0.78rem;
  color: var(--text-m);
}
.git-marker-warning {
  color: #b45309;
}
.git-conflict-inline {
  margin-bottom: 12px;
  outline: none;
}
.git-conflict-overlay {
  position: fixed;
  inset: 0;
  z-index: 1400;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: rgba(15, 23, 42, 0.46);
}
.git-conflict-dialog {
  display: flex;
  flex-direction: column;
  width: min(1180px, calc(100vw - 48px));
  height: min(820px, calc(100vh - 48px));
  background: var(--bg, #fff);
  color: var(--text, #0f172a);
  border-radius: 8px;
  box-shadow: 0 24px 80px rgba(15, 23, 42, 0.3);
  overflow: hidden;
}
.git-conflict-dialog--inline {
  width: 100%;
  height: min(760px, calc(100vh - 170px));
  min-height: 620px;
  border: 1px solid var(--border, #e2e8f0);
  box-shadow: none;
}
.git-conflict-dialog-hd,
.git-conflict-dialog-ft {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 18px;
  border-bottom: 1px solid var(--border, #e2e8f0);
}
.git-conflict-dialog-ft {
  border-top: 1px solid var(--border, #e2e8f0);
  border-bottom: none;
}
.git-conflict-dialog-hd h2 {
  margin: 0;
  font-size: 1rem;
  line-height: 1.3;
}
.git-conflict-dialog-hd p {
  margin: 3px 0 0;
  font-size: 0.76rem;
  color: var(--text-m);
}
.git-dialog-close {
  width: 34px;
  height: 34px;
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 8px;
  background: var(--bg, #fff);
  cursor: pointer;
}
.git-conflict-loading {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  color: var(--text-m);
  font-size: 0.86rem;
}
.git-conflict-load-error {
  flex-direction: column;
}
.git-conflict-dialog-bd {
  flex: 1 1 auto;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(220px, 280px) minmax(0, 1fr);
}
.git-conflict-sidebar {
  min-height: 0;
  overflow: auto;
  padding: 10px;
  border-right: 1px solid var(--border, #e2e8f0);
  background: #f8fafc;
}
.git-conflict-file-tab {
  width: 100%;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 5px;
  padding: 10px;
  margin-bottom: 8px;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  color: inherit;
  text-align: left;
  cursor: pointer;
}
.git-conflict-file-tab:hover,
.git-conflict-file-tab.active {
  border-color: #bfdbfe;
  background: #fff;
}
.git-conflict-file-tab.resolved .git-conflict-file-meta strong {
  color: #15803d;
}
.git-conflict-file-path {
  overflow-wrap: anywhere;
  font: 600 0.76rem var(--mono, ui-monospace, monospace);
}
.git-conflict-file-meta {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  font-size: 0.7rem;
  color: var(--text-m);
}
.git-conflict-file-meta strong {
  color: #b91c1c;
}
.git-conflict-workspace {
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.git-conflict-workspace-hd {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--border, #e2e8f0);
}
.git-conflict-selected-path {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 7px;
  font: 700 0.78rem var(--mono, ui-monospace, monospace);
}
.git-conflict-selected-path span {
  overflow-wrap: anywhere;
}
.git-conflict-mode-tabs {
  flex: 0 0 auto;
  display: inline-flex;
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 8px;
  overflow: hidden;
}
.git-conflict-mode-tabs button,
.git-chunk-actions button {
  border: none;
  background: #fff;
  padding: 7px 10px;
  font-size: 0.74rem;
  cursor: pointer;
}
.git-conflict-mode-tabs button + button,
.git-chunk-actions button + button {
  border-left: 1px solid var(--border, #e2e8f0);
}
.git-conflict-mode-tabs button.active,
.git-chunk-actions button.active {
  background: #dbeafe;
  color: #1d4ed8;
  font-weight: 700;
}
.git-direct-only-badge {
  flex: 0 0 auto;
  font-size: 0.72rem;
  color: #92400e;
}
.git-conflict-notice {
  flex: 0 0 auto;
  margin: 0;
  padding: 8px 12px;
  font-size: 0.75rem;
  color: #92400e;
  background: #fffbeb;
  border-bottom: 1px solid #fde68a;
}
.git-chunk-scroll {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  padding: 12px;
  background: #f8fafc;
}
.git-common-block,
.git-conflict-side pre,
.git-conflict-direct-editor,
.git-chunk-resolved pre {
  font: var(--conflict-code-size, 0.86rem)/1.5 var(--mono, ui-monospace, monospace);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  tab-size: 2;
}
.git-common-block {
  margin: 0 0 10px;
  padding: 10px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #fff;
  color: #334155;
}
.git-conflict-chunk {
  margin-bottom: 12px;
  border: 1px solid #fecaca;
  border-radius: 8px;
  background: #fff;
  overflow: hidden;
}
.git-conflict-chunk-hd {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 9px 10px;
  border-bottom: 1px solid #fee2e2;
  background: #fff7ed;
  font-size: 0.76rem;
  font-weight: 700;
}
.git-chunk-actions {
  display: inline-flex;
  border: 1px solid #fed7aa;
  border-radius: 8px;
  overflow: hidden;
}
.git-conflict-sides {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
}
.git-conflict-side {
  min-width: 0;
}
.git-conflict-side + .git-conflict-side {
  border-left: 1px solid #e2e8f0;
}
.git-conflict-side-label {
  padding: 7px 10px;
  font-size: 0.72rem;
  font-weight: 700;
  border-bottom: 1px solid #e2e8f0;
}
.git-conflict-side.ours .git-conflict-side-label {
  color: #1d4ed8;
  background: #eff6ff;
}
.git-conflict-side.theirs .git-conflict-side-label {
  color: #047857;
  background: #ecfdf5;
}
.git-conflict-side pre {
  min-height: 54px;
  margin: 0;
  padding: 8px 0;
  color: #0f172a;
}
.git-conflict-direct-editor {
  flex: 1 1 auto;
  min-height: 0;
  width: 100%;
  border: none;
  border-radius: 0;
  padding: 12px;
  resize: none;
  outline: none;
  color: var(--text, #0f172a);
  background: #fff;
}
.git-conflict-guard {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  color: #b45309;
  font-size: 0.76rem;
}
.git-conflict-guard.ok {
  color: #15803d;
}
.git-conflict-guard span {
  overflow-wrap: anywhere;
}
.git-conflict-footer-actions {
  flex: 0 0 auto;
  display: flex;
  gap: 10px;
}
.git-ai-assist-strip {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  padding: 10px 18px;
  border-bottom: 1px solid #ddd6fe;
  color: #5b21b6;
  background: #f5f3ff;
  font-size: 0.76rem;
}
.git-ai-assist-strip > div,
.git-ai-assist-strip strong { display: flex; align-items: center; gap: 8px; }
.git-conflict-navigator {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--border, #e2e8f0);
  background: #fff;
}
.git-conflict-nav-summary { display: flex; flex-direction: column; gap: 1px; font-size: 0.74rem; }
.git-conflict-nav-summary span { color: var(--text-m); font-size: 0.66rem; }
.git-conflict-nav-actions,
.git-code-size-controls { display: inline-flex; align-items: center; gap: 4px; }
.git-conflict-nav-actions button,
.git-code-size-controls button {
  min-width: 28px;
  height: 28px;
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 6px;
  background: #fff;
  color: inherit;
  cursor: pointer;
}
.git-conflict-nav-actions button:disabled,
.git-code-size-controls button:disabled { opacity: 0.45; cursor: default; }
.git-conflict-nav-actions .git-conflict-chip {
  min-width: 26px;
  color: #b91c1c;
  border-color: #fecaca;
  background: #fef2f2;
}
.git-conflict-nav-actions .git-conflict-chip.resolved {
  color: #15803d;
  border-color: #bbf7d0;
  background: #f0fdf4;
}
.git-conflict-nav-actions .git-conflict-chip.active { box-shadow: 0 0 0 2px #93c5fd; }
.git-code-size-controls { margin-left: auto; color: var(--text-m); font-size: 0.7rem; }
.git-common-shell { margin-bottom: 10px; }
.git-common-toggle {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 8px 10px;
  border: 1px dashed #cbd5e1;
  border-radius: 8px;
  color: #475569;
  background: #f8fafc;
  font-size: 0.72rem;
  cursor: pointer;
}
.git-common-toggle--open { margin-bottom: 4px; border-radius: 8px 8px 0 0; }
.git-conflict-chunk.resolved { border-color: #bbf7d0; background: #f0fdf4; }
.git-conflict-chunk.focused { box-shadow: 0 0 0 2px #93c5fd; }
.git-resolved-badge,
.git-ai-recommended {
  display: inline-flex;
  margin-left: 6px;
  padding: 1px 6px;
  border-radius: 999px;
  color: #15803d;
  background: #dcfce7;
  font-size: 0.66rem;
}
.git-chunk-undo {
  border: 1px solid #86efac;
  border-radius: 6px;
  padding: 5px 8px;
  color: #166534;
  background: #fff;
  cursor: pointer;
}
.git-chunk-actions button.suggested {
  color: #6d28d9;
  background: #f5f3ff;
  box-shadow: inset 0 -2px #8b5cf6;
}
.git-chunk-actions .git-ai-apply { color: #fff; background: #7c3aed; }
.git-ai-hold {
  display: inline-flex;
  align-items: center;
  padding: 0 8px;
  color: #92400e;
  font-size: 0.68rem;
  white-space: nowrap;
}
.git-chunk-resolved {
  display: grid;
  grid-template-columns: minmax(170px, 0.35fr) minmax(0, 1fr);
  gap: 12px;
  padding: 10px;
}
.git-chunk-resolved > div {
  display: flex;
  flex-direction: column;
  gap: 3px;
  color: #166534;
  font-size: 0.72rem;
}
.git-chunk-resolved pre {
  max-height: 92px;
  overflow: auto;
  margin: 0;
  padding: 8px;
  border: 1px solid #bbf7d0;
  border-radius: 6px;
  background: #fff;
  white-space: pre-wrap;
}
.git-code-line {
  display: grid;
  grid-template-columns: 3.6rem minmax(0, 1fr);
  min-height: 1.5em;
}
.git-line-number {
  padding: 0 10px 0 6px;
  color: #94a3b8;
  text-align: right;
  user-select: none;
}
.git-code-line-text {
  min-width: 0;
  padding-right: 8px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.git-empty-side { display: block; padding: 4px 10px; color: #94a3b8; font-style: italic; }@media (max-width: 760px) {
  .git-conflict-inline {
  margin-bottom: 12px;
  outline: none;
}
.git-conflict-overlay {
    padding: 0;
  }
  .git-conflict-dialog {
    width: 100vw;
    height: 100vh;
    border-radius: 0;
  }
  .git-conflict-dialog-bd {
    grid-template-columns: 1fr;
    grid-template-rows: auto minmax(0, 1fr);
  }
  .git-conflict-sidebar {
    display: flex;
    gap: 8px;
    overflow-x: auto;
    border-right: none;
    border-bottom: 1px solid var(--border, #e2e8f0);
  }
  .git-conflict-file-tab {
    flex: 0 0 220px;
    margin-bottom: 0;
  }
  .git-conflict-sides {
    grid-template-columns: 1fr;
  }
  .git-conflict-side + .git-conflict-side {
    border-left: none;
    border-top: 1px solid #e2e8f0;
  }
  .git-conflict-dialog-ft,
  .git-conflict-workspace-hd {
    align-items: stretch;
    flex-direction: column;
  }
  .git-conflict-footer-actions {
    justify-content: flex-end;
  }
}
</style>
