<template>
  <ModelessShell
    :open="true"
    :initial-x="x"
    :initial-y="y"
    :initial-width="640"
    :initial-height="520"
    :min-width="380"
    :min-height="260"
    @close="close"
  >
    <template #header>
      <span class="wpj-heading" data-test="wp-json-heading">
        <span class="wpj-kind">{{ mode === 'raw' ? t('json.raw_title') : t('json.import_title') }}</span>
        <span class="wpj-doc" :title="docTitle">{{ docTitle }}</span>
      </span>
    </template>

    <div class="wpj" data-test="wp-json-window" :data-mode="mode">
      <template v-if="mode === 'raw'">
        <Textarea class="wpj-text" :model-value="rawText" readonly :rows="18" :aria-label="t('json.raw_title')" data-test="wp-json-raw" />
        <div class="wpj-bar">
          <p class="wpj-note" :class="{ 'is-danger': copied === false }" role="status" data-test="wp-json-copy-state">
            {{ copied === true ? t('json.copied') : copied === false ? t('json.copy_failed') : t('json.raw_hint') }}
          </p>
          <Button size="sm" variant="secondary" :disabled="rawText === ''" data-test="wp-json-copy" @click="copy">{{ t('json.copy') }}</Button>
          <Button size="sm" variant="ghost" data-test="wp-json-close" @click="close">{{ t('close') }}</Button>
        </div>
      </template>

      <template v-else>
        <p class="wpj-note">{{ t('json.import_hint') }}</p>
        <p v-if="blocked" class="wpj-note is-warning" role="status" data-test="wp-json-blocked" :data-reason="blocked">{{ t(`json.blocked.${blocked}`) }}</p>
        <Textarea
          v-model="pasted"
          class="wpj-text"
          :placeholder="t('json.paste')"
          :disabled="blocked !== null || reading"
          :invalid="issues.length > 0"
          :rows="14"
          :aria-label="t('json.paste')"
          data-test="wp-json-input"
          @update:model-value="issues = []"
        />
        <ul v-if="issues.length || readFailed" class="wpj-issues" role="alert" data-test="wp-json-issues">
          <li v-if="readFailed" data-test="wp-json-issue" data-code="read_failed">{{ t('json.read_failed') }}</li>
          <li v-for="(issue, index) in issues" :key="index" data-test="wp-json-issue" :data-code="issue.code">{{ issueText(issue) }}</li>
        </ul>
        <input ref="fileInput" class="wpj-file" type="file" accept=".json,application/json" tabindex="-1" aria-hidden="true" data-test="wp-json-file" @change="onFile" />
        <div class="wpj-bar">
          <Button size="sm" variant="secondary" :disabled="blocked !== null || reading" data-test="wp-json-choose" @click="fileInput?.click()">
            {{ reading ? t('json.reading') : t('json.choose_file') }}
          </Button>
          <span class="wpj-grow" />
          <Button size="sm" variant="ghost" data-test="wp-json-cancel" @click="close">{{ t('confirm.cancel') }}</Button>
          <Button size="sm" variant="primary" :disabled="blocked !== null || reading || pasted.trim() === ''" data-test="wp-json-apply" @click="apply(pasted)">
            {{ t('json.apply') }}
          </Button>
        </div>
      </template>
    </div>
  </ModelessShell>
</template>

<script setup lang="ts">
// flowgate.v02.0073 T#4 — the work plan edit window's JSON window, a ModelessShell of its own (it moves, resizes,
// minimizes and closes like the plan window; Esc closes it): `raw` shows the draft as JSON, read only, as the save would
// send it (unsaved edits included, following every edit) with [복사]; `import` takes a JSON file or a pasted text and
// hands it to the editor (importJson: checked, then the draft — dirty; the save stays the plan window's [저장]). A JSON
// the client refuses lists why, in the person's words (a type by its name, a Step by its place — never a type code);
// the server's own refusals come with the save, on the plan window. Locked, or a Step window holding edits: no import.
// Not the Inspector's document upload / download (DocumentIoGroup, 0068). v0.2 parts and theme tokens only.
// A file still being read when the window closes (취소, ×, Esc, the plan window closing) or turns into the other mode
// (the plan window mounts a new JSON window per mode) is dropped: nothing is imported and nothing is emitted for it.
import { computed, onBeforeUnmount, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '@/components/ui/Button.vue'
import ModelessShell from '@/components/ui/ModelessShell.vue'
import Textarea from '@/components/ui/Textarea.vue'
import { copyToClipboard } from '@/core/interaction/clipboard'
import { documentIoReadFile } from '@/features/documentWindow/documentIo'
import { workPlanQuantityNameKey } from '@/features/documentWindow/specialDocumentModel'
import { workPlanWindowMessages } from './messages'
import type { WorkPlanEditor } from './useWorkPlanEditor'
import { WORK_PLAN_SET_TYPES } from './workPlanEditModel'
import type { WorkPlanJsonIssue } from './workPlanJson'

const props = defineProps<{
  editor: WorkPlanEditor
  mode: 'raw' | 'import'
  docTitle: string
  /** A Step window of this plan holds edits (no import until they are saved or dropped). */
  stepPending: boolean
  x: number
  y: number
}>()

const emit = defineEmits<{ close: [] }>()

const { t } = useI18n({ useScope: 'local', messages: workPlanWindowMessages, inheritLocale: true })
const { t: gt } = useI18n({ useScope: 'global' })

// --- raw -----------------------------------------------------------------------------------------------------------

const rawText = computed(() => (props.editor.draft.value ? props.editor.exportJson() ?? '' : ''))
const copied = ref<boolean | null>(null)

async function copy(): Promise<void> {
  copied.value = await copyToClipboard(rawText.value)
}

// --- import --------------------------------------------------------------------------------------------------------

const pasted = ref('')
const issues = ref<readonly WorkPlanJsonIssue[]>([])
const reading = ref(false)
const readFailed = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

const blocked = computed<'locked' | 'step_pending' | null>(() => {
  if (props.editor.inputsLocked.value) return 'locked'
  return props.stepPending ? 'step_pending' : null
})

/** Moves on every close and on the unmount: a file read started before it no longer belongs to this window. */
let generation = 0

function close(): void {
  generation += 1
  emit('close')
}

onBeforeUnmount(() => {
  generation += 1
})

/** Hands the text to the editor; taken (or the same plan), the window closes. */
function apply(text: string): void {
  readFailed.value = false
  const result = props.editor.importJson(text)
  if (result.status === 'invalid') issues.value = result.issues
  else if (result.status === 'imported' || result.status === 'unchanged') close()
}

async function onFile(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0] ?? null
  // The same file may be picked again after a fix.
  input.value = ''
  if (!file || blocked.value !== null) return
  const mine = generation
  reading.value = true
  readFailed.value = false
  issues.value = []
  let text: string | null
  try {
    text = await documentIoReadFile(file)
  } catch {
    text = null
  }
  // Closed or remounted in the other mode while reading: the file is not this window's any more.
  if (mine !== generation) return
  reading.value = false
  if (text === null) {
    readFailed.value = true
    return
  }
  pasted.value = text
  apply(text)
}

function typeName(type: string): string {
  const unit = WORK_PLAN_SET_TYPES.includes(type) ? 'set' : 'sheet'
  return gt(`gui.document_window.${workPlanQuantityNameKey({ type, unit })}`)
}

function issueText(issue: WorkPlanJsonIssue): string {
  return t(`json.issue.${issue.code}`, {
    field: issue.field ?? '',
    type: issue.type ? typeName(issue.type) : '',
    n: issue.n ?? 0,
    version: issue.version ?? '',
    expected: issue.expected ?? '',
  })
}
</script>

<style scoped>
.wpj-heading {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  max-width: 100%;
}

.wpj-kind {
  flex: 0 0 auto;
}

.wpj-doc {
  overflow: hidden;
  color: var(--fg2-muted);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wpj {
  display: flex;
  flex-direction: column;
  gap: 8px;
  height: 100%;
  min-height: 0;
  color: var(--fg2-text);
  font-size: 12.5px;
}

.wpj-text {
  flex: 1;
  min-height: 0;
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 12px;
  resize: none;
}

.wpj-note {
  flex: 1;
  min-width: 0;
  margin: 0;
  color: var(--fg2-muted);
  font-size: 12px;
}

.wpj-note.is-warning {
  flex: none;
  color: var(--fg2-status-warning-fg);
}

.wpj-note.is-danger,
.wpj-issues {
  color: var(--fg2-status-danger-fg);
}

.wpj-issues {
  flex: none;
  max-height: 96px;
  margin: 0;
  overflow: auto;
  padding-left: 16px;
  font-size: 12px;
}

.wpj-file {
  display: none;
}

.wpj-bar {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--fg2-line);
}

.wpj-grow {
  flex: 1;
}
</style>
