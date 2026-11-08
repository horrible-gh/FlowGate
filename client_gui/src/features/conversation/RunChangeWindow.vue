<template>
  <ModelessShell
    ref="shell"
    :open="!closed"
    :title="title"
    :initial-x="80 + (index % 8) * 24"
    :initial-y="80 + (index % 8) * 24"
    :initial-width="800"
    :initial-height="520"
    :min-width="360"
    :min-height="240"
    v-model:minimized="minimized"
    @close="close"
  >
    <section class="rc-window" data-test="rc-window" :data-run-id="activity.runId" role="dialog" :aria-label="title">
      <ul class="rc-window-files" data-test="rc-window-files" :aria-label="t('gui.conversation.change.files', { n: activity.files.length })">
        <li v-for="file in activity.files" :key="file.path">
          <button
            type="button"
            class="rc-window-file"
            data-test="rc-window-file"
            :data-path="file.path"
            :aria-pressed="selectedPath === file.path"
            :title="t('gui.conversation.change.open_file', { path: file.path })"
            @click="selectFile(file.path)"
          >
            <span>{{ file.status ?? '?' }}</span>
            <span class="rc-window-path">{{ file.path }}</span>
            <span class="rc-window-stats">
              {{ file.insertions == null && file.deletions == null ? t('gui.conversation.change.no_stats') : `+${file.insertions ?? 0} −${file.deletions ?? 0}` }}
            </span>
          </button>
        </li>
      </ul>
      <div class="rc-window-detail">
        <RunChangeDiff v-if="selectedDiff" :file-diff="selectedDiff" />
        <p v-else data-test="rc-window-empty">{{ t('gui.conversation.diff.empty') }}</p>
        <button v-if="selectedDiff?.status === 'error'" type="button" data-test="rc-window-retry" @click="selectFile(selectedDiff.path)">
          {{ t('gui.conversation.retry') }}
        </button>
      </div>
    </section>
  </ModelessShell>
</template>

<script setup lang="ts">
// flowgate.v02.0151 T-B: each run owns a modeless viewer. Only recorded run diffs are read;
// file states never enter the chat session or its timeline. Closing destroys the cache.
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import ModelessShell from '../../components/ui/ModelessShell.vue'
import RunChangeDiff from './RunChangeDiff.vue'
import type { OpenFileDiff } from './changeDiff'
import type { ConversationSource } from './conversationSource'
import type { WorkChangeActivity } from './types'

const props = defineProps<{
  chatId: string
  activity: WorkChangeActivity
  requestedPath?: string
  requestVersion: number
  index: number
  loadDiff: ConversationSource['fetchRunFileDiff']
}>()
const emit = defineEmits<{ close: [] }>()
const { t } = useI18n()
const shell = ref<InstanceType<typeof ModelessShell> | null>(null)
const minimized = ref(false)
const closed = ref(false)
const selectedPath = ref<string | null>(null)
const files = reactive(new Map<string, OpenFileDiff>())
const selectedDiff = computed(() => selectedPath.value === null ? null : files.get(selectedPath.value) ?? null)
const title = computed(() => t('gui.conversation.change.window_title', { run: props.activity.runId }))
let generation = 0

function invalidate(): void {
  generation += 1
  files.clear()
  selectedPath.value = null
}

function close(): void {
  closed.value = true
  invalidate()
  emit('close')
}

async function selectFile(path: string): Promise<void> {
  if (closed.value || !props.activity.files.some((file) => file.path === path)) return
  selectedPath.value = path
  // A second click never toggles the viewer closed or duplicates a pending read.
  if (files.has(path) && files.get(path)!.status !== 'error') return
  const chatId = props.chatId
  const runId = props.activity.runId
  const gen = generation
  files.set(path, { runId, path, status: 'loading', diff: null, error: null })
  const current = () => !closed.value && gen === generation && props.chatId === chatId && props.activity.runId === runId
  try {
    const diff = await props.loadDiff(chatId, runId, path)
    if (!current()) return
    if (diff.runId !== runId || diff.path !== path) throw new Error('run_diff_identity_mismatch')
    files.set(path, { runId, path, status: 'ready', diff, error: null })
  } catch (error) {
    if (current()) files.set(path, { runId, path, status: 'error', diff: null, error: error instanceof Error ? error.message : String(error) })
  }
}

watch(() => [props.chatId, props.activity.runId], invalidate, { flush: 'sync' })
watch(() => [props.chatId, props.activity.runId, props.requestVersion], async () => {
  minimized.value = false
  const path = props.requestedPath ?? selectedPath.value ?? props.activity.files[0]?.path
  if (path) void selectFile(path)
  await nextTick()
  if (!closed.value) shell.value?.bringToFront()
}, { immediate: true })
onBeforeUnmount(() => {
  closed.value = true
  invalidate()
})
</script>

<style scoped>
.rc-window {
  display: grid;
  grid-template-columns: minmax(140px, 30%) minmax(0, 1fr);
  gap: 10px;
  height: 100%;
  min-height: 0;
  color: var(--fg2-text);
  font-family: var(--fg2-font);
  font-size: 11px;
}
.rc-window-files {
  list-style: none;
  margin: 0;
  padding: 0;
  overflow: auto;
  border-right: 1px solid var(--fg2-line);
}
.rc-window-file {
  width: 100%;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 4px 6px;
  padding: 7px;
  border: 0;
  text-align: left;
  background: transparent;
  color: inherit;
  font: inherit;
  cursor: pointer;
}
.rc-window-file[aria-pressed='true'] {
  background: var(--fg2-status-info-bg);
}
.rc-window-file:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: -2px;
}
.rc-window-path { overflow-wrap: anywhere; }
.rc-window-stats { grid-column: 2; color: var(--fg2-muted); }
.rc-window-detail {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}
.rc-window-detail > .rc-diff { flex: 1; }
.rc-window-detail > button { align-self: flex-start; margin-top: 6px; }
</style>
