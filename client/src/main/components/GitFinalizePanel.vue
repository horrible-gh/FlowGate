<template>
  <!-- 0115 R0001-4: git finalize panel - visible only for git-integrated groups
       whose worktree exists (status !== 'none'); everyone else sees nothing. -->
  <div v-if="state && state.status !== 'none'" class="card git-fin-card">
    <div class="card-hd">
      <span class="card-title">
        <i class="fa-solid fa-code-branch" style="color:var(--text-m);"></i>
        {{ t('main.git_finalize.title') }}
      </span>
      <span class="git-branch-badge">
        <i class="fa-solid fa-code-commit"></i> {{ state.branch }}
      </span>
      <span class="badge" :class="statusBadgeClass">{{ statusLabel }}</span>
      <button class="git-refresh-btn" :disabled="busy" @click="fetchState" :title="t('main.git_finalize.refresh')">
        <i class="fa-solid fa-rotate"></i>
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
            <i class="fa-solid fa-play"></i>
            {{ busy ? t('main.git_finalize.running') : t('main.git_finalize.execute') }}
          </button>
        </div>
      </template>

      <template v-else-if="state.status === 'merged'">
        <p class="git-fin-done">
          <i class="fa-solid fa-circle-check"></i>
          {{ t('main.git_finalize.merged_msg', { base: state.base_branch, commit: mergeCommit || '-' }) }}
        </p>
      </template>
      <template v-else-if="state.status === 'pushed'">
        <p class="git-fin-done">
          <i class="fa-solid fa-circle-check"></i>
          {{ t('main.git_finalize.pushed_msg', { branch: state.branch }) }}
        </p>
      </template>
      <template v-else-if="state.status === 'merging'">
        <p class="git-fin-meta"><i class="fa-solid fa-spinner fa-spin"></i> {{ t('main.git_finalize.merging_msg') }}</p>
      </template>

      <template v-else-if="state.status === 'conflict'">
        <p class="git-fin-conflict-msg">
          <i class="fa-solid fa-triangle-exclamation"></i>
          {{ t('main.git_finalize.conflict_msg', { n: conflictFiles.length }) }}
        </p>
        <p v-if="conflictError" class="git-fin-conflict-msg">{{ conflictError }}</p>
        <div class="git-conflict-summary">
          <span>
            <i class="fa-solid fa-file-code"></i>
            {{ t('main.git_finalize.conflict_files_summary', { resolved: resolvedFileCount, total: conflictFiles.length }) }}
          </span>
          <span v-if="firstResidualMarker" class="git-marker-warning">{{ firstResidualMarker }}</span>
        </div>
        <div class="flex" style="justify-content:flex-end; gap:10px; margin-top:10px;">
          <button class="btn btn-secondary" :disabled="busy" @click="abortMerge">
            <i class="fa-solid fa-ban"></i> {{ t('main.git_finalize.abort') }}
          </button>
          <button class="btn btn-primary" :disabled="busy" @click="openConflictDialog">
            <i class="fa-solid fa-code-compare"></i> {{ t('main.git_finalize.open_resolver') }}
          </button>
        </div>
      </template>
    </div>
  </div>

  <div v-if="conflictDialogOpen" class="git-conflict-overlay" @click.self="closeConflictDialog">
    <div class="git-conflict-dialog" role="dialog" aria-modal="true">
      <div class="git-conflict-dialog-hd">
        <div>
          <h2>{{ t('main.git_finalize.dialog_title', { branch: state?.branch || '-', base: state?.base_branch || '-' }) }}</h2>
          <p>{{ t('main.git_finalize.dialog_subtitle', { n: conflictFiles.length }) }}</p>
        </div>
        <button class="git-dialog-close" :title="t('main.git_finalize.close_dialog')" @click="closeConflictDialog">
          <i class="fa-solid fa-xmark"></i>
        </button>
      </div>

      <div v-if="conflictLoadStatus === 'loading'" class="git-conflict-loading">
        <i class="fa-solid fa-spinner fa-spin"></i>
        {{ t('main.git_finalize.loading_conflicts') }}
      </div>
      <div v-else-if="conflictLoadStatus === 'error'" class="git-conflict-loading git-conflict-load-error">
        <span>{{ conflictError || t('main.git_finalize.load_failed') }}</span>
        <button class="btn btn-secondary" :disabled="busy" @click="retryFetchConflicts">
          <i class="fa-solid fa-rotate"></i> {{ t('main.git_finalize.retry') }}
        </button>
      </div>
      <div v-else-if="!conflictFiles.length" class="git-conflict-loading">
        {{ t('main.git_finalize.no_conflicts') }}
      </div>
      <template v-else>
        <div class="git-conflict-dialog-bd">
          <aside class="git-conflict-sidebar" :aria-label="t('main.git_finalize.file_list')">
            <button
              v-for="(f, idx) in conflictFiles"
              :key="f.path"
              class="git-conflict-file-tab"
              :class="{ active: idx === selectedConflictIndex, resolved: isFileResolved(f) }"
              @click="selectedConflictIndex = idx"
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
                <i class="fa-solid fa-file-code"></i>
                <span>{{ selectedConflictFile.path }}</span>
              </div>
              <div class="git-conflict-mode-tabs" v-if="selectedConflictFile.mode !== 'direct_only'">
                <button :class="{ active: selectedConflictFile.mode === 'chunk' }" @click="switchToChunkView(selectedConflictFile)">
                  <i class="fa-solid fa-code-compare"></i> {{ t('main.git_finalize.chunk_view') }}
                </button>
                <button :class="{ active: selectedConflictFile.mode === 'direct' }" @click="switchToDirectEdit(selectedConflictFile)">
                  <i class="fa-solid fa-pen-to-square"></i> {{ t('main.git_finalize.direct_edit') }}
                </button>
              </div>
              <span v-else class="git-direct-only-badge">
                <i class="fa-solid fa-pen-to-square"></i> {{ t('main.git_finalize.direct_only') }}
              </span>
            </div>

            <p v-if="selectedConflictFile.notice" class="git-conflict-notice">{{ selectedConflictFile.notice }}</p>

            <div v-if="selectedConflictFile.mode === 'chunk'" class="git-chunk-scroll">
              <template v-for="(seg, idx) in selectedConflictFile.segments" :key="idx">
                <pre v-if="seg.kind === 'common' && seg.lines.length" class="git-common-block">{{ joinLines(seg.lines) }}</pre>
                <article v-else-if="seg.kind === 'chunk'" class="git-conflict-chunk">
                  <div class="git-conflict-chunk-hd">
                    <span>{{ t('main.git_finalize.conflict_chunk', { n: chunkNumber(selectedConflictFile, idx) }) }}</span>
                    <div class="git-chunk-actions">
                      <button :class="{ active: seg.choice === 'ours' }" @click="applyChunkChoice(seg, 'ours')">
                        {{ t('main.git_finalize.current') }}
                      </button>
                      <button :class="{ active: seg.choice === 'theirs' }" @click="applyChunkChoice(seg, 'theirs')">
                        {{ t('main.git_finalize.incoming') }}
                      </button>
                      <button :class="{ active: seg.choice === 'both' }" @click="applyChunkChoice(seg, 'both')">
                        {{ t('main.git_finalize.both') }}
                      </button>
                    </div>
                  </div>
                  <div class="git-conflict-sides">
                    <div class="git-conflict-side ours">
                      <div class="git-conflict-side-label">{{ chunkLabel(seg.oursLabel, t('main.git_finalize.current')) }}</div>
                      <pre>{{ joinLines(seg.ours) || '\n' }}</pre>
                    </div>
                    <div class="git-conflict-side theirs">
                      <div class="git-conflict-side-label">{{ chunkLabel(seg.theirsLabel, t('main.git_finalize.incoming')) }}</div>
                      <pre>{{ joinLines(seg.theirs) || '\n' }}</pre>
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
            <i :class="allConflictsResolved ? 'fa-solid fa-circle-check' : 'fa-solid fa-triangle-exclamation'"></i>
            <span>{{ markerGuardText }}</span>
          </div>
          <div class="git-conflict-footer-actions">
            <button class="btn btn-secondary" :disabled="busy" @click="abortMerge">
              <i class="fa-solid fa-ban"></i> {{ t('main.git_finalize.abort') }}
            </button>
            <button class="btn btn-primary" :disabled="busy || !allConflictsResolved" @click="submitResolve">
              <i class="fa-solid fa-check"></i> {{ t('main.git_finalize.resolve_submit') }}
            </button>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getRequest, postRequest } from '@shared/api'
import { useToast } from './common/useToast'

const props = defineProps<{ groupId: string }>()

const { t } = useI18n()
const { showToast } = useToast()

const MAX_CHUNK_VIEW_CHARS = 500000
const MAX_MARKER_REPORT = 5
const MARKER_OPEN_RE = /^<{7}( |$)/
const MARKER_CLOSE_RE = /^>{7}( |$)/
const MARKER_SEP_RE = /^={7}$/
const MARKER_BASE_RE = /^\|{7}( |$)/

type ConflictMode = 'chunk' | 'direct' | 'direct_only'
type ChunkChoice = 'ours' | 'theirs' | 'both' | null

type CommonSegment = { kind: 'common'; lines: string[] }
type ChunkSegment = {
  kind: 'chunk'
  openLine: string
  baseLine: string | null
  sepLine: string
  closeLine: string
  ours: string[]
  base: string[]
  theirs: string[]
  oursLabel: string
  theirsLabel: string
  choice: ChunkChoice
  resolution: string[] | null
}
type ConflictSegment = CommonSegment | ChunkSegment

interface ConflictFileState {
  path: string
  conflict_count: number
  directText: string
  mode: ConflictMode
  segments: ConflictSegment[]
  notice: string
}

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

function actionLabel(c: string): string {
  return t(`main.git_finalize.action.${c}`)
}
function actionDesc(c: string): string {
  return t(`main.git_finalize.action_desc.${c}`)
}

function splitKeepEol(content: string): string[] {
  if (!content) return []
  const matches = content.match(/.*(?:\r\n|\n|\r|$)/g) || []
  return matches.filter((line, index) => line !== '' || index < matches.length - 1)
}
function stripEol(line: string): string {
  return line.replace(/\r\n$|\n$|\r$/, '')
}
function markerLabel(line: string, prefix: string): string {
  const s = stripEol(line)
  return s.startsWith(prefix) ? s.slice(prefix.length).trim() : ''
}
function pushCommon(segments: ConflictSegment[], lines: string[]) {
  if (lines.length) segments.push({ kind: 'common', lines: [...lines] })
  lines.length = 0
}
function parseConflictFile(content: string): ConflictSegment[] | null {
  const lines = splitKeepEol(content)
  const segments: ConflictSegment[] = []
  const common: string[] = []
  let stateName: 'COMMON' | 'OURS' | 'BASE' | 'THEIRS' = 'COMMON'
  let chunk: ChunkSegment | null = null

  for (const line of lines) {
    const s = stripEol(line)
    if (stateName === 'COMMON') {
      if (MARKER_OPEN_RE.test(s)) {
        pushCommon(segments, common)
        chunk = {
          kind: 'chunk',
          openLine: line,
          baseLine: null,
          sepLine: '',
          closeLine: '',
          ours: [],
          base: [],
          theirs: [],
          oursLabel: markerLabel(line, '<<<<<<< '),
          theirsLabel: '',
          choice: null,
          resolution: null,
        }
        stateName = 'OURS'
      } else {
        common.push(line)
      }
    } else if (stateName === 'OURS') {
      if (!chunk) return null
      if (MARKER_BASE_RE.test(s)) {
        chunk.baseLine = line
        stateName = 'BASE'
      } else if (MARKER_SEP_RE.test(s)) {
        chunk.sepLine = line
        stateName = 'THEIRS'
      } else if (MARKER_OPEN_RE.test(s) || MARKER_CLOSE_RE.test(s)) {
        return null
      } else {
        chunk.ours.push(line)
      }
    } else if (stateName === 'BASE') {
      if (!chunk) return null
      if (MARKER_SEP_RE.test(s)) {
        chunk.sepLine = line
        stateName = 'THEIRS'
      } else if (MARKER_OPEN_RE.test(s) || MARKER_CLOSE_RE.test(s)) {
        return null
      } else {
        chunk.base.push(line)
      }
    } else if (stateName === 'THEIRS') {
      if (!chunk) return null
      if (MARKER_CLOSE_RE.test(s)) {
        chunk.closeLine = line
        chunk.theirsLabel = markerLabel(line, '>>>>>>> ')
        segments.push(chunk)
        chunk = null
        stateName = 'COMMON'
      } else if (MARKER_OPEN_RE.test(s) || MARKER_SEP_RE.test(s) || MARKER_BASE_RE.test(s)) {
        return null
      } else {
        chunk.theirs.push(line)
      }
    }
  }

  if (stateName !== 'COMMON') return null
  pushCommon(segments, common)
  return segments
}
function assembleFile(segments: ConflictSegment[]): string {
  const out: string[] = []
  for (const seg of segments) {
    if (seg.kind === 'common') {
      out.push(...seg.lines)
    } else if (seg.resolution) {
      out.push(...seg.resolution)
    } else {
      out.push(seg.openLine, ...seg.ours)
      if (seg.baseLine) out.push(seg.baseLine, ...seg.base)
      out.push(seg.sepLine, ...seg.theirs, seg.closeLine)
    }
  }
  return out.join('')
}
function residualMarkers(content: string): number[] {
  const result: number[] = []
  const lines = content.split(/\r\n|\n|\r/)
  lines.forEach((line, index) => {
    if (result.length >= MAX_MARKER_REPORT) return
    if (MARKER_OPEN_RE.test(line) || MARKER_CLOSE_RE.test(line)) result.push(index + 1)
  })
  return result
}
function initConflictFile(f: { path: string; content: string; conflict_count: number }): ConflictFileState {
  if (f.content.length > MAX_CHUNK_VIEW_CHARS) {
    return {
      path: f.path,
      conflict_count: f.conflict_count,
      directText: f.content,
      mode: 'direct_only',
      segments: [],
      notice: t('main.git_finalize.too_large_direct'),
    }
  }
  const parsed = parseConflictFile(f.content)
  const chunkCount = parsed ? parsed.filter((seg) => seg.kind === 'chunk').length : 0
  if (!parsed || (f.conflict_count > 0 && chunkCount === 0)) {
    return {
      path: f.path,
      conflict_count: f.conflict_count,
      directText: f.content,
      mode: 'direct_only',
      segments: [],
      notice: t('main.git_finalize.direct_only_notice'),
    }
  }
  return {
    path: f.path,
    conflict_count: f.conflict_count,
    directText: f.content,
    mode: 'chunk',
    segments: parsed,
    notice: '',
  }
}
function currentFileContent(file: ConflictFileState): string {
  return file.mode === 'chunk' ? assembleFile(file.segments) : file.directText
}
function isFileResolved(file: ConflictFileState): boolean {
  return residualMarkers(currentFileContent(file)).length === 0
}
function joinLines(lines: string[]): string {
  return lines.join('')
}
function applyChunkChoice(seg: ChunkSegment, choice: Exclude<ChunkChoice, null>) {
  seg.choice = choice
  if (choice === 'ours') seg.resolution = [...seg.ours]
  else if (choice === 'theirs') seg.resolution = [...seg.theirs]
  else seg.resolution = [...seg.ours, ...seg.theirs]
}
function switchToDirectEdit(file: ConflictFileState) {
  file.directText = assembleFile(file.segments)
  file.mode = 'direct'
  file.notice = ''
}
function switchToChunkView(file: ConflictFileState) {
  const parsed = parseConflictFile(file.directText)
  if (!parsed) {
    file.notice = t('main.git_finalize.switch_parse_failed')
    return
  }
  file.segments = parsed
  file.mode = 'chunk'
  file.notice = ''
}
function chunkNumber(file: ConflictFileState, segmentIndex: number): number {
  return file.segments.slice(0, segmentIndex + 1).filter((seg) => seg.kind === 'chunk').length
}
function chunkLabel(label: string, fallback: string): string {
  return label || fallback
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
    const { data } = await postRequest<{ ok: boolean; result?: any; error?: any }>(
      `/api/v1/groups/${props.groupId}/git/finalize`,
      payload,
    )
    if (data.ok === false) {
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
    showToast(e?.response?.data?.error?.message || t('main.git_finalize.failed'), 'danger')
  } finally {
    busy.value = false
    await fetchState()
  }
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
.git-conflict-direct-editor {
  font: 0.75rem/1.48 var(--mono, ui-monospace, monospace);
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
  padding: 10px;
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
@media (max-width: 760px) {
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
