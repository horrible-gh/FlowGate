<template>
  <!-- v02test.0074 (시안 v2 · 6): the files an answer links, listed once more under it — per file [미리보기] (inline, text
       or image only) and [다운로드] (the original, straight from the Flow's attachment API), and from two files up
       [모두 받기(ZIP)]. A deleted file is not listed (its chip in the text says so). The name goes to the file's row in
       the attachment window, like the chip's name. -->
  <section class="tl-lf" data-test="tl-linked-files">
    <div class="tl-lf-head">
      <span data-test="tl-linked-count">{{ t('gui.conversation.message.file.linked_n', { n: files.length }) }}</span>
      <Button v-if="files.length >= 2" size="sm" variant="ghost" data-test="tl-linked-zip" :disabled="zipping" @click="zip">
        {{ t('gui.conversation.message.file.zip') }}
      </Button>
    </div>
    <div v-for="file in files" :key="file.key" class="tl-lf-file" data-test="tl-linked-file" :data-attachment-key="file.key">
      <div class="tl-lf-row">
        <span class="tl-lf-ico" aria-hidden="true">{{ isImageName(file.name) ? '🖼' : '📄' }}</span>
        <div class="tl-lf-meta">
          <button
            type="button"
            class="tl-lf-name"
            data-test="tl-linked-name"
            :title="t('gui.conversation.message.file.open', { name: file.name })"
            @click="open(file)"
          >{{ file.name }}</button>
          <span class="tl-lf-sub">
            <span v-if="file.size != null" data-test="tl-linked-size">{{ formatFileSize(file.size) }}</span>
            <span v-if="file.replaced" class="tl-lf-replaced" data-test="tl-linked-replaced">{{ t('gui.conversation.message.file.replaced') }}</span>
          </span>
        </div>
        <Button size="sm" variant="ghost" data-test="tl-linked-preview" :aria-expanded="shown === file.key" @click="togglePreview(file)">
          {{ shown === file.key ? t('gui.conversation.message.file.hide_preview') : t('gui.conversation.message.file.preview') }}
        </Button>
        <!-- v02test.0074: icon only, so the file name and size keep their room in the 300px panel; the name stays in the
             accessible label and the tooltip. -->
        <Button
          size="sm"
          variant="primary"
          class="tl-lf-download"
          data-test="tl-linked-download"
          :aria-label="t('gui.conversation.message.file.download', { name: file.name })"
          :title="t('gui.conversation.message.file.download', { name: file.name })"
          :disabled="busy.includes(file.key)"
          @click="download(file)"
        >
          <span aria-hidden="true">⬇</span>
        </Button>
      </div>
      <div v-if="shown === file.key" class="tl-lf-preview" data-test="tl-linked-preview-body">
        <p v-if="preview.state === 'loading'" class="tl-lf-muted">{{ t('gui.conversation.message.file.preview_loading') }}</p>
        <p v-else-if="preview.state === 'failed'" class="tl-lf-error" role="alert">{{ t('gui.conversation.message.file.preview_failed') }}</p>
        <p v-else-if="preview.state === 'none'" class="tl-lf-muted" data-test="tl-linked-preview-none">{{ t('gui.conversation.message.file.preview_none') }}</p>
        <img v-else-if="preview.state === 'image'" :src="preview.url" :alt="file.name" class="tl-lf-image" data-test="tl-linked-preview-image" />
        <!-- Text only, never rendered as HTML. -->
        <pre v-else-if="preview.state === 'text'" class="tl-lf-text" data-test="tl-linked-preview-text">{{ preview.text }}</pre>
      </div>
    </div>
    <p v-if="error" class="tl-lf-error" role="alert" data-test="tl-linked-error">{{ error }}</p>
  </section>
</template>

<script setup lang="ts">
import { onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '../../components/ui/Button.vue'
import { formatFileSize, isImageName, previewKindOf, readBlobText, useAttachmentLinkActions, type LinkedFile } from './attachmentLinks'

const props = defineProps<{ files: LinkedFile[] }>()

const { t } = useI18n()
const actions = useAttachmentLinkActions()

const error = ref('')
const busy = ref<string[]>([])
const zipping = ref(false)

async function download(file: LinkedFile): Promise<void> {
  if (busy.value.includes(file.key)) return
  busy.value = [...busy.value, file.key]
  error.value = ''
  const done = await actions.download(file.key, file.name)
  busy.value = busy.value.filter((key) => key !== file.key)
  if (!done) error.value = t('gui.conversation.message.file.download_failed')
}

async function zip(): Promise<void> {
  if (zipping.value) return
  zipping.value = true
  error.value = ''
  const done = await actions.downloadZip(props.files.map((file) => file.key))
  zipping.value = false
  if (!done) error.value = t('gui.conversation.message.file.download_failed')
}

async function open(file: LinkedFile): Promise<void> {
  error.value = ''
  if (!(await actions.focus(file.key))) error.value = t('gui.conversation.message.file.not_found')
}

// One preview open at a time. `turn` drops a read that ends after another file (or none) was chosen.
const shown = ref<string | null>(null)
const preview = reactive<{ state: 'loading' | 'failed' | 'none' | 'image' | 'text'; url: string; text: string }>({ state: 'loading', url: '', text: '' })
let turn = 0

function dropImage(): void {
  if (preview.url) URL.revokeObjectURL(preview.url)
  preview.url = ''
}

function closePreview(): void {
  turn += 1
  shown.value = null
  dropImage()
  preview.text = ''
}

async function togglePreview(file: LinkedFile): Promise<void> {
  if (shown.value === file.key) return closePreview()
  closePreview()
  const mine = turn
  shown.value = file.key
  const kind = previewKindOf(file.name, file.size)
  if (kind === 'none') {
    preview.state = 'none'
    return
  }
  preview.state = 'loading'
  const blob = await actions.read(file.key)
  if (mine !== turn) return
  if (!blob) {
    preview.state = 'failed'
    return
  }
  if (kind === 'image') {
    preview.url = URL.createObjectURL(blob)
    preview.state = 'image'
    return
  }
  try {
    const text = await readBlobText(blob)
    if (mine !== turn) return
    preview.text = text
    preview.state = 'text'
  } catch {
    if (mine === turn) preview.state = 'failed'
  }
}

// The list changed under an open preview (the file was deleted / replaced): the preview of a file no longer listed closes.
watch(
  () => props.files.map((file) => file.key).join('\n'),
  () => {
    if (shown.value && !props.files.some((file) => file.key === shown.value)) closePreview()
  },
)
onBeforeUnmount(closePreview)
</script>

<style scoped>
.tl-lf {
  margin-top: 8px;
  padding-top: 7px;
  border-top: 1px solid var(--fg2-line);
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.tl-lf-head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: var(--fg2-muted);
}

.tl-lf-file {
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius);
  background: var(--fg2-control-surface-flat);
  padding: 5px 7px;
}

.tl-lf-row {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.tl-lf-ico {
  flex: 0 0 auto;
  font-size: 13px;
}

.tl-lf-meta {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.tl-lf-name {
  min-width: 0;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--fg2-text);
  font: inherit;
  font-size: 12px;
  font-weight: 600;
  text-align: left;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
}

.tl-lf-name:hover,
.tl-lf-name:focus-visible {
  color: var(--fg2-blue);
  text-decoration: underline;
}

.tl-lf-sub {
  display: flex;
  gap: 6px;
  white-space: nowrap;
  font-size: 10.5px;
  color: var(--fg2-muted);
}

.tl-lf-download {
  flex: 0 0 auto;
}

.tl-lf-replaced {
  color: var(--fg2-status-warning-fg);
}

.tl-lf-preview {
  margin-top: 5px;
}

.tl-lf-muted {
  margin: 0;
  font-size: 11px;
  color: var(--fg2-muted);
}

.tl-lf-error {
  margin: 0;
  font-size: 11px;
  color: var(--fg2-status-danger-fg);
}

.tl-lf-image {
  display: block;
  max-width: 100%;
  max-height: 260px;
  border-radius: var(--fg2-control-radius-sm);
}

.tl-lf-text {
  margin: 0;
  max-height: 220px;
  overflow: auto;
  padding: 6px 8px;
  border: 1px solid var(--fg2-line);
  border-radius: var(--fg2-control-radius-sm);
  background: var(--fg2-bg);
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  line-height: 1.45;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
