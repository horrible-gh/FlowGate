<template>
  <ModelessShell
    :open="true"
    :title="win.title"
    :minimized="win.minimized"
    :initial-x="initialX"
    :initial-y="72"
    :initial-width="width"
    :initial-height="height"
    :min-width="520"
    :min-height="360"
    @update:minimized="(value: boolean) => session.setWindowMinimized(win.id, value)"
    @close="session.closeWindow(win.id)"
  >
    <div class="gx-window gx-resolver" data-test="git-conflict-window">
      <div v-if="conflict && conflict.files.length" class="gx-resolver-files" role="tablist" data-test="git-resolver-files">
        <button
          v-for="item in conflict.files"
          :key="item.path"
          type="button"
          role="tab"
          class="gx-resolver-file"
          :aria-selected="item.path === file?.path ? 'true' : 'false'"
          :data-state="item.conflictCount > 0 ? 'open' : 'done'"
          :data-path="item.path"
          @click="select(item.path)"
        >
          <span class="gx-file-st" :class="item.conflictCount > 0 ? 'st-c' : 'st-m'">{{ item.conflictCount > 0 ? 'C' : 'M' }}</span>
          <span class="gx-file-nm">{{ item.path }}</span>
        </button>
      </div>
      <p v-else-if="session.state.conflictLoad === 'loading'" class="gx-desc">{{ t('gui.git.conflict.loading') }}</p>
      <p v-else-if="!conflict" class="gx-desc" data-test="git-resolver-gone">{{ t('gui.git.resolver.gone') }}</p>

      <p v-if="allDone" class="gx-resolver-done" data-test="git-resolver-all-done">{{ t('gui.git.resolver.all_done') }}</p>

      <template v-if="file">
        <p v-if="file.conflictCount === 0" class="gx-desc" data-test="git-resolver-file-done">{{ t('gui.git.resolver.file_done', { path: file.path }) }}</p>
        <p v-else-if="!segments || !chunks.length" class="gx-notice" data-test="git-resolver-malformed">{{ t('gui.git.resolver.malformed') }}</p>
        <div v-else class="gx-resolver-body" data-test="git-resolver-body">
          <template v-for="(segment, index) in segments" :key="index">
            <pre v-if="segment.kind === 'common' && contextOf(segment.lines, index).length" class="gx-resolver-context">{{ contextOf(segment.lines, index).join('\n') }}</pre>
            <section v-else-if="segment.kind === 'chunk'" class="gx-chunk" :data-chunk="segment.chunk.number" :data-choice="drafts[segment.chunk.number]?.choice ?? ''">
              <div class="gx-chunk-head">
                <span class="gx-kicker">{{ t('gui.git.resolver.chunk', { n: segment.chunk.number, total: chunks.length }) }}</span>
                <div class="gx-chunk-picks">
                  <button type="button" class="gx-btn sm" :class="{ primary: drafts[segment.chunk.number]?.choice === 'ours' }" :disabled="locked" data-test="git-chunk-ours" @click="choose(segment.chunk, 'ours')">{{ t('gui.git.resolver.use_ours') }}</button>
                  <button type="button" class="gx-btn sm" :class="{ primary: drafts[segment.chunk.number]?.choice === 'theirs' }" :disabled="locked" data-test="git-chunk-theirs" @click="choose(segment.chunk, 'theirs')">{{ t('gui.git.resolver.use_theirs') }}</button>
                  <button type="button" class="gx-btn sm" :class="{ primary: drafts[segment.chunk.number]?.choice === 'both' }" :disabled="locked" data-test="git-chunk-both" @click="choose(segment.chunk, 'both')">{{ t('gui.git.resolver.use_both') }}</button>
                </div>
              </div>
              <div class="gx-chunk-sides">
                <div class="gx-chunk-side ours">
                  <div class="gx-chunk-label">{{ t('gui.git.resolver.ours', { branch: oursLabel }) }}</div>
                  <pre>{{ segment.chunk.ours.join('\n') || ' ' }}</pre>
                </div>
                <div class="gx-chunk-side theirs">
                  <div class="gx-chunk-label">{{ t('gui.git.resolver.theirs', { branch: theirsLabel }) }}</div>
                  <pre>{{ segment.chunk.theirs.join('\n') || ' ' }}</pre>
                </div>
              </div>
              <label class="gx-chunk-label" :for="`gx-chunk-${segment.chunk.number}`">{{ t('gui.git.resolver.result') }}</label>
              <textarea
                :id="`gx-chunk-${segment.chunk.number}`"
                class="gx-input gx-textarea gx-chunk-result"
                :value="drafts[segment.chunk.number]?.text ?? ''"
                :placeholder="t('gui.git.resolver.result_placeholder')"
                :rows="Math.min(12, Math.max(3, segment.chunk.ours.length + segment.chunk.theirs.length))"
                :disabled="locked"
                spellcheck="false"
                data-test="git-chunk-result"
                @input="edit(segment.chunk.number, ($event.target as HTMLTextAreaElement).value)"
              />
            </section>
          </template>
        </div>
        <div v-if="file.conflictCount > 0 && chunks.length" class="gx-resolver-foot">
          <span class="gx-reason" data-test="git-resolver-progress">{{ t('gui.git.resolver.progress', { done: decided, total: chunks.length }) }}</span>
          <button type="button" class="gx-btn primary" :disabled="locked || decided < chunks.length" data-test="git-resolver-save" @click="save">
            {{ t('gui.git.resolver.save') }}
          </button>
        </div>
        <p v-if="lockedText" class="gx-reason" data-test="git-resolver-locked">{{ lockedText }}</p>
      </template>
      <p v-if="noticeText" class="gx-notice" data-test="git-notice">{{ noticeText }}</p>
    </div>
  </ModelessShell>
</template>

<script setup lang="ts">
// flowgate.v02.0033 — conflict modeless (0033-D §6.3) on the non-blocking ModelessShell.
// flowgate.v02gui.0004 (0001-B §A-3/§A-4) — this is now the direct resolver, opened over the
// centre from the Git tab's conflict card (a file, [직접 해결], `외 N개 보기`). The duplicate
// AI / 직접 해결 buttons and the technical block are gone: the card is the one entry point.
// A file's chunks are shown side by side (current = the branch the merge lands on, incoming = the
// group branch); each chunk takes 현재 / 들어온 쪽 / 둘 다 or a typed result, and [저장] sends the
// chunks to the existing POST …/resolve. The conflicts answer read right after decides the file's
// state; once no file has markers left the review card's [승인] continues the apply.
// Unsaved choices are kept per merge / file / file content in this browser session, so a tab
// switch, a closed window or F5 does not lose them; saved ones live on the server.
import { computed, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import ModelessShell from '../../components/ui/ModelessShell.vue'
import { chunkText, conflictChunks, splitConflictSegments, type ChunkChoice, type ConflictChunk } from './conflictChunks'
import { useGitPanel } from './gitSession'
import type { GitWindowDescriptor } from './types'

const props = defineProps<{ win: GitWindowDescriptor }>()
const { t } = useI18n()
const ctx = useGitPanel()
const session = ctx.session

const viewport = typeof window !== 'undefined' ? window : null
const width = Math.min(920, Math.max(560, (viewport?.innerWidth ?? 1280) - 480))
const height = Math.min(680, Math.max(400, (viewport?.innerHeight ?? 800) - 140))
// Over the centre of the Designer, clear of the right Tool Panel.
const initialX = Math.max(24, Math.round(((viewport?.innerWidth ?? 1280) - width) / 2) - 120)

const conflict = computed(() => {
  const value = session.state.conflict
  return value && value.mergeId === props.win.mergeId ? value : null
})
const selected = ref<string | null>(props.win.path ?? null)
watch(
  () => props.win.path,
  (path) => {
    if (path) selected.value = path
  },
)
const file = computed(() => {
  const files = conflict.value?.files ?? []
  return files.find((f) => f.path === selected.value) ?? files.find((f) => f.conflictCount > 0) ?? files[0] ?? null
})
const allDone = computed(() => !!conflict.value && conflict.value.files.length > 0 && conflict.value.files.every((f) => f.conflictCount === 0))
const segments = computed(() => (file.value && file.value.conflictCount > 0 ? splitConflictSegments(file.value.content) : null))
const chunks = computed(() => conflictChunks(segments.value))
const oursLabel = computed(() => conflict.value?.targetBranch ?? conflict.value?.baseBranch ?? 'HEAD')
const theirsLabel = computed(() => conflict.value?.branch ?? t('gui.git.resolver.incoming'))

const resolving = computed(() => session.groupCard.value === 'ai')
const locked = computed(() => !session.canAct.value || !session.conflictActionable.value || resolving.value)
const lockedText = computed(() => {
  if (!file.value || file.value.conflictCount === 0) return null
  if (resolving.value) return t('gui.git.resolver.ai_running')
  if (session.state.readOnly) return t('gui.git.conflict.blocked.read_only')
  if (session.state.forbidden) return t('gui.git.conflict.blocked.forbidden')
  return null
})

const noticeText = computed(() => {
  const notice = session.state.notices.window
  return notice ? t(`gui.git.notice.${notice.code}`, { ...(notice.params ?? {}) }) : null
})

// ── Drafts ──────────────────────────────────────────────────────────────────────────
interface ChunkDraft {
  choice: ChunkChoice
  text: string
}
const STORE_PREFIX = 'flowgate.git.resolveDraft.'
function hash(text: string): string {
  let h = 0
  for (let i = 0; i < text.length; i += 1) h = (Math.imul(31, h) + text.charCodeAt(i)) | 0
  return `${text.length}.${(h >>> 0).toString(36)}`
}
const draftKey = computed(() =>
  file.value && conflict.value ? `${STORE_PREFIX}${props.win.groupId}:${conflict.value.mergeId}:${file.value.path}:${hash(file.value.content)}` : null,
)
const drafts = reactive<Record<number, ChunkDraft>>({})
function loadDrafts(key: string | null): void {
  for (const n of Object.keys(drafts)) delete drafts[Number(n)]
  if (!key) return
  try {
    const raw = globalThis.sessionStorage?.getItem(key)
    const parsed = raw ? (JSON.parse(raw) as Record<string, ChunkDraft>) : {}
    for (const [n, draft] of Object.entries(parsed)) {
      if (draft && typeof draft.text === 'string' && typeof draft.choice === 'string') drafts[Number(n)] = draft
    }
  } catch {
    // a broken entry is just not restored
  }
}
function storeDrafts(): void {
  const key = draftKey.value
  if (!key) return
  try {
    if (Object.keys(drafts).length) globalThis.sessionStorage?.setItem(key, JSON.stringify(drafts))
    else globalThis.sessionStorage?.removeItem(key)
  } catch {
    // storage is only a convenience
  }
}
watch(draftKey, (key) => loadDrafts(key), { immediate: true })

const decided = computed(() => chunks.value.filter((c) => !!drafts[c.number]).length)

function choose(chunk: ConflictChunk, choice: Exclude<ChunkChoice, 'manual'>): void {
  if (locked.value) return
  drafts[chunk.number] = { choice, text: chunkText(chunk, choice) }
  storeDrafts()
}
function edit(number: number, text: string): void {
  if (locked.value) return
  drafts[number] = { choice: 'manual', text }
  storeDrafts()
}

function select(path: string): void {
  selected.value = path
  session.dismissNotice('window')
}

// Context around chunks: the last lines before the first chunk, a few lines on each side between
// chunks, the first lines after the last one. Not editable — only what a chunk replaces is sent.
const CONTEXT = 3
function contextOf(lines: readonly string[], index: number): string[] {
  const last = (segments.value?.length ?? 1) - 1
  if (index === 0) return lines.slice(-CONTEXT)
  if (index === last) return lines.slice(0, CONTEXT)
  if (lines.length <= CONTEXT * 2) return [...lines]
  return [...lines.slice(0, CONTEXT), `… ${t('gui.git.resolver.lines_hidden', { n: lines.length - CONTEXT * 2 })} …`, ...lines.slice(-CONTEXT)]
}

async function save(): Promise<void> {
  const current = file.value
  const key = draftKey.value
  if (!current || locked.value || decided.value < chunks.value.length) return
  const payload = chunks.value.map((c) => ({ chunk: c.number, content: drafts[c.number].text }))
  if (!(await session.resolveFile(current.path, payload))) return
  try {
    if (key) globalThis.sessionStorage?.removeItem(key)
  } catch {
    // nothing to clean
  }
  // Next file still open, if any.
  const next = conflict.value?.files.find((f) => f.conflictCount > 0 && f.path !== current.path)
  if (next) selected.value = next.path
}
</script>
