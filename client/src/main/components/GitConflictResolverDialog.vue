<template>
  <!-- flowgate.default.0212 T0009 — the shared 1180×820 conflict resolver
       (approved 0207 mockup A). Extracted verbatim from GitFinalizePanel so the
       header Git status panel and the finalize panel present the SAME resolver:
       file sidebar, chunk chips/navigation, quick recommendation strip + per-chunk
       recommendation, common-block folding and font-size controls. The host
       component owns fetching, submission and abort; this dialog owns only
       view-state and in-place chunk choices on the shared file objects. -->
  <div class="git-conflict-overlay" @keydown="onResolverKeydown">
    <div class="git-conflict-dialog" role="dialog" aria-modal="true">
      <div class="git-conflict-dialog-hd">
        <div>
          <h2>{{ t('main.git_finalize.dialog_title', { branch: branch || '-', base: baseBranch || '-' }) }}</h2>
          <p>{{ t('main.git_finalize.dialog_subtitle', { n: files.length }) }}</p>
        </div>
        <div v-if="totalChunkCount > 0" class="git-conflict-progress">
          <span>{{ t('main.git_finalize.resolve_progress', { done: resolvedChunkTotal, total: totalChunkCount }) }}</span>
          <div class="git-conflict-progress-bar">
            <div class="git-conflict-progress-fill" :style="{ width: progressPercent }"></div>
          </div>
        </div>
        <button class="git-dialog-close" :title="t('main.git_finalize.close_dialog')" @click="emit('close')">
          <AppIcon name="x" />
        </button>
      </div>

      <div v-if="loadStatus === 'loading'" class="git-conflict-loading">
        <AppIcon name="spinner" spin />
        {{ t('main.git_finalize.loading_conflicts') }}
      </div>
      <div v-else-if="loadStatus === 'error'" class="git-conflict-loading git-conflict-load-error">
        <span>{{ errorMessage || t('main.git_finalize.load_failed') }}</span>
        <button class="btn btn-secondary" :disabled="busy" @click="emit('retry')">
          <AppIcon name="arrows-clockwise" /> {{ t('main.git_finalize.retry') }}
        </button>
      </div>
      <!-- 0481 T0010 #2 — "아무 액션도 못하는 다이얼로그만 계속뜨고". This branch used to be a
           bare sentence: no retry, no [AI 호출], no [중단], nothing but the ✕. A session can
           legitimately reach zero listed files (the resolver already staged them, or the
           panel's list is a moment stale), and when it did the operator was cornered. The
           list is refreshable from here, and the footer below now renders in this state too. -->
      <div v-else-if="!files.length" class="git-conflict-loading git-conflict-empty">
        <span>{{ t('main.git_finalize.no_conflicts') }}</span>
        <button class="btn btn-secondary" :disabled="busy" @click="emit('retry')">
          <AppIcon name="arrows-clockwise" /> {{ t('main.git_finalize.reload_conflicts') }}
        </button>
      </div>
      <template v-if="loadStatus === 'ready' && files.length">
        <div class="git-ai-assist-strip">
          <div>
            <strong><AppIcon name="magic-wand" /> {{ t('main.git_finalize.quick_recommend_title') }}</strong>
            <span>{{ t('main.git_finalize.quick_recommend_summary', { ready: aiSuggestionTotal, total: totalChunkCount }) }}</span>
          </div>
          <button class="btn btn-secondary btn-sm" :disabled="busy || aiSuggestionRemaining === 0" @click="applyAllSuggestions">
            <AppIcon name="magic-wand" /> {{ t('main.git_finalize.quick_apply_all') }}
          </button>
        </div>
        <div class="git-conflict-dialog-bd">
          <aside class="git-conflict-sidebar" :aria-label="t('main.git_finalize.file_list')">
            <button
              v-for="(f, idx) in files"
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
                      <button v-if="recommendedChoice(seg)" class="git-ai-apply" @click="applySuggestion(seg)"><AppIcon name="magic-wand" /> {{ t('main.git_finalize.quick_apply_one') }}</button>
                      <span v-else class="git-ai-hold">{{ t('main.git_finalize.quick_hold') }}</span>
                    </div>
                  </div>
                  <div v-if="seg.resolution" class="git-chunk-resolved">
                    <div><strong>{{ t('main.git_finalize.selected_choice', { choice: choiceLabel(seg.choice) }) }}</strong><span>{{ t('main.git_finalize.resolved_preview') }}</span></div>
                    <pre>{{ joinLines(seg.resolution).trim() || t('main.git_finalize.empty_choice') }}</pre>
                  </div>
                  <div v-else class="git-conflict-sides">
                    <div class="git-conflict-side ours">
                      <div class="git-conflict-side-label">{{ chunkLabel(seg.oursLabel, t('main.git_finalize.current')) }} <span v-if="recommendedChoice(seg) === 'ours'" class="git-ai-recommended">{{ t('main.git_finalize.quick_recommended') }}</span></div>
                      <pre><span v-for="line in chunkDiff(seg).ours" :key="line.sourceIndex" class="git-code-line" :class="'diff-' + line.status"><span class="git-line-number">{{ sideLineNumber(selectedConflictFile, idx, 'ours', line.sourceIndex) }}</span><span class="git-code-line-text"><span v-for="(token, tokenIdx) in line.tokens" :key="tokenIdx" class="git-code-token" :class="'diff-token-' + token.status">{{ token.text }}</span></span></span><span v-if="!seg.ours.length" class="git-empty-side">{{ t('main.git_finalize.empty_side') }}</span></pre>
                    </div>
                    <div v-if="seg.baseLine" class="git-conflict-side base">
                      <div class="git-conflict-side-label">{{ t('main.git_finalize.common_base') }}</div>
                      <pre><span v-for="line in baseDiff(seg)" :key="line.sourceIndex" class="git-code-line" :class="'diff-' + line.status"><span class="git-line-number">{{ sideLineNumber(selectedConflictFile, idx, 'base', line.sourceIndex) }}</span><span class="git-code-line-text"><span v-for="(token, tokenIdx) in line.tokens" :key="tokenIdx" class="git-code-token" :class="'diff-token-' + token.status">{{ token.text }}</span></span></span><span v-if="!seg.base.length" class="git-empty-side">{{ t('main.git_finalize.empty_side') }}</span></pre>
                    </div>
                    <div class="git-conflict-side theirs">
                      <div class="git-conflict-side-label">{{ chunkLabel(seg.theirsLabel, t('main.git_finalize.incoming')) }} <span v-if="recommendedChoice(seg) === 'theirs'" class="git-ai-recommended">{{ t('main.git_finalize.quick_recommended') }}</span></div>
                      <pre><span v-for="line in chunkDiff(seg).theirs" :key="line.sourceIndex" class="git-code-line" :class="'diff-' + line.status"><span class="git-line-number">{{ sideLineNumber(selectedConflictFile, idx, 'theirs', line.sourceIndex) }}</span><span class="git-code-line-text"><span v-for="(token, tokenIdx) in line.tokens" :key="tokenIdx" class="git-code-token" :class="'diff-token-' + token.status">{{ token.text }}</span></span></span><span v-if="!seg.theirs.length" class="git-empty-side">{{ t('main.git_finalize.empty_side') }}</span></pre>
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
      </template>

      <!-- 0481 T0010 rev5 (반려 #1·#2) — "AI 호출"의 상태는 이 한 줄이 전부 말한다: 호출을
           보낸 순간부터(실행 기록이 브라우저에 등록되기 전에도), 실행이 도는 동안, 그리고
           공급자 목록을 못 읽어 호출 자체가 막혀 있을 때까지. 액션바 안이 아니라 액션바 위의
           제 줄에 서기 때문에 가드 문장의 폭을 빼앗지 않는다(rev3 의 세로 글자 사고).
           시안 v13 화면 1 이 그리는 상태(공급자 정상·실행 없음)에서는 아예 없는 줄이라
           액션바는 시안 그대로다. -->
      <div
        v-if="showAiActions && aiStrip"
        class="git-conflict-ai-strip"
        :class="'git-conflict-ai-strip--' + aiStrip.kind"
        data-test="conflict-ai-run"
      >
        <AppIcon :name="aiStrip.icon" :spin="aiStrip.spin" />
        <span class="git-conflict-ai-strip-text" :title="aiStrip.text">{{ aiStrip.text }}</span>
        <button
          v-if="aiStrip.retry"
          type="button"
          class="git-conflict-ai-strip-retry"
          :disabled="busy || providerLoading"
          data-test="conflict-provider-retry"
          @click="emit('reload-providers')"
        >
          <AppIcon name="arrows-clockwise" /> {{ t('main.git_finalize.provider_reload') }}
        </button>
      </div>

      <!-- 0481 T0010 #2 — the instruction box and the action bar are what the operator acts
           WITH, so they belong to the dialog, not to the "we have files" branch. They render
           for ready, empty and load-error alike; only the loading frame has nothing to act on
           yet. Individual controls disable themselves (see :disabled below) — 0441 TR0005's
           rule: a control that cannot run right now stays visible and disabled. -->
      <template v-if="loadStatus !== 'loading'">
        <div v-if="showAiActions" class="git-conflict-message-bar">
          <label for="git-conflict-ai-message">{{ t('main.git_finalize.conflict_ai_message_label') }}</label>
          <textarea
            id="git-conflict-ai-message"
            v-model="conflictMessage"
            rows="2"
            :disabled="busy"
            :placeholder="t('main.git_finalize.conflict_ai_message_placeholder')"
            data-test="conflict-ai-message"
          ></textarea>
        </div>

        <!-- 0481 D0006 §6.2 v13 화면 1 그대로: 왼쪽 `.git-conflict-footer-context` 는
             마커 가드 + 세로 구분선 + AI 호출 옵션(공급자 셀렉트·[자동])이고, 오른쪽
             `.git-conflict-footer-actions` 는 [멘트 복사]·[AI 호출]·[중단]·[해결 제출]
             네 버튼만 담는다. v13 이전에는 옵션 두 개가 버튼 묶음 안에 섞여 있었다. -->
        <div class="git-conflict-dialog-ft">
          <div class="git-conflict-footer-context">
            <!-- 0481 T0010 rev5 (반려 #2) — 시안 y5bwr1o0 v13 화면 1 의 가드는
                 `white-space: nowrap` 한 줄이다. 구현은 `overflow-wrap: anywhere` 였고,
                 rev3 이 그 옆에 폭을 고정으로 먹는 실행 문구를 끼워 넣자 가드에 남은 폭이
                 거의 0 이 되어 "README.md: 16, 28행" 이 글자마다 줄바꿈해 세로로 섰다.
                 잘리는 문장은 title 로 그대로 읽을 수 있다. -->
            <div class="git-conflict-guard" :class="{ ok: allConflictsResolved }" :title="guardText">
              <AppIcon :name="guardIcon" />
              <span>{{ guardText }}</span>
            </div>
            <span v-if="showAiActions" class="ft-divider" aria-hidden="true"></span>
            <!-- 0234 B0001 RC1/RC2: confirm/change the provider that the conflict AI run
                 uses. 0481 D0006 §6.2 v13 화면 1: [자동] 은 호출 시점에만 정하는 옵션이라
                 공급자 셀렉트 바로 옆에 둔다. 이 세션의 해결 실행을 새로 시작하는
                 [AI 호출]/[해결 제출] 요청에만 실려 나가고, 그 뒤 회신(재지시 대화,
                 승인 대기 화면)에는 아예 존재하지 않는 필드다. -->
            <div v-if="showAiActions" class="git-conflict-invoke-options" :aria-label="t('main.git_finalize.invoke_options')">
              <AiProviderSelect
                class="git-conflict-provider"
                :providers="providers || []"
                :model-value="selectedProvider"
                :loading="providerLoading"
                :errored="providerErrored"
                hide-label
                @update:model-value="(v) => emit('update:provider', v)"
              />
              <label class="git-conflict-auto-toggle" :class="{ disabled: busy }">
                <input type="checkbox" v-model="autoResolve" :disabled="busy" />
                <span>{{ t('main.git_finalize.auto_resolve_label') }}</span>
              </label>
            </div>
          </div>
          <div class="git-conflict-footer-actions">
            <button v-if="showAiActions" class="btn btn-secondary" :disabled="busy" @click="emit('copy-mention')">
              <AppIcon name="copy" /> {{ t('main.git_finalize.copy_conflict_mention') }}
            </button>
            <!-- 0481 T0010 #3 — "AI호출하니까 호출도 안되는거같고". This button used to
                 disappear entirely whenever the provider list was empty (the whole action
                 group hung off `v-if="providers?.length"`), taking [중단] and [해결 제출]
                 with it. It stays, and says why it cannot run. -->
            <button
              v-if="showAiActions"
              class="btn btn-secondary"
              :disabled="busy || !providers?.length || !!aiRunNotice || !!aiRunPending"
              :title="invokeBlockedReason"
              data-test="conflict-ai-invoke"
              @click="emit('ai-invoke', conflictMessage.trim(), autoResolve)"
            >
              <AppIcon name="robot" /> {{ t('main.git_finalize.invoke_conflict_ai') }}
            </button>
            <button class="btn btn-secondary" :disabled="busy" @click="emit('abort')">
              <AppIcon name="prohibit" /> {{ t('main.git_finalize.abort') }}
            </button>
            <button class="btn btn-primary" :disabled="busy || !allConflictsResolved" @click="emit('submit', autoResolve)">
              <AppIcon name="check" /> {{ t('main.git_finalize.resolve_submit') }}
            </button>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import AppIcon from '@shared/AppIcon.vue'
import AiProviderSelect from './AiProviderSelect.vue'
import { computed, nextTick, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  useConflictChunks,
  buildChunkSideDiff,
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
  type ChunkSideDiff,
  type ConflictFileState,
  type ConflictSegment,
  type DiffLine,
} from '../composables/useConflictChunks'

const props = defineProps<{
  files: ConflictFileState[]
  branch: string | null
  baseBranch: string | null
  busy: boolean
  loadStatus: 'idle' | 'loading' | 'ready' | 'error'
  errorMessage: string
  // 0234 B0001: runtime provider list + current selection, owned by the host panel
  // (GitStatusPanel / GitFinalizePanel) which holds the aiProvider store.
  providers?: { id: string; name: string }[]
  selectedProvider?: string
  providerLoading?: boolean
  providerErrored?: boolean
  // 0481 T0010 #2: does THIS host wire the AI half ([멘트 복사]/[AI 호출]/공급자/[자동])?
  // FileExplorer's group-update conflict does not, and used to express that by passing an
  // empty `providers` list — which, while the whole action group hung off `providers?.length`,
  // also deleted [중단] and [해결 제출] and left that dialog with no way to finish at all.
  // The intent is a prop now, and it only ever hides the AI controls.
  //
  // Phrased as an opt-OUT because a type-only `defineProps` gives every boolean prop the
  // boolean-casting default of `false`: an `aiActions?: boolean` would arrive as `false`
  // for every host that does not pass it, i.e. the exact bug this replaces.
  hideAiActions?: boolean
  // 0481 T0010 rev3: a sentence describing the group's live conflict-AI run, or
  // null/absent when nothing is running. Until rev3 the run's progress lived
  // ONLY on the generic AI-run surface, which replaced the document column and
  // took this dialog down with it; now the dialog survives its own run, so it
  // has to be the one that says the run is happening. A string (not a boolean)
  // because the host owns i18n and the provider/elapsed values.
  aiRunNotice?: string | null
  // 0481 T0010 rev5 (반려 #1): [AI 호출]을 눌러 요청은 나갔지만 이 브라우저에 아직 실행
  // 기록이 없는 구간. 그 구간이 rev3 까지는 화면에 존재하지 않아서, 누른 사람에게는
  // "눌렀는데 아무 일도 안 일어난다"로 보였다. 호출 성공 여부와 무관하게 호스트가 켜고 끈다.
  aiRunPending?: boolean
}>()
const emit = defineEmits<{
  close: []
  abort: []
  submit: [auto: boolean]
  retry: []
  'ai-invoke': [message: string, auto: boolean]
  'copy-mention': []
  'update:provider': [value: string]
  // 0481 T0010 rev5 (반려 #4): 공급자 목록을 못 읽었을 때 이 다이얼로그 안에서 다시 읽는다.
  'reload-providers': []
}>()

const { t } = useI18n()
const { switchToDirectEdit, switchToChunkView } = useConflictChunks()
const showAiActions = computed(() => !props.hideAiActions)

const selectedConflictIndex = ref(0)
const currentChunkSegment = ref(-1)
const collapsedCommon = ref<Record<string, boolean>>({})
const codeFontRem = ref(0.86)
const conflictMessage = ref('')
// 0481 D0006 §3.2 / L0007 §2.2: [자동] is a per-session UI toggle owned by this
// dialog — it is only ever read at the moment [AI 호출]/[해결 제출] is pressed,
// never persisted here and never sent with anything else this dialog emits.
const autoResolve = ref(false)
const COMMON_COLLAPSE_LINES = 12

const selectedConflictFile = computed(() => props.files[selectedConflictIndex.value] || null)
const allConflictsResolved = computed(
  () => props.files.length > 0 && props.files.every(isFileResolved),
)
const markerGuardText = computed(() => {
  if (allConflictsResolved.value) return t('main.git_finalize.markers_clear')
  const remaining = props.files
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
// 0481 T0010 #2: the guard line is the footer's one sentence, so it has to be able to speak
// for the states that have no files either — otherwise the load error and the empty list are
// the two cases where the operator is told nothing at all.
const guardText = computed(() => {
  if (props.loadStatus === 'error') return props.errorMessage || t('main.git_finalize.load_failed')
  if (!props.files.length) return t('main.git_finalize.no_conflicts_hint')
  return props.errorMessage || markerGuardText.value
})
const guardIcon = computed(() =>
  props.loadStatus !== 'error' && props.files.length && allConflictsResolved.value
    ? 'check-circle'
    : 'warning',
)
/**
 * 0481 T0010 rev5 — the ONE line that describes the state of "AI 호출", in the order the
 * operator meets it. A live run outranks everything; a call that has been sent but has no
 * run entry yet is the window rev3 left blank; and when no call can be made at all, the
 * reason is a SENTENCE on screen, not a title attribute nobody hovers (반려 #4: "어떤
 * 조건에서 안 나오는지 모르겠다"). Absent — the deck's own state — renders nothing.
 */
const aiStrip = computed<{ kind: string; text: string; icon: string; spin: boolean; retry: boolean } | null>(() => {
  if (props.aiRunNotice) {
    return { kind: 'run', text: props.aiRunNotice, icon: 'spinner', spin: true, retry: false }
  }
  if (props.aiRunPending) {
    return { kind: 'run', text: t('main.git_finalize.conflict_ai_starting'), icon: 'spinner', spin: true, retry: false }
  }
  if (props.providerLoading) {
    return { kind: 'wait', text: t('main.git_finalize.provider_loading'), icon: 'spinner', spin: true, retry: false }
  }
  if (props.providerErrored) {
    return { kind: 'blocked', text: t('main.git_finalize.provider_load_failed'), icon: 'warning', spin: false, retry: true }
  }
  if (!props.providers?.length) {
    return { kind: 'blocked', text: t('main.git_finalize.provider_none'), icon: 'warning', spin: false, retry: true }
  }
  return null
})
// The same answer for the button that cannot be pressed. 0441 TR0005: a control that cannot
// run right now stays visible and disabled — but it also has to say which of the reasons it is.
const invokeBlockedReason = computed(() => {
  if (props.aiRunNotice) return props.aiRunNotice
  if (props.aiRunPending) return t('main.git_finalize.conflict_ai_starting')
  if (props.providerLoading) return t('main.git_finalize.provider_loading')
  if (props.providerErrored) return t('main.git_finalize.provider_load_failed')
  if (!props.providers?.length) return t('main.git_finalize.provider_none')
  return ''
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
  props.files.reduce((total, file) => total + chunkIndexes(file).length, 0),
)
const resolvedChunkTotal = computed(() =>
  props.files.reduce(
    (total, file) => total + file.segments.filter(
      (seg): seg is ChunkSegment => seg.kind === 'chunk' && !!seg.resolution,
    ).length,
    0,
  ),
)
const progressPercent = computed(() =>
  totalChunkCount.value
    ? Math.round((resolvedChunkTotal.value / totalChunkCount.value) * 100) + '%'
    : '0%',
)
const aiSuggestionTotal = computed(() =>
  props.files.reduce(
    (total, file) => total + file.segments.filter(
      (seg): seg is ChunkSegment => seg.kind === 'chunk' && recommendChunkChoice(seg) !== null,
    ).length,
    0,
  ),
)
const aiSuggestionRemaining = computed(() =>
  props.files.reduce(
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
function chunkDiff(seg: Pick<ChunkSegment, 'ours' | 'theirs'>): ChunkSideDiff {
  return buildChunkSideDiff(seg.ours, seg.theirs)
}
// The merged-base column renders as plain context: diffing base against itself
// marks every line and token 'common', so it shows line numbers with no diff
// highlight. It needs its own helper because chunkDiff() takes a whole
// ChunkSegment, not a pair of sides.
function baseDiff(seg: ChunkSegment): DiffLine[] {
  return buildChunkSideDiff(seg.base, seg.base).ours
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
  for (const file of props.files) {
    for (const seg of file.segments) {
      if (seg.kind !== 'chunk' || seg.resolution) continue
      const recommendation = recommendedChoice(seg)
      if (recommendation) applyChunkChoice(seg, recommendation)
    }
  }
}
function selectConflictFile(index: number) {
  selectedConflictIndex.value = index
  const file = props.files[index]
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
    // 0207 CH0005: jump to a numbered chunk by scrolling ONLY the code area
    // (.git-chunk-scroll). scrollIntoView() walks up and scrolls EVERY scrollable
    // ancestor — including the overflow:hidden .git-conflict-dialog shell — which
    // pushed the fixed header/footer out of the clipped dialog. Confine the
    // scroll to the code container and center the chunk.
    const el = document.getElementById(chunkDomId(segmentIndex))
    const scroller = el?.closest('.git-chunk-scroll') as HTMLElement | null
    if (!el || !scroller) return
    const elRect = el.getBoundingClientRect()
    const scRect = scroller.getBoundingClientRect()
    const centered = (elRect.top - scRect.top) - (scroller.clientHeight - el.clientHeight) / 2
    scroller.scrollTo({ top: scroller.scrollTop + centered, behavior: 'smooth' })
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
  props.files.forEach((file, fileIndex) => {
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
  side: 'ours' | 'base' | 'theirs',
  lineIndex: number,
): number {
  const seg = file.segments[segmentIndex]
  if (!seg || seg.kind !== 'chunk') return lineIndex + 1
  let offset: number
  if (side === 'ours') {
    offset = 1
  } else if (side === 'base') {
    offset = 1 + seg.ours.length + 1
  } else {
    offset = 1 + seg.ours.length + (seg.baseLine ? 1 + seg.base.length : 0) + 1
  }
  return segmentStartLine(file, segmentIndex) + offset + lineIndex
}
function stripLineEnding(line: string): string {
  return line.replace(/\r\n$|\n$|\r$/, '')
}

// A fresh files array (initial open or refetch) resets view-state: first file,
// first chunk focused, long common blocks re-collapsed.
watch(
  () => props.files,
  (files) => {
    selectedConflictIndex.value = 0
    resetCommonCollapse()
    autoResolve.value = false
    const firstFile = files[0]
    currentChunkSegment.value = firstFile ? (chunkIndexes(firstFile)[0] ?? -1) : -1
  },
  { immediate: true },
)
</script>

<style scoped>
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
.git-conflict-message-bar {
  flex: 0 0 auto;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  align-items: center;
  gap: 10px;
  padding: 10px 18px;
  border-top: 1px solid var(--border, #e2e8f0);
  background: var(--surface, #fff);
}
.git-conflict-message-bar label {
  color: var(--text-s, #475569);
  font-size: 0.76rem;
  font-weight: 700;
  white-space: nowrap;
}
.git-conflict-message-bar textarea {
  width: 100%;
  min-height: 48px;
  padding: 8px 10px;
  border: 1px solid var(--border, #cbd5e1);
  border-radius: 6px;
  background: var(--bg, #fff);
  color: var(--text, #0f172a);
  font: inherit;
  font-size: 0.78rem;
  line-height: 1.45;
  resize: none;
}
.git-conflict-message-bar textarea:focus {
  border-color: var(--primary, #2563eb);
  outline: 2px solid color-mix(in srgb, var(--primary, #2563eb) 18%, transparent);
}
.git-conflict-message-bar textarea:disabled {
  opacity: 0.65;
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
.git-conflict-progress {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 0.76rem;
  color: var(--text-m);
  white-space: nowrap;
}
.git-conflict-progress-bar {
  width: 130px;
  height: 7px;
  border-radius: 999px;
  background: var(--border, #e2e8f0);
  overflow: hidden;
}
.git-conflict-progress-fill {
  height: 100%;
  background: #16a34a;
  transition: width 0.25s ease;
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
.git-conflict-load-error,
.git-conflict-empty {
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
/* 0481 D0006 §6.2 v13 화면 1 `.git-conflict-footer-context`: 가드 문장 · 세로 구분선 ·
   AI 호출 옵션이 한 덩어리로 왼쪽에 서고, 남는 폭은 가드 문장이 먹는다. */
.git-conflict-footer-context {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 12px;
}
.git-conflict-footer-context .ft-divider {
  flex: 0 0 auto;
  width: 1px;
  height: 20px;
  background: var(--border, #e2e8f0);
}
.git-conflict-invoke-options {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 10px;
}
/* 0481 T0010 rev5 (반려 #2): 실행/차단 문구는 액션바 위의 제 줄이다. `.git-ai-assist-strip`
   과 같은 전폭 띠라서 가드 문장의 폭을 한 픽셀도 빼앗지 않는다. */
.git-conflict-ai-strip {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 18px;
  border-top: 1px solid #bfdbfe;
  background: #eff6ff;
  color: #1d4ed8;
  font-size: 0.76rem;
}
.git-conflict-ai-strip-text {
  flex: 1 1 auto;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.git-conflict-ai-strip--blocked {
  border-top-color: #fecaca;
  background: #fef2f2;
  color: #b91c1c;
}
.git-conflict-ai-strip--wait {
  border-top-color: var(--border, #e2e8f0);
  background: #f8fafc;
  color: var(--text-m, #64748b);
}
.git-conflict-ai-strip-retry {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  border: 1px solid currentColor;
  border-radius: 6px;
  background: transparent;
  color: inherit;
  font-size: 0.72rem;
  white-space: nowrap;
  cursor: pointer;
}
.git-conflict-ai-strip-retry:disabled {
  opacity: 0.5;
  cursor: default;
}
/* 시안 v13 화면 1: `.git-conflict-guard { white-space: nowrap }`. 폭이 모자라면 줄을
   늘리는 게 아니라 말줄임한다 — 늘리면 다이얼로그 아래가 문장에 먹힌다. */
.git-conflict-guard {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  color: #b45309;
  font-size: 0.76rem;
  white-space: nowrap;
}
.git-conflict-guard.ok {
  color: #15803d;
}
.git-conflict-guard span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.git-conflict-footer-actions {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 10px;
}
.git-conflict-provider {
  flex: 0 1 210px;
  max-width: 210px;
}
.git-conflict-auto-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 0 4px;
  color: var(--text-m, #475569);
  font-size: 0.76rem;
  white-space: nowrap;
  cursor: pointer;
}
.git-conflict-auto-toggle.disabled {
  opacity: 0.6;
  cursor: default;
}
.git-conflict-auto-toggle input {
  cursor: inherit;
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
.git-empty-side { display: block; padding: 4px 10px; color: #94a3b8; font-style: italic; }
.git-code-line.diff-removed {
  background: #fff1f2;
}
.git-code-line.diff-added {
  background: #ecfdf5;
}
.git-code-line.diff-changed {
  background: #fff7ed;
}
.git-code-line.diff-common {
  background: transparent;
}
.git-code-token.diff-token-removed,
.git-code-token.diff-token-added,
.git-code-token.diff-token-changed {
  border-radius: 3px;
  padding: 0 1px;
}
.git-code-token.diff-token-removed {
  color: #9f1239;
  background: #ffe4e6;
}
.git-code-token.diff-token-added {
  color: #047857;
  background: #bbf7d0;
}
.git-code-token.diff-token-changed {
  color: #9a3412;
  background: #fed7aa;
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
  .git-conflict-message-bar {
    grid-template-columns: 1fr;
    gap: 6px;
  }
  .git-conflict-footer-actions {
    justify-content: flex-end;
  }
}
</style>
