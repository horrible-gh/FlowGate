<template>
  <!-- flowgate.v02.0060 T#2 (0006-D rev2 §4.3 2-B) — the windows of the context menu's existing server commands, in the
       look of the v0.2 dialogs (ModalShell, the File menu dialogs' layout): name input for 이름 변경 / 새 폴더 / 새 파일,
       the Flow 삭제 (dispose, DC) confirmation with its required reason and danger look, the delete confirmation that
       names the target and its scope, the upload with its overwrite check and result, the folder download notice, and
       the 수정 확인 diff against the project Base. A window only names what the menu saw; right before it runs it asks
       the menu to build the item again from now, and a refusal of the server stays in the window (nothing changes). -->
  <ModalShell
    :open="!!shown"
    :size="shown?.kind === 'file.git.changes' ? 'lg' : 'md'"
    :role="danger ? 'alertdialog' : 'dialog'"
    close-on-backdrop
    :aria-describedby="refusal ? refusalId : undefined"
    @close="close"
  >
    <template #header="{ titleId }">
      <div class="dma-head">
        <h2 :id="titleId" class="dma-title" :class="{ 'is-danger': danger }">{{ title }}</h2>
        <IconButton :label="t('close')" variant="ghost" data-test="dma-close" @click="close">✕</IconButton>
      </div>
    </template>

    <template #default>
      <div v-if="shown" class="dma-body" :data-test="`dma-${shown.kind}`" :data-kind="shown.kind">
        <Alert v-if="stale || staleTried" tone="warning" class="dma-alert" data-test="dma-stale">{{ t('stale') }}</Alert>
        <!-- Only the sentence shows; the server's status and code are the sentence's tooltip (Alert's title is a heading). -->
        <Alert v-if="refusal" :id="refusalId" tone="danger" class="dma-alert" data-test="dma-refusal" :data-reason="refusal.reason"
          :data-status="refusal.status ?? undefined" :data-code="refusal.code ?? undefined"
          ><span data-test="dma-refusal-text" :title="refusalTip">{{ t(`refusal.${refusal.reason}`) }}</span></Alert>

        <dl class="dma-facts">
          <div v-for="fact in facts" :key="fact.key" class="dma-fact" :data-test="`dma-fact-${fact.key}`">
            <dt>{{ fact.label }}</dt>
            <dd :title="fact.value">{{ fact.value }}</dd>
          </div>
        </dl>

        <!-- 이름 변경 -->
        <template v-if="shown.kind === 'group.rename' || shown.kind === 'flow.rename'">
          <p class="dma-note">{{ t('rename.note') }}</p>
          <label class="dma-label" for="dma-name">{{ t('rename.name') }}</label>
          <Input id="dma-name" :model-value="text" :invalid="!!nameError" data-modal-autofocus data-test="dma-input"
            @update:model-value="text = $event" @keydown.enter.prevent="submit" />
          <p v-if="nameError" class="dma-error" data-test="dma-input-error">{{ nameError }}</p>
        </template>

        <!-- Flow 삭제 = the existing dispose (DC) -->
        <template v-else-if="shown.kind === 'flow.delete'">
          <p class="dma-lead">{{ t('dispose.lead') }}</p>
          <ul class="dma-effects" data-test="dma-dispose-effects">
            <li v-for="index in 4" :key="index">{{ t(`dispose.effects.${index - 1}`) }}</li>
          </ul>
          <label class="dma-label" for="dma-reason">{{ t('dispose.reason') }}</label>
          <Textarea id="dma-reason" :model-value="text" :rows="3" :invalid="tried && !text.trim()" data-modal-autofocus
            data-test="dma-reason" @update:model-value="text = $event" />
          <p v-if="!text.trim()" class="dma-hint" data-test="dma-reason-required">{{ t('dispose.reasonRequired') }}</p>
        </template>

        <!-- 새 폴더 / 새 파일 -->
        <template v-else-if="shown.kind === 'folder.newFolder' || shown.kind === 'folder.newFile'">
          <label class="dma-label" for="dma-name">{{ t('create.name') }}</label>
          <Input id="dma-name" :model-value="text" :invalid="!!nameError" data-modal-autofocus data-test="dma-input"
            @update:model-value="text = $event" @keydown.enter.prevent="submit" />
          <p v-if="nameError" class="dma-error" data-test="dma-input-error">{{ nameError }}</p>
        </template>

        <!-- 업로드 -->
        <template v-else-if="shown.kind === 'folder.upload'">
          <template v-if="!uploadResult">
            <label class="dma-pick">
              <span class="dma-label">{{ t('upload.pick') }}</span>
              <input type="file" multiple :disabled="busy" data-modal-autofocus data-test="dma-files" @change="onFiles" />
            </label>
            <p v-if="!files.length" class="dma-hint">{{ t('upload.none') }}</p>
            <ul v-else class="dma-list" data-test="dma-file-list">
              <li v-for="file in files" :key="file.name">{{ file.name }}</li>
            </ul>
            <!-- The server overwrites a same-named file without asking, and the tree read does not show every file
                 (ignored, unreadable): any chosen file may overwrite one, so the confirmation is always asked. -->
            <div v-if="files.length" class="dma-conflicts" data-test="dma-overwrite-check">
              <p v-if="checking" class="dma-hint" data-test="dma-checking">{{ t('upload.checking') }}</p>
              <p v-else-if="checkFailed" class="dma-error" data-test="dma-check-failed">{{ t('upload.checkFailed') }}</p>
              <template v-else-if="conflicts.length">
                <p class="dma-label">{{ t('upload.overwriteTitle') }}</p>
                <ul class="dma-list" data-test="dma-conflicts">
                  <li v-for="name in conflicts" :key="name">{{ name }}</li>
                </ul>
              </template>
              <p v-else class="dma-hint" data-test="dma-no-known">{{ t('upload.noKnown') }}</p>
              <p class="dma-note" data-test="dma-overwrite-note">{{ t('upload.overwriteNote') }}</p>
              <label class="dma-check">
                <input v-model="overwrite" type="checkbox" :disabled="busy" data-test="dma-overwrite" />
                <span>{{ t('upload.overwrite') }}</span>
              </label>
            </div>
          </template>
          <div v-else class="dma-result" data-test="dma-upload-result">
            <p class="dma-label">{{ t('upload.done') }}</p>
            <p data-test="dma-uploaded">{{ t('upload.uploaded', { n: uploadResult.uploaded.length }) }}</p>
            <template v-if="uploadResult.skipped.length">
              <p class="dma-error" data-test="dma-skipped">{{ t('upload.skipped', { n: uploadResult.skipped.length }) }}</p>
              <ul class="dma-list">
                <li v-for="row in uploadResult.skipped" :key="row.path">{{ t('upload.skippedRow', { path: row.path, reason: row.reason }) }}</li>
              </ul>
            </template>
          </div>
        </template>

        <!-- 폴더 다운로드 -->
        <template v-else-if="shown.kind === 'folder.download'">
          <p class="dma-note">{{ t('download.zip') }}</p>
          <p class="dma-note" data-test="dma-empty-folders">{{ t('download.empty') }}</p>
        </template>

        <!-- 삭제 (파일 / 폴더) -->
        <template v-else-if="shown.kind === 'folder.delete' || shown.kind === 'file.delete'">
          <p class="dma-lead" data-test="dma-scope">{{ shown.kind === 'folder.delete' ? t('remove.folder') : t('remove.file') }}</p>
          <p class="dma-error">{{ t('remove.irreversible') }}</p>
        </template>

        <!-- 수정 확인 -->
        <template v-else-if="shown.kind === 'file.git.changes'">
          <p v-if="diffLoading" class="dma-hint" data-test="dma-diff-loading">{{ t('diff.loading') }}</p>
          <template v-else-if="diff">
            <p v-if="diffUnavailable" class="dma-hint" data-test="dma-diff-unavailable">{{ diffUnavailable }}</p>
            <p v-else-if="!rows.length" class="dma-hint" data-test="dma-diff-same">{{ t('diff.same') }}</p>
            <template v-else>
              <p class="dma-hint" data-test="dma-diff-counts">{{ t('diff.counts', counts) }}</p>
              <!-- Only the changed lines and a few around them; each folded run of equal lines says how many. -->
              <div class="dma-code" data-test="dma-diff-lines">
                <template v-for="(row, i) in shownRows" :key="i">
                  <div v-if="row.kind === 'gap'" class="dma-line is-gap" data-kind="gap"
                    ><span class="dma-gap">⋯ {{ t('diff.gap', { n: row.count }) }} ⋯</span></div>
                  <div v-else class="dma-line" :class="`is-${row.kind}`" :data-kind="row.kind"
                    ><span class="dma-no">{{ row.oldNo ?? '' }}</span><span class="dma-no">{{ row.newNo ?? '' }}</span
                    ><span class="dma-sign">{{ row.kind === 'add' ? '+' : row.kind === 'del' ? '-' : ' ' }}</span
                    ><span class="dma-text">{{ row.text }}</span
                    ><span v-if="endMark(row)" class="dma-end" :data-end="row.end">{{ endMark(row) }}</span></div>
                </template>
              </div>
              <div v-if="rows.length > shownRows.length" class="dma-more" data-test="dma-diff-partial">
                <span>{{ t('diff.partial', { shown: shownRows.length, total: rows.length }) }}</span>
                <Button variant="secondary" size="sm" data-test="dma-diff-more" @click="rowLimit += DIFF_PAGE_ROWS">{{ t('diff.more') }}</Button>
              </div>
            </template>
          </template>
        </template>
      </div>
    </template>

    <template #footer>
      <div v-if="shown" class="dma-actions">
        <template v-if="shown.kind === 'file.git.changes'">
          <Button v-if="refusal" variant="secondary" data-test="dma-retry" @click="readDiff">{{ t('diff.retry') }}</Button>
          <Button variant="secondary" data-test="dma-cancel" @click="close">{{ t('close') }}</Button>
        </template>
        <Button v-else-if="uploadResult" variant="primary" data-test="dma-cancel" @click="close">{{ t('close') }}</Button>
        <template v-else>
          <Button variant="secondary" data-test="dma-cancel" @click="close">{{ t('cancel') }}</Button>
          <Button :variant="danger ? 'danger' : 'primary'" :disabled="!canSubmit" :loading="busy" data-test="dma-submit" @click="submit">
            {{ submitLabel }}
          </Button>
        </template>
      </div>
    </template>
  </ModalShell>
</template>

<script setup lang="ts">
import { computed, ref, shallowRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Alert from '../../../../components/ui/Alert.vue'
import Button from '../../../../components/ui/Button.vue'
import IconButton from '../../../../components/ui/IconButton.vue'
import Input from '../../../../components/ui/Input.vue'
import ModalShell from '../../../../components/ui/ModalShell.vue'
import Textarea from '../../../../components/ui/Textarea.vue'
import { menuActionMessages } from './actionMessages'
import {
  baseName,
  diffRows,
  entryNameProblem,
  lineDiff,
  type DesignerMenuServerDeps,
  type DiffLine,
  type MenuActionDiff,
  type MenuActionRefusal,
  type MenuActionTarget,
  type MenuActionUpload,
  type PendingMenuAction,
} from './menuActions'

/** The pending window of the connector and the server paths the Shell lent it. */
export interface DesignerMenuActionsHandle {
  readonly pending: { readonly value: PendingMenuAction | null }
  readonly server: DesignerMenuServerDeps
  close(seq: number): void
}

const props = defineProps<{ actions: DesignerMenuActionsHandle }>()
const { t } = useI18n({ useScope: 'local', messages: menuActionMessages, inheritLocale: true })

const refusalId = 'designer-menu-action-refusal'
/** Rows of the 수정 확인 diff shown at first and added by each 더 보기. */
const DIFF_PAGE_ROWS = 2000

const pending = computed(() => props.actions.pending.value)
/** 파일 다운로드 has no window: it runs as soon as it is asked. */
const shown = computed<MenuActionTarget | null>(() => (pending.value && pending.value.target.kind !== 'file.download' ? pending.value.target : null))

const text = ref('')
const tried = ref(false)
const busy = ref(false)
const staleTried = ref(false)
const refusal = shallowRef<MenuActionRefusal | null>(null)
const files = shallowRef<File[]>([])
const existing = shallowRef<readonly string[] | null>(null)
const checking = ref(false)
const checkFailed = ref(false)
const overwrite = ref(false)
const uploadResult = shallowRef<MenuActionUpload | null>(null)
const diff = shallowRef<MenuActionDiff | null>(null)
const diffLoading = ref(false)
const rowLimit = ref(DIFF_PAGE_ROWS)
/** Numbers each window: an answer that comes back for an earlier window is dropped. */
let windowSeq = 0
/** Numbers the overwrite reads and the diff reads of a window: only the latest answer of each is kept. */
let checkSeq = 0
let diffSeq = 0

const danger = computed(() => ['flow.delete', 'folder.delete', 'file.delete'].includes(shown.value?.kind ?? ''))
/** The menu built again from now still has the item (the target is there, in the same scope, still enabled). */
const stale = computed(() => !!pending.value && !uploadResult.value && !pending.value.stillValid())

const groupNow = computed(() => {
  const target = shown.value
  return target?.kind === 'group.rename' ? props.actions.server.group(target.projectId, target.groupId) : null
})
const flowNow = computed(() => {
  const target = shown.value
  return target && (target.kind === 'flow.rename' || target.kind === 'flow.delete') ? props.actions.server.flow(target.projectId, target.flowId) : null
})
const currentName = computed(() => groupNow.value?.name ?? flowNow.value ?? '')

const title = computed(() => {
  switch (shown.value?.kind) {
    case 'group.rename': return t('rename.groupTitle')
    case 'flow.rename': return t('rename.flowTitle')
    case 'flow.delete': return t('dispose.title')
    case 'folder.newFolder': return t('create.folderTitle')
    case 'folder.newFile': return t('create.fileTitle')
    case 'folder.upload': return t('upload.title')
    case 'folder.download': return t('download.title')
    case 'folder.delete': case 'file.delete': return t('remove.title')
    case 'file.git.changes': return t('diff.title')
    default: return ''
  }
})
const submitLabel = computed(() => {
  switch (shown.value?.kind) {
    case 'group.rename': case 'flow.rename': return t('rename.submit')
    case 'flow.delete': return t('dispose.submit')
    case 'folder.upload': return t('upload.submit')
    case 'folder.download': return t('download.submit')
    case 'folder.delete': case 'file.delete': return t('remove.submit')
    default: return t('create.submit')
  }
})

/** What the window names: the right-clicked target as the menu saw it (and the base of a diff). */
const facts = computed(() => {
  const target = shown.value
  if (!target) return []
  if (target.kind === 'group.rename') {
    return [
      { key: 'target', label: t('target'), value: currentName.value || target.groupId },
      { key: 'id', label: t('id'), value: groupNow.value?.moduleName ?? target.groupId },
    ]
  }
  if (target.kind === 'flow.rename' || target.kind === 'flow.delete') {
    return [
      { key: 'target', label: t('flow'), value: currentName.value || target.flowId },
      { key: 'id', label: t('id'), value: target.flowId },
    ]
  }
  if (target.kind === 'file.git.changes') {
    const rows = [{ key: 'target', label: t('target'), value: target.path }]
    if (diff.value) {
      rows.push({ key: 'base', label: t('diff.base'), value: t('diff.baseValue', { base: diff.value.base }) })
      rows.push({ key: 'status', label: t('diff.statusLabel'), value: t(`diff.status.${diff.value.status}`) })
    }
    return rows
  }
  if (target.kind === 'file.delete' || target.kind === 'file.download') return [{ key: 'target', label: t('target'), value: target.path }]
  if (!('root' in target)) return []
  const where = target.path || t('root')
  return target.kind === 'folder.delete' || target.kind === 'folder.download'
    ? [{ key: 'target', label: t('target'), value: where }]
    : [{ key: 'location', label: t('location'), value: where }]
})

const nameError = computed(() => {
  const kind = shown.value?.kind
  if (kind === 'group.rename' || kind === 'flow.rename') {
    if (!text.value.trim()) return tried.value ? t('rename.required') : null
    return text.value.trim() === currentName.value ? t('rename.same') : null
  }
  if (kind === 'folder.newFolder' || kind === 'folder.newFile') {
    const problem = entryNameProblem(text.value)
    if (problem === 'empty') return tried.value ? t('create.empty') : null
    return problem ? t(`create.${problem}`) : null
  }
  return null
})

const conflicts = computed(() => (existing.value ? files.value.map((file) => file.name).filter((name) => existing.value!.includes(name)) : []))

const canSubmit = computed(() => {
  const target = shown.value
  if (!target || busy.value || stale.value) return false
  switch (target.kind) {
    case 'group.rename': case 'flow.rename':
      return !!text.value.trim() && text.value.trim() !== currentName.value
    case 'flow.delete': return !!text.value.trim()
    case 'folder.newFolder': case 'folder.newFile': return entryNameProblem(text.value) === null
    // Always confirmed: a name the tree read does not show (ignored, unreadable, read failed) is overwritten too.
    case 'folder.upload': return files.value.length > 0 && !checking.value && overwrite.value
    default: return true
  }
})

const refusalTip = computed(() => {
  const r = refusal.value
  return r ? [r.status, r.code].filter((part) => part !== null && part !== '').join(' ') || undefined : undefined
})

const diffUnavailable = computed(() => {
  const d = diff.value
  if (!d) return null
  if (d.old.binary || d.new.binary) return t('diff.binary')
  if (d.old.truncated || d.new.truncated) return t('diff.tooLarge')
  return null
})
const lines = computed(() => (diff.value && !diffUnavailable.value ? lineDiff(diff.value.old.content, diff.value.new.content) : []))
const rows = computed(() => diffRows(lines.value))
const shownRows = computed(() => rows.value.slice(0, rowLimit.value))
const counts = computed(() => ({
  add: lines.value.filter((line) => line.kind === 'add').length,
  del: lines.value.filter((line) => line.kind === 'del').length,
}))
/** CRLF is named only when the changed lines end both ways (else every changed line of a CRLF file would say so). */
const crlfShown = computed(() => {
  const ends = new Set(lines.value.filter((line) => line.kind !== 'context').map((line) => line.end))
  return ends.has('crlf') && (ends.has('lf') || ends.has('none'))
})
function endMark(line: DiffLine): string {
  if (line.kind === 'context') return ''
  if (line.end === 'none') return t('diff.noEol')
  return line.end === 'crlf' && crlfShown.value ? t('diff.crlf') : ''
}

function reset() {
  windowSeq += 1
  text.value = ''
  tried.value = false
  busy.value = false
  staleTried.value = false
  refusal.value = null
  files.value = []
  existing.value = null
  checking.value = false
  checkFailed.value = false
  overwrite.value = false
  uploadResult.value = null
  diff.value = null
  diffLoading.value = false
  rowLimit.value = DIFF_PAGE_ROWS
}

function close() {
  const current = pending.value
  reset()
  if (current) props.actions.close(current.seq)
}

/** Right before a request: the target must still be what the menu saw (§3.3, T 4항). */
function stillValid(): boolean {
  if (pending.value?.stillValid()) return true
  staleTried.value = true
  return false
}

function save(file: { filename: string; blob: Blob }) {
  const url = URL.createObjectURL(file.blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = file.filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}

async function readDiff() {
  const target = shown.value
  if (target?.kind !== 'file.git.changes') return
  if (!stillValid()) return
  const win = windowSeq
  const mine = ++diffSeq
  refusal.value = null
  diffLoading.value = true
  const answer = await props.actions.server.fileDiff(target.projectId, target.flowId, target.path)
  if (win !== windowSeq || mine !== diffSeq) return
  diffLoading.value = false
  rowLimit.value = DIFF_PAGE_ROWS
  if (answer.ok) diff.value = answer.value
  else {
    diff.value = null
    refusal.value = answer.refusal
  }
}

async function downloadFileNow(seq: number, target: { projectId: string; flowId: string; path: string }, valid: boolean) {
  const server = props.actions.server
  props.actions.close(seq)
  if (!valid) return
  const answer = await server.download(target.projectId, target.flowId, 'file', target.path, baseName(target.path) || 'download')
  if (answer.ok) {
    save(answer.value)
    const filename = answer.value.filename
    server.notify?.(() => t('download.done', { name: filename }), 'success')
  } else {
    const reason = answer.refusal.reason
    server.notify?.(() => `${t('download.failed')} — ${t(`refusal.${reason}`)}`, 'danger')
  }
}

watch(() => pending.value?.seq, () => {
  reset()
  const current = pending.value
  if (!current) return
  const target = current.target
  if (target.kind === 'file.download') {
    void downloadFileNow(current.seq, target, current.stillValid())
    return
  }
  if (target.kind === 'group.rename' || target.kind === 'flow.rename') text.value = currentName.value
  if (target.kind === 'file.git.changes') void readDiff()
}, { immediate: true })

async function onFiles(event: Event) {
  const target = shown.value
  // The choice is locked while an upload runs (the input is disabled too): its result belongs to what was sent.
  if (target?.kind !== 'folder.upload' || busy.value) return
  const picked = Array.from((event.target as HTMLInputElement).files ?? [])
  files.value = picked
  existing.value = null
  overwrite.value = false
  checkFailed.value = false
  refusal.value = null
  const win = windowSeq
  const mine = ++checkSeq
  checking.value = false
  if (!picked.length) return
  checking.value = true
  const answer = await props.actions.server.fileNames(target.projectId, target.flowId, target.path)
  if (win !== windowSeq || mine !== checkSeq) return
  checking.value = false
  if (answer.ok) existing.value = answer.value
  else checkFailed.value = true
}

async function submit() {
  const target = shown.value
  tried.value = true
  if (!target || !canSubmit.value) return
  if (!stillValid()) return
  const server = props.actions.server
  const win = windowSeq
  busy.value = true
  refusal.value = null
  const name = text.value.trim()
  let answer: { ok: boolean; refusal?: MenuActionRefusal; value?: unknown }
  switch (target.kind) {
    case 'group.rename': answer = await server.renameGroup(target.projectId, target.groupId, name); break
    case 'flow.rename': answer = await server.renameFlow(target.projectId, target.groupId, target.flowId, name); break
    case 'flow.delete': answer = await server.disposeFlow(target.projectId, target.groupId, target.flowId, name); break
    case 'folder.newFolder': case 'folder.newFile':
      answer = await server.createEntry(target.projectId, target.flowId, target.kind === 'folder.newFolder' ? 'folder' : 'file', target.path, name)
      break
    case 'folder.upload': answer = await server.upload(target.projectId, target.flowId, target.path, files.value); break
    case 'folder.download':
      answer = await server.download(target.projectId, target.flowId, 'folder', target.path || '.', `${baseName(target.path) || 'root'}.zip`)
      break
    case 'folder.delete': case 'file.delete':
      answer = await server.remove(target.projectId, target.flowId, target.kind === 'folder.delete' ? 'folder' : 'file', target.path)
      break
    default:
      busy.value = false
      return
  }
  // Closed (or another window) meanwhile: reset() has already cleared busy; this answer is not shown.
  if (win !== windowSeq) return
  busy.value = false
  if (!answer.ok) {
    // The server's refusal is final: it is shown here and nothing on screen changes.
    refusal.value = answer.refusal ?? { reason: 'failed', status: null, code: null }
    return
  }
  if (target.kind === 'folder.upload') {
    uploadResult.value = answer.value as MenuActionUpload
    return
  }
  if (target.kind === 'folder.download') {
    const file = answer.value as { filename: string; blob: Blob }
    save(file)
    server.notify?.(() => t('download.done', { name: file.filename }), 'success')
  }
  close()
}
</script>

<style scoped>
.dma-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 20px 24px 0;
}

.dma-title {
  margin: 0;
  font-size: 16px;
  font-weight: 700;
  color: var(--fg2-text);
}

.dma-title.is-danger {
  color: var(--fg2-status-danger-fg);
}

.dma-body {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 16px 0 4px;
}

.dma-alert {
  margin: 0;
}

.dma-facts {
  display: grid;
  gap: 4px;
  margin: 0;
}

.dma-fact {
  display: flex;
  gap: 10px;
  min-width: 0;
  font-size: 12px;
}

.dma-fact dt {
  flex: 0 0 72px;
  color: var(--fg2-muted);
  font-weight: 650;
}

.dma-fact dd {
  margin: 0;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--fg2-text);
}

.dma-label {
  font-size: 12px;
  font-weight: 650;
  color: var(--fg2-muted);
}

.dma-lead,
.dma-note,
.dma-hint {
  margin: 0;
  font-size: 12px;
  color: var(--fg2-muted);
}

.dma-lead {
  color: var(--fg2-text);
}

.dma-error {
  margin: 0;
  font-size: 11px;
  color: var(--fg2-status-danger-fg);
}

.dma-effects,
.dma-list {
  margin: 0;
  padding-left: 18px;
  font-size: 12px;
  color: var(--fg2-text);
}

.dma-list {
  max-height: 140px;
  overflow: auto;
}

.dma-pick,
.dma-check {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--fg2-text);
}

.dma-conflicts,
.dma-result {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12px;
}

.dma-result p {
  margin: 0;
}

.dma-code {
  max-height: 52vh;
  overflow: auto;
  border: 1px solid var(--fg2-line);
  border-radius: var(--fg2-control-radius);
  background: var(--fg2-control-surface-flat);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
}

.dma-line {
  display: flex;
  width: max-content;
  min-width: 100%;
  white-space: pre;
}

.dma-line.is-add {
  background: var(--fg2-status-success-bg);
}

.dma-line.is-del {
  background: var(--fg2-status-danger-bg);
}

.dma-no {
  flex: 0 0 44px;
  padding-right: 6px;
  text-align: right;
  color: var(--fg2-muted-2);
}

.dma-sign {
  flex: 0 0 14px;
  color: var(--fg2-muted);
}

.dma-end {
  margin-left: 8px;
  padding: 0 4px;
  border-radius: var(--fg2-control-radius-sm);
  background: var(--fg2-control-surface-flat);
  color: var(--fg2-muted);
  font-size: 11px;
}

.dma-line.is-gap {
  color: var(--fg2-muted);
  background: var(--fg2-control-surface-flat);
}

.dma-gap {
  padding: 0 0 0 64px;
  font-style: italic;
}

.dma-more {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  font-size: 12px;
  color: var(--fg2-muted);
}

.dma-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 16px 24px 20px;
}
</style>
