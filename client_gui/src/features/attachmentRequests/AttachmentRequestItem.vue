<template>
  <!-- flowgate.chat.0017 CH0002 — one AI attachment upload request, the same row in the chat timeline and in the card
       attachments tab. A person checks the real content first (preview / download), then approves (keep both by
       default, overwrite only when chosen) or rejects. Available in reading and editing screens alike; the server
       decides who may (perm_document_update). A changed target is never followed silently: the person confirms it.
       Two lines per request: the name with its state, then size · AI · target with the actions; the AI's note under them.
       `disabled` (the list's bulk call or reject confirm) locks the decisions and the tick box, not preview / download. -->
  <div class="ar-item" :data-status="current.status" data-test="ar-item" :data-request="current.id">
    <div class="ar-head">
      <input
        v-if="selectable && pending"
        type="checkbox"
        class="ar-check"
        data-test="ar-select"
        :checked="selected"
        :disabled="disabled"
        :aria-label="t('gui.attachment_request.bulk.select_one', { name: current.filename })"
        @change="onSelect"
      />
      <span class="ar-name" :title="current.originalFilename ?? current.filename" data-test="ar-name">{{ current.filename }}</span>
      <span class="ar-state" data-test="ar-state">{{ t(`gui.attachment_request.status.${statusKey}`) }}</span>
    </div>
    <div class="ar-foot">
      <span class="ar-meta">
        <span class="ar-size">{{ sizeText }}</span>
        <span v-if="current.providerName">{{ t('gui.attachment_request.by_ai', { name: current.providerName }) }}</span>
        <span v-if="showTarget" data-test="ar-target">{{ t('gui.attachment_request.target', { title: targetText }) }}</span>
      </span>
      <div v-if="open || (pending && !choosing && !retarget)" class="ar-actions">
        <Button v-if="open" size="sm" variant="ghost" data-test="ar-preview" :aria-expanded="previewOpen" @click="togglePreview">
          {{ previewOpen ? t('gui.attachment_request.actions.hide_preview') : t('gui.attachment_request.actions.preview') }}
        </Button>
        <Button v-if="open" size="sm" variant="ghost" data-test="ar-download" :disabled="downloading" @click="download">
          {{ t('gui.attachment_request.actions.download') }}
        </Button>
        <template v-if="pending && !choosing && !retarget">
          <Button size="sm" variant="secondary" data-test="ar-reject" :disabled="busy || disabled" @click="reject">
            {{ t('gui.attachment_request.actions.reject') }}
          </Button>
          <Button size="sm" variant="primary" data-test="ar-approve" :disabled="busy || disabled || !expected" @click="startApprove">
            {{ t('gui.attachment_request.actions.approve') }}
          </Button>
        </template>
      </div>
    </div>
    <p v-if="current.note" class="ar-note" data-test="ar-note">{{ current.note }}</p>
    <p v-if="resultText" class="ar-result" data-test="ar-result">{{ resultText }}</p>
    <p v-if="conflict && open" class="ar-conflict" data-test="ar-conflict">
      {{ conflict.caseOnly
        ? t('gui.attachment_request.conflict.case_only', { name: conflict.filename })
        : t('gui.attachment_request.conflict.exists', { name: conflict.filename }) }}
    </p>

    <!-- Same name on the target: keep both (default) or overwrite. -->
    <div v-if="choosing" class="ar-choice" data-test="ar-choice" role="group" :aria-label="t('gui.attachment_request.conflict.choose')">
      <label><input v-model="mode" type="radio" value="keep_both" data-test="ar-keep-both" /> {{ t('gui.attachment_request.actions.keep_both') }}</label>
      <label :class="{ 'ar-disabled': conflict?.caseOnly }">
        <input v-model="mode" type="radio" value="overwrite" :disabled="conflict?.caseOnly" data-test="ar-overwrite" />
        {{ t('gui.attachment_request.actions.overwrite') }}
      </label>
      <p v-if="mode === 'overwrite'" class="ar-hint">{{ t('gui.attachment_request.conflict.overwrite_hint') }}</p>
      <div class="ar-actions">
        <Button size="sm" variant="secondary" :disabled="busy" @click="choosing = false">{{ t('gui.attachment_request.actions.cancel') }}</Button>
        <Button size="sm" variant="primary" data-test="ar-confirm" :disabled="busy || disabled" @click="approve(expected)">
          {{ t('gui.attachment_request.actions.approve') }}
        </Button>
      </div>
    </div>

    <!-- The card's document changed since the request was shown: confirm the new target or stop. -->
    <div v-if="retarget" class="ar-choice" data-test="ar-retarget">
      <p>{{ t('gui.attachment_request.errors.target_changed', { title: retarget.title ?? retarget.docId }) }}</p>
      <div class="ar-actions">
        <Button size="sm" variant="secondary" :disabled="busy" @click="retarget = null">{{ t('gui.attachment_request.actions.cancel') }}</Button>
        <Button size="sm" variant="primary" data-test="ar-retarget-approve" :disabled="busy || disabled" @click="approveRetarget">
          {{ t('gui.attachment_request.actions.confirm_new_target') }}
        </Button>
      </div>
    </div>

    <p v-if="errorText" class="ar-error" role="alert" data-test="ar-error">{{ errorText }}</p>

    <div v-if="previewOpen" class="ar-preview" data-test="ar-preview-body">
      <p v-if="previewState === 'loading'" class="ar-muted">{{ t('gui.attachment_request.preview.loading') }}</p>
      <p v-else-if="previewState === 'failed'" class="ar-error">{{ t('gui.attachment_request.preview.failed') }}</p>
      <template v-else-if="preview">
        <img v-if="preview.kind === 'image' && imageUrl" :src="imageUrl" :alt="current.filename" class="ar-image" data-test="ar-preview-image" />
        <p v-else-if="preview.kind === 'binary'" class="ar-muted" data-test="ar-preview-binary">{{ t('gui.attachment_request.preview.binary') }}</p>
        <template v-else-if="preview.kind === 'text'">
          <!-- Text only, never rendered as HTML. -->
          <pre class="ar-text" data-test="ar-preview-text">{{ preview.content }}</pre>
          <p v-if="preview.truncated" class="ar-muted">{{ t('gui.attachment_request.preview.truncated') }}</p>
        </template>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '@/components/ui/Button.vue'
import {
  attachmentRequestApprove,
  attachmentRequestContent,
  attachmentRequestIsOpen,
  attachmentRequestPreview,
  attachmentRequestReject,
  attachmentRequestsErrorOf,
  type AttachmentRequest,
  type AttachmentRequestConflict,
  type AttachmentRequestConflictMode,
  type AttachmentRequestPreview,
} from '@/features/document/api/attachmentRequests'

const props = defineProps<{
  request: AttachmentRequest
  /** The document the screen shows as the target (the card tab: the Node's live document). Default: the request's. */
  expectedDocId?: string | null
  /** A same-name attachment the screen already knows of on that document (the card tab's list). */
  existing?: AttachmentRequestConflict | null
  /** Show "target: <card>" (the chat shows it; the card tab is the card itself). */
  showTarget?: boolean
  /** A tick box for the list's bulk approve / reject (shown while the request waits). */
  selectable?: boolean
  selected?: boolean
  /** The list's bulk call or reject confirm is on: no decision and no tick from this row meanwhile. */
  disabled?: boolean
}>()

const emit = defineEmits<{ decided: [request: AttachmentRequest]; select: [id: string, selected: boolean] }>()

const { t } = useI18n()

const local = ref<AttachmentRequest | null>(null)
watch(
  () => props.request,
  () => {
    // A newer server read replaces what this row decided locally.
    local.value = null
  },
)
const current = computed<AttachmentRequest>(() => local.value ?? props.request)
const open = computed(() => attachmentRequestIsOpen(current.value))
const pending = computed(() => current.value.status === 'pending')
const expected = computed(() => props.expectedDocId ?? current.value.targetDocId ?? null)
const conflict = computed<AttachmentRequestConflict | null>(() => props.existing ?? current.value.conflict)
const statusKey = computed(() =>
  ['pending', 'approving', 'approved', 'rejected', 'expired', 'failed'].includes(current.value.status) ? current.value.status : 'failed',
)
const targetText = computed(() => current.value.targetTitle ?? current.value.nodeKey ?? '—')
const sizeText = computed(() => {
  const n = current.value.size
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
})
const resultText = computed(() => {
  const r = current.value
  if (r.status === 'approved') {
    return r.decision === 'overwrite'
      ? t('gui.attachment_request.result.overwritten', { name: r.finalFilename ?? r.filename })
      : t('gui.attachment_request.result.approved', { name: r.finalFilename ?? r.filename })
  }
  if (r.status === 'expired') return t('gui.attachment_request.result.expired')
  return ''
})

const busy = ref(false)
const choosing = ref(false)
const mode = ref<AttachmentRequestConflictMode>('keep_both')
const retarget = ref<{ docId: string; title: string | null } | null>(null)
const errorText = ref('')

function fail(error: unknown): void {
  const read = attachmentRequestsErrorOf(error)
  if (read.kind === 'target_changed' && read.currentDocId) {
    retarget.value = { docId: read.currentDocId, title: read.currentTitle }
    errorText.value = ''
    return
  }
  errorText.value = t(`gui.attachment_request.errors.${read.kind}`)
}

function onSelect(event: Event): void {
  emit('select', current.value.id, (event.target as HTMLInputElement).checked)
}

function startApprove(): void {
  errorText.value = ''
  if (conflict.value) {
    mode.value = 'keep_both'
    choosing.value = true
    return
  }
  void approve(expected.value)
}

async function approve(docId: string | null): Promise<void> {
  if (!docId || busy.value || props.disabled) return
  busy.value = true
  errorText.value = ''
  try {
    const overwrite = mode.value === 'overwrite' && conflict.value && !conflict.value.caseOnly
    const updated = await attachmentRequestApprove(current.value.id, {
      expectedDocId: docId,
      conflict: overwrite ? 'overwrite' : 'keep_both',
      expectedExistingSha256: overwrite ? conflict.value?.sha256 ?? null : null,
    })
    local.value = updated
    choosing.value = false
    retarget.value = null
    emit('decided', updated)
  } catch (error) {
    fail(error)
  } finally {
    busy.value = false
  }
}

async function approveRetarget(): Promise<void> {
  const target = retarget.value
  if (!target) return
  // A changed document: the same-name check of the old one no longer applies, keep both is the safe default.
  mode.value = 'keep_both'
  await approve(target.docId)
}

async function reject(): Promise<void> {
  if (busy.value || props.disabled) return
  busy.value = true
  errorText.value = ''
  try {
    const updated = await attachmentRequestReject(current.value.id)
    local.value = updated
    emit('decided', updated)
  } catch (error) {
    fail(error)
  } finally {
    busy.value = false
  }
}

const previewOpen = ref(false)
const previewState = ref<'idle' | 'loading' | 'ready' | 'failed'>('idle')
const preview = ref<AttachmentRequestPreview | null>(null)
const imageUrl = ref<string | null>(null)

function dropImage(): void {
  if (imageUrl.value) URL.revokeObjectURL(imageUrl.value)
  imageUrl.value = null
}

async function togglePreview(): Promise<void> {
  previewOpen.value = !previewOpen.value
  if (!previewOpen.value || previewState.value === 'ready' || previewState.value === 'loading') return
  previewState.value = 'loading'
  try {
    const read = await attachmentRequestPreview(current.value.id)
    if (read.kind === 'image') {
      const blob = await attachmentRequestContent(current.value.id, true)
      dropImage()
      imageUrl.value = URL.createObjectURL(blob)
    }
    preview.value = read
    previewState.value = 'ready'
  } catch {
    previewState.value = 'failed'
  }
}

const downloading = ref(false)

async function download(): Promise<void> {
  if (downloading.value) return
  downloading.value = true
  errorText.value = ''
  try {
    const blob = await attachmentRequestContent(current.value.id)
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = current.value.filename
    anchor.rel = 'noopener'
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    setTimeout(() => URL.revokeObjectURL(url), 0)
  } catch (error) {
    fail(error)
  } finally {
    downloading.value = false
  }
}

onBeforeUnmount(dropImage)
</script>

<style scoped>
/* The v0.2 app's theme tokens (--fg2-*): the dark panel, its muted text, the shared status tones. */
.ar-item {
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius);
  padding: 8px 10px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  background: var(--fg2-control-surface-flat);
  color: var(--fg2-text);
}
.ar-item[data-status='pending'] {
  border-color: var(--fg2-status-warning-border);
}
.ar-head {
  display: flex;
  gap: 8px;
  align-items: center;
  min-width: 0;
}
.ar-check {
  flex: none;
  margin: 0;
}
.ar-name {
  font-size: 13px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
  flex: 1 1 auto;
}
.ar-state {
  flex: none;
  font-size: 11.5px;
  padding: 1px 8px;
  border-radius: var(--fg2-control-radius-pill);
  border: 1px solid var(--fg2-line-strong);
  color: var(--fg2-control-fg);
}
.ar-item[data-status='pending'] .ar-state {
  border-color: var(--fg2-status-warning-border);
  background: var(--fg2-status-warning-bg);
  color: var(--fg2-status-warning-fg);
}
.ar-foot {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex-wrap: wrap;
}
.ar-meta {
  display: flex;
  gap: 4px 10px;
  flex: 1 1 140px;
  min-width: 0;
  flex-wrap: wrap;
  color: var(--fg2-muted);
  font-size: 12px;
}
.ar-meta > span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 100%;
}
.ar-muted {
  color: var(--fg2-muted);
  font-size: 12px;
}
.ar-note,
.ar-result,
.ar-conflict,
.ar-hint {
  margin: 0;
  font-size: 12px;
  color: var(--fg2-control-fg);
}
.ar-conflict {
  color: var(--fg2-status-warning-fg);
}
.ar-error {
  margin: 0;
  font-size: 12px;
  color: var(--fg2-status-danger-fg);
}
.ar-actions {
  display: flex;
  gap: 6px;
  justify-content: flex-end;
  flex-wrap: wrap;
  margin-left: auto;
}
.ar-choice {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12px;
  padding: 8px;
  border-radius: var(--fg2-control-radius-sm);
  border: 1px solid var(--fg2-line);
  background: rgba(255, 255, 255, 0.03);
  color: var(--fg2-control-fg);
}
.ar-choice p {
  margin: 0;
}
.ar-disabled {
  opacity: 0.5;
}
.ar-preview {
  max-height: 320px;
  overflow: auto;
  border-top: 1px dashed var(--fg2-line-strong);
  padding-top: 6px;
}
.ar-text {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  color: var(--fg2-control-fg);
}
.ar-image {
  max-width: 100%;
  max-height: 300px;
}
</style>
