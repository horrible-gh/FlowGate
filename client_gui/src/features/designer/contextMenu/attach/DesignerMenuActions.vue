<template>
  <!-- flowgate.v02.0060 T#2 (0006-D rev2 §4.3 2-B) — the windows of the context menu's existing server commands, in the
       look of the v0.2 dialogs (ModalShell, the File menu dialogs' layout): name input for 이름 변경 / 새 폴더 / 새 파일,
       the Flow 삭제 (dispose, DC) confirmation with its required reason and danger look, the delete confirmation that
       names the target and its scope, the upload with its overwrite check and result, the folder download notice, and
       the 수정 확인 diff against the project Base. A window only names what the menu saw; right before it runs it asks
       the menu to build the item again from now, and a refusal of the server stays in the window (nothing changes).
       v02gui.0029 (0003-CH, 시안 tfi2zm20): every window but 수정 확인 takes the Group archive's look. The confirms (그룹
       아카이브 / 삭제, 플로우 삭제, 파일 삭제, 폴더 삭제) lead with the large badge above the title; the input windows (이름
       변경, 새 폴더, 새 파일, 업로드, 폴더 다운로드) carry a small badge beside the title. Both name their target in one box
       and end in two wide buttons; what is sent, the required reason and a refusal kept in the window are unchanged. -->
  <ModalShell
    :open="!!shown"
    :size="shown?.kind === 'file.git.changes' ? 'lg' : 'md'"
    :role="danger ? 'alertdialog' : 'dialog'"
    :aria-describedby="refusal ? refusalId : undefined"
    :surface-class="look !== 'plain' ? 'dma-hero-surface' : undefined"
    @close="close"
  >
    <template #header="{ titleId }">
      <!-- 0016-CH (시안 basodrr4 B안): a confirm leads with a large icon badge above the title; v02gui.0029: an input
           window has a small badge beside its title; 수정 확인 keeps the plain title row. The close button is the shared
           line icon either way. -->
      <div class="dma-head" :class="{ 'is-hero': look !== 'plain', 'is-compact': look === 'compact', 'is-danger': look !== 'plain' && danger }" :data-look="look">
        <span v-if="badgeIcon" class="dma-badge" aria-hidden="true" data-test="dma-badge" :data-icon="badgeIcon"><LineIcon :name="badgeIcon" /></span>
        <h2 :id="titleId" class="dma-title" :class="{ 'is-danger': danger && look === 'plain' }">{{ title }}</h2>
        <IconButton :label="t('close')" :title="t('close')" variant="ghost" size="sm" class="dma-close" data-test="dma-close" @click="close"><LineIcon name="x" /></IconButton>
      </div>
    </template>

    <template #default>
      <div v-if="shown" class="dma-body" :data-test="`dma-${shown.kind}`" :data-kind="shown.kind">
        <Alert v-if="stale || staleTried" tone="warning" class="dma-alert" data-test="dma-stale">{{ t('stale') }}</Alert>
        <!-- Only the sentence shows; the server's status and code are the sentence's tooltip (Alert's title is a heading). -->
        <Alert v-if="refusal" :id="refusalId" tone="danger" class="dma-alert" data-test="dma-refusal" :data-reason="refusal.reason"
           :data-status="refusal.status ?? undefined" :data-code="refusal.code ?? undefined"
          ><span data-test="dma-refusal-text" :title="refusalTip">{{ refusalText }}</span></Alert>
        <p v-if="refusal?.titleChanged" class="dma-hint" data-test="dma-title-saved">{{ t('rename.server.titleSaved') }}</p>

        <!-- v02gui.0029: the window's one sentence, then its target in a box (name large, its identifier / folder faint). -->
        <p v-if="lead" class="dma-lead" :data-test="lead.test">{{ lead.text }}</p>
        <div v-if="box" class="dma-target" :class="{ 'is-danger': box.danger }" :data-test="`dma-fact-${box.key}`" :data-label="box.label"
          :data-path="box.path ?? undefined">
          <LineIcon :name="box.icon" class="dma-target-icon" :class="`is-${box.tone}`" />
          <span class="dma-target-name" :title="box.path ?? box.name">{{ box.name }}</span>
          <span v-if="box.meta" class="dma-target-meta" :title="box.meta" :data-test="box.metaTest ?? 'dma-target-meta'">{{ box.meta }}</span>
        </div>

        <dl v-if="facts.length && look === 'plain'" class="dma-facts">
          <div v-for="fact in facts" :key="fact.key" class="dma-fact" :data-test="`dma-fact-${fact.key}`">
            <dt>{{ fact.label }}</dt>
            <dd :title="fact.value">{{ fact.value }}</dd>
          </div>
        </dl>

        <!-- 이름 변경 -->
        <template v-if="shown.kind === 'group.rename' || shown.kind === 'flow.rename'">
          <!-- v02gui.0012 (0004-CH seq 19/20): the Group window is the two inputs only — no facts, note or hint, and
               nothing is flagged before the user types; the button stays off until something changed. -->
          <div class="dma-field">
            <label class="dma-label" for="dma-name">{{ shown.kind === 'group.rename' ? t('rename.groupName') : t('rename.name') }}</label>
            <Input id="dma-name" :model-value="text" :invalid="!!nameError" data-modal-autofocus data-test="dma-input"
              @update:model-value="text = $event" @keydown.enter.prevent="submit" />
            <p v-if="nameError" class="dma-error" data-test="dma-input-error">{{ nameError }}</p>
          </div>
          <!-- v02gui.0012: the Group identifier changes on its own; the name may stay as it is. -->
          <div v-if="shown.kind === 'group.rename'" class="dma-field">
            <label class="dma-label" for="dma-ident">{{ t('rename.ident') }}</label>
            <Input id="dma-ident" :model-value="identText" :invalid="!!identError" data-test="dma-ident"
              @update:model-value="identText = $event" @keydown.enter.prevent="submit" />
            <p v-if="identError" class="dma-error" data-test="dma-ident-error">{{ identError }}</p>
          </div>
          <ul v-if="shown.kind === 'flow.rename'" class="dma-points" data-test="dma-rename-note">
            <li class="is-info"><LineIcon name="info" /><span>{{ t('rename.note') }}</span></li>
          </ul>
        </template>

        <!-- 그룹 아카이브 (v02gui.0027): display-only; the Group's Flows, documents, runs and Git stay as they are. -->
        <!-- 0016-CH B안: title → the one key sentence → the Group in its own box → the supporting points → buttons. -->
        <template v-else-if="shown.kind === 'group.archive'">
          <p class="dma-lead" data-test="dma-group-archive-note">{{ t('archive.note') }}</p>
          <div class="dma-target" data-test="dma-fact-target" :data-label="t('group')">
            <LineIcon name="folder" class="dma-target-icon" />
            <span class="dma-target-name" :title="heroName">{{ heroName }}</span>
            <span v-if="heroMeta" class="dma-target-meta" data-test="dma-target-meta">{{ heroMeta }}</span>
          </div>
          <ul class="dma-points" data-test="dma-group-archive-hint">
            <li><LineIcon name="check-circle" /><span>{{ t('archive.points.kept') }}</span></li>
            <li><LineIcon name="eye" /><span>{{ t('archive.points.visible') }}</span></li>
            <li><LineIcon name="restore" /><span>{{ t('archive.points.restore') }}</span></li>
          </ul>
        </template>

        <!-- 그룹 삭제 (v02gui.0027 0008-T): an empty Group only. With Flows (archived and discarded count) nothing is deleted:
             the window says why and offers 대신 아카이브. Only the Group's record goes; no history is erased. -->
        <template v-else-if="shown.kind === 'group.delete'">
          <Alert v-if="deleteBlockedShown" tone="danger" class="dma-alert" data-test="dma-group-delete-blocked"
            :data-count="deleteBlockedCount ?? undefined">{{ deleteBlockedText }}</Alert>
          <p v-else class="dma-lead" data-test="dma-group-delete-note">{{ t('groupDelete.note') }}</p>
          <div class="dma-target" data-test="dma-fact-target" :data-label="t('group')">
            <LineIcon name="folder" class="dma-target-icon" />
            <span class="dma-target-name" :title="heroName">{{ heroName }}</span>
            <span v-if="heroMeta" class="dma-target-meta" data-test="dma-target-meta">{{ heroMeta }}</span>
          </div>
          <ul v-if="!deleteBlockedShown" class="dma-points" data-test="dma-group-delete-hint">
            <li><LineIcon name="check-circle" /><span>{{ t('groupDelete.hint') }}</span></li>
            <li><LineIcon name="alert" /><span>{{ t('groupDelete.points.archiveAlt') }}</span></li>
          </ul>
        </template>

        <!-- Flow 삭제 = the existing dispose (DC). v02gui.0029: the Group delete's look — one sentence, the Flow in its box,
             two points, then the required reason; a reason chip only fills the input (it can still be edited). -->
        <template v-else-if="shown.kind === 'flow.delete'">
          <ul class="dma-points" data-test="dma-dispose-points">
            <li><LineIcon name="check-circle" /><span>{{ t('dispose.points.kept') }}</span></li>
            <li><LineIcon name="eye-off" /><span>{{ t('dispose.points.hidden') }}</span></li>
          </ul>
          <div class="dma-field">
            <label class="dma-label" for="dma-reason">{{ t('dispose.reason') }} <span class="dma-required" aria-hidden="true">*</span></label>
            <div class="dma-chips" data-test="dma-reason-chips">
              <button v-for="chip in REASON_CHIPS" :key="chip" type="button" class="dma-chip" :disabled="busy" :data-chip="chip"
                @click="pickReason(chip)">{{ t(`dispose.chips.${chip}`) }}</button>
            </div>
            <Textarea id="dma-reason" ref="reasonInput" :model-value="text" :rows="3" :invalid="(tried || reasonTouched) && !text.trim()" data-modal-autofocus
              :placeholder="t('dispose.placeholder')" data-test="dma-reason" @update:model-value="text = $event; reasonTouched = true" />
            <p v-if="(tried || reasonTouched) && !text.trim()" class="dma-error" data-test="dma-reason-required">{{ t('dispose.reasonRequired') }}</p>
          </div>
        </template>

        <!-- 새 폴더 / 새 파일 -->
        <div v-else-if="shown.kind === 'folder.newFolder' || shown.kind === 'folder.newFile'" class="dma-field">
          <label class="dma-label" for="dma-name">{{ t('create.name') }}</label>
          <Input id="dma-name" :model-value="text" :invalid="!!nameError" data-modal-autofocus data-test="dma-input"
            @update:model-value="text = $event" @keydown.enter.prevent="submit" />
          <p v-if="nameError" class="dma-error" data-test="dma-input-error">{{ nameError }}</p>
        </div>

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
          <ul class="dma-points">
            <li class="is-info"><LineIcon name="info" /><span>{{ t('download.zip') }}</span></li>
            <li class="is-info" data-test="dma-empty-folders"><LineIcon name="info" /><span>{{ t('download.empty') }}</span></li>
          </ul>
        </template>

        <!-- 삭제 (파일 / 폴더). v02gui.0029: titled 파일 삭제 / 폴더 삭제; the name large in a red-edged box, its folder faint;
             what goes with it and that it cannot be undone as red points. -->
        <template v-else-if="shown.kind === 'folder.delete' || shown.kind === 'file.delete'">
          <ul class="dma-points is-warn" data-test="dma-remove-points">
            <li v-if="shown.kind === 'folder.delete'"><LineIcon name="alert" /><span>{{ t('remove.contents') }}</span></li>
            <li><LineIcon name="alert" /><span>{{ t('remove.irreversible') }}</span></li>
          </ul>
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
      <div v-if="shown" class="dma-actions" :class="{ 'is-wide': look !== 'plain' }">
        <template v-if="shown.kind === 'file.git.changes'">
          <Button v-if="refusal" variant="secondary" data-test="dma-retry" @click="readDiff">{{ t('diff.retry') }}</Button>
          <Button variant="secondary" data-test="dma-cancel" @click="close">{{ t('close') }}</Button>
        </template>
        <Button v-else-if="uploadResult" variant="primary" data-test="dma-cancel" @click="close">{{ t('close') }}</Button>
        <!-- v02gui.0027: a Group with Flows is not deleted; 대신 아카이브 runs the Group archive (none for an archived Group). -->
        <template v-else-if="deleteBlockedShown">
          <Button variant="secondary" data-test="dma-cancel" @click="close">{{ t('close') }}</Button>
          <Button v-if="canArchiveInstead" variant="primary" :disabled="busy || stale" :loading="busy" data-test="dma-archive-instead"
            @click="archiveInstead"><LineIcon name="archive" />{{ t('groupDelete.archiveInstead') }}</Button>
        </template>
        <template v-else>
          <Button variant="secondary" data-test="dma-cancel" @click="close">{{ t('cancel') }}</Button>
          <Button :variant="danger ? 'danger' : 'primary'" :disabled="!canSubmit" :loading="busy" data-test="dma-submit" @click="submit">
            <LineIcon v-if="hero && badgeIcon" :name="badgeIcon" />{{ submitLabel }}
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
import LineIcon, { type LineIconName } from '../../../../components/ui/LineIcon.vue'
import Input from '../../../../components/ui/Input.vue'
import ModalShell from '../../../../components/ui/ModalShell.vue'
import Textarea from '../../../../components/ui/Textarea.vue'
import { menuActionMessages } from './actionMessages'
import {
  baseName,
  diffRows,
  entryNameProblem,
  groupIdentifierProblem,
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
/** The server's limit of a Group display name (module_routes.patch_module). */
const GROUP_TITLE_MAX = 100
/** Rows of the 수정 확인 diff shown at first and added by each 더 보기. */
const DIFF_PAGE_ROWS = 2000

const pending = computed(() => props.actions.pending.value)
/** 파일 다운로드 has no window: it runs as soon as it is asked. */
const shown = computed<MenuActionTarget | null>(() => (pending.value && pending.value.target.kind !== 'file.download' ? pending.value.target : null))

const text = ref('')
/** 그룹 이름 변경: the identifier input (v02gui.0012). */
const identText = ref('')
const tried = ref(false)
// The delete reason was typed in and left empty: the only moment its input error shows.
const reasonTouched = ref(false)
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

const danger = computed(() => ['flow.delete', 'folder.delete', 'file.delete', 'group.delete'].includes(shown.value?.kind ?? ''))
/** 그룹 삭제: the Flow count of a 409 `module_not_empty` (the server's final word), else null. */
const serverFlowCount = ref<number | null>(null)
/** The menu built again from now still has the item (the target is there, in the same scope, still enabled). */
const stale = computed(() => !!pending.value && !uploadResult.value && !pending.value.stillValid())

const groupNow = computed(() => {
  const target = shown.value
  return target?.kind === 'group.rename' || target?.kind === 'group.archive' || target?.kind === 'group.delete'
    ? props.actions.server.group(target.projectId, target.groupId) : null
})
const flowNow = computed(() => {
  const target = shown.value
  return target && (target.kind === 'flow.rename' || target.kind === 'flow.delete') ? props.actions.server.flow(target.projectId, target.flowId) : null
})
const currentName = computed(() => groupNow.value?.name ?? flowNow.value ?? '')
/**
 * 0016-CH B안 / v02gui.0029 (0003-CH): how a window looks — `hero` = a confirm (the large badge above the title, the target
 * box, the point list, wide buttons), `compact` = an input window (a small badge beside the title, the same box and wide
 * buttons), `plain` = 수정 확인 (the title row and its facts).
 */
const look = computed<'hero' | 'compact' | 'plain'>(() => {
  switch (shown.value?.kind) {
    case 'group.archive': case 'group.delete': case 'flow.delete': case 'folder.delete': case 'file.delete': return 'hero'
    case 'group.rename': case 'flow.rename': case 'folder.newFolder': case 'folder.newFile': case 'folder.upload': case 'folder.download': return 'compact'
    default: return 'plain'
  }
})
const hero = computed(() => look.value === 'hero')
/** The badge of the window's header (and of a confirm's submit button), or null for a plain window. */
const badgeIcon = computed<LineIconName | null>(() => {
  switch (shown.value?.kind) {
    case 'group.archive': return 'archive'
    case 'group.delete': case 'flow.delete': case 'folder.delete': case 'file.delete': return 'trash'
    case 'group.rename': case 'flow.rename': return 'pencil'
    case 'folder.newFolder': return 'folder-plus'
    case 'folder.newFile': return 'file-plus'
    case 'folder.upload': return 'upload'
    case 'folder.download': return 'download'
    default: return null
  }
})
/** v02gui.0029: a confirm's one sentence above its target box (the Group windows keep their own). */
const lead = computed(() => {
  switch (shown.value?.kind) {
    case 'flow.delete': return { text: t('dispose.lead'), test: 'dma-dispose-note' }
    case 'folder.delete': return { text: t('remove.folderLead'), test: 'dma-scope' }
    case 'file.delete': return { text: t('remove.file'), test: 'dma-scope' }
    default: return null
  }
})
interface TargetBox {
  readonly key: 'target' | 'location'
  readonly label: string
  readonly icon: LineIconName
  readonly tone: 'flow' | 'folder' | 'file'
  readonly name: string
  readonly meta: string
  readonly metaTest?: string
  readonly path?: string
  readonly danger: boolean
}
/** The folder a path sits in, as the box's faint side note ('' at the root). */
function parentOf(path: string): string {
  const parts = path.split('/').filter(Boolean)
  return parts.length > 1 ? `${parts.slice(0, -1).join('/')}/` : ''
}
/**
 * v02gui.0029: the target box of the windows other than the Group ones (they draw their own) and 수정 확인 (its facts):
 * the Flow with its identifier, the file / folder by its name with its folder, or the folder a new entry / upload goes
 * to. The 그룹 이름 변경 window stays the two inputs only (0004-CH seq 19/20); an upload's result has no box.
 */
const box = computed<TargetBox | null>(() => {
  const target = shown.value
  if (!target || uploadResult.value) return null
  switch (target.kind) {
    case 'flow.rename': case 'flow.delete':
      return { key: 'target', label: t('flow'), icon: 'workflow', tone: 'flow', name: currentName.value || target.flowId, meta: target.flowId, metaTest: 'dma-fact-id', danger: false }
    case 'file.delete':
      return { key: 'target', label: t('target'), icon: 'file', tone: 'file', name: baseName(target.path) || target.path, meta: parentOf(target.path), path: target.path, danger: true }
    case 'folder.delete': case 'folder.download':
      return { key: 'target', label: t('target'), icon: 'folder', tone: 'folder', name: baseName(target.path) || t('root'), meta: parentOf(target.path), path: target.path || undefined, danger: target.kind === 'folder.delete' }
    case 'folder.newFolder': case 'folder.newFile': case 'folder.upload':
      return { key: 'location', label: t('location'), icon: 'folder', tone: 'folder', name: target.path || t('root'), meta: t(target.kind === 'folder.upload' ? 'upload.where' : 'create.where'), danger: false }
    default: return null
  }
})
/** v02gui.0029: the reasons a Flow is often deleted for; a chip fills the reason input, which can still be edited. */
const REASON_CHIPS = ['duplicate', 'test', 'unused', 'mistake'] as const
const reasonInput = ref<{ $el?: Element } | null>(null)
function pickReason(chip: typeof REASON_CHIPS[number]) {
  text.value = t(`dispose.chips.${chip}`)
  reasonTouched.value = true
  const el = reasonInput.value?.$el
  const area = el instanceof HTMLTextAreaElement ? el : el?.querySelector?.('textarea')
  area?.focus()
}
/** The Group the confirm names in its target box (its display name, else its id). */
const heroName = computed(() => {
  const target = shown.value
  return target?.kind === 'group.archive' || target?.kind === 'group.delete' ? currentName.value || target.groupId : ''
})
/** The target box's side note: the Group's Flow count when the tree read gave one (e.g. "플로우 0"). */
const heroMeta = computed(() => {
  const total = serverFlowCount.value ?? groupNow.value?.flowTotal
  return typeof total === 'number' ? `${t('flow')} ${total}` : ''
})
/** The Group's identifier now (the Explorer's moduleName: the module key itself). */
const currentIdent = computed(() => groupNow.value?.moduleName ?? '')

const title = computed(() => {
  switch (shown.value?.kind) {
    case 'group.rename': return t('rename.groupTitle')
    case 'group.archive': return t('archive.title')
    case 'group.delete': return t('groupDelete.title')
    case 'flow.rename': return t('rename.flowTitle')
    case 'flow.delete': return t('dispose.title')
    case 'folder.newFolder': return t('create.folderTitle')
    case 'folder.newFile': return t('create.fileTitle')
    case 'folder.upload': return t('upload.title')
    case 'folder.download': return t('download.title')
    case 'folder.delete': return t('remove.folderTitle')
    case 'file.delete': return t('remove.fileTitle')
    case 'file.git.changes': return t('diff.title')
    default: return ''
  }
})
const submitLabel = computed(() => {
  switch (shown.value?.kind) {
    case 'group.rename': case 'flow.rename': return t('rename.submit')
    case 'flow.delete': return t('dispose.submit')
    case 'group.archive': return t('archive.submit')
    case 'group.delete': return t('groupDelete.submit')
    case 'folder.upload': return t('upload.submit')
    case 'folder.download': return t('download.submit')
    case 'folder.delete': return t('remove.folderSubmit')
    case 'file.delete': return t('remove.submit')
    default: return t('create.submit')
  }
})

/** What the window names: the right-clicked target as the menu saw it (and the base of a diff). */
const facts = computed(() => {
  const target = shown.value
  if (!target) return []
  // The Group window shows its name and identifier in the inputs themselves.
  if (target.kind === 'group.rename') return []
  if (target.kind === 'group.archive' || target.kind === 'group.delete') return [{ key: 'target', label: t('group'), value: currentName.value || target.groupId }]
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
  // An unchanged name is not an error: the window opens with it, and the button just stays off.
  if (kind === 'group.rename' || kind === 'flow.rename') {
    if (!text.value.trim()) return tried.value ? t('rename.required') : null
    return kind === 'group.rename' && text.value.trim().length > GROUP_TITLE_MAX ? t('rename.nameLong') : null
  }
  if (kind === 'folder.newFolder' || kind === 'folder.newFile') {
    const problem = entryNameProblem(text.value)
    if (problem === 'empty') return tried.value ? t('create.empty') : null
    return problem ? t(`create.${problem}`) : null
  }
  return null
})

/** The same rules as the server (module_identifiers.validate_identifier_format); the server still decides. */
const identError = computed(() => {
  if (shown.value?.kind !== 'group.rename') return null
  const value = identText.value.trim()
  if (value === currentIdent.value) return null
  const problem = groupIdentifierProblem(value)
  return problem ? t(`rename.identProblem.${problem}`) : null
})
const groupChanged = computed(() =>
  text.value.trim() !== currentName.value || identText.value.trim() !== currentIdent.value)

/**
 * 그룹 삭제: how many Flows keep the Group — the server's 409 count once it answered, else the Flow count the tree read
 * gave (`flowTotal`, archived and discarded included). null = the Group may be deleted (the DELETE still decides).
 */
const deleteBlockedCount = computed<number | null>(() => {
  if (shown.value?.kind !== 'group.delete') return null
  if (serverFlowCount.value !== null) return serverFlowCount.value
  const total = groupNow.value?.flowTotal
  return typeof total === 'number' && total > 0 ? total : null
})
const deleteBlockedShown = computed(() => deleteBlockedCount.value !== null)
/** An archived Group is told only why (it is archived already); any other is asked to archive first. */
const deleteBlockedText = computed(() => deleteBlockedCount.value === null ? ''
  : t(groupNow.value?.archived ? 'groupDelete.blockedArchived' : 'groupDelete.blocked', { n: deleteBlockedCount.value }))
const canArchiveInstead = computed(() => !!props.actions.server.archiveGroup && !groupNow.value?.archived)

const conflicts = computed(() => (existing.value ? files.value.map((file) => file.name).filter((name) => existing.value!.includes(name)) : []))

const canSubmit = computed(() => {
  const target = shown.value
  if (!target || busy.value || stale.value) return false
  switch (target.kind) {
    case 'group.rename':
      return !!text.value.trim() && !nameError.value && !identError.value && groupChanged.value
    case 'flow.rename':
      return !!text.value.trim() && text.value.trim() !== currentName.value
    case 'flow.delete': return !!text.value.trim()
    case 'group.delete': return !deleteBlockedShown.value
    case 'folder.newFolder': case 'folder.newFile': return entryNameProblem(text.value) === null
    // Always confirmed: a name the tree read does not show (ignored, unreadable, read failed) is overwritten too.
    case 'folder.upload': return files.value.length > 0 && !checking.value && overwrite.value
    default: return true
  }
})

/**
 * The Group rename's own refusals (module_routes.patch_module / module_identifiers) say what is wrong; any other
 * refusal keeps the general sentence of its reason.
 */
const GROUP_REFUSAL_TEXT: Readonly<Record<string, string>> = {
  module_identifier_taken: 'rename.server.taken',
  module_identifier_reserved: 'rename.server.reserved',
  module_rename_busy: 'rename.server.busy',
  module_rename_branch_exists: 'rename.server.branchExists',
  module_rename_path_exists: 'rename.server.pathExists',
  module_rename_git_failed: 'rename.server.gitFailed',
  module_identifier_required: 'rename.identProblem.empty',
  module_identifier_uppercase: 'rename.identProblem.upper',
  module_identifier_invalid: 'rename.identProblem.chars',
  module_identifier_too_long: 'rename.identProblem.long',
  module_title_required: 'rename.required',
  module_title_too_long: 'rename.nameLong',
  module_not_found: 'rename.server.notFound',
}
/** v02gui.0027: the Group archive's own refusals (module_routes archive_module). */
const GROUP_ARCHIVE_REFUSAL_TEXT: Readonly<Record<string, string>> = {
  already_archived: 'archive.server.already',
  module_not_found: 'rename.server.notFound',
}
/** v02gui.0027 (0008-T): the Group delete's refusals (module_routes delete_module) and those of its 대신 아카이브. */
const GROUP_DELETE_REFUSAL_TEXT: Readonly<Record<string, string>> = {
  ...GROUP_ARCHIVE_REFUSAL_TEXT,
}
const refusalText = computed(() => {
  const r = refusal.value
  if (!r) return ''
  const kind = shown.value?.kind
  const own = r.code
    ? (kind === 'group.rename' ? GROUP_REFUSAL_TEXT[r.code]
      : kind === 'group.archive' ? GROUP_ARCHIVE_REFUSAL_TEXT[r.code]
        : kind === 'group.delete' ? GROUP_DELETE_REFUSAL_TEXT[r.code] : undefined)
    : undefined
  if (kind === 'group.rename' && r.code === 'module_identifier_taken' && r.holderArchived) {
    return t('rename.server.archivedTaken', { group: r.subject ?? '' })
  }
  if (kind === 'group.rename' && r.code === 'module_rename_too_large') {
    return t('rename.server.tooLarge', { documents: r.documents ?? 0, limit: r.limit ?? 0 })
  }
  if (kind === 'group.rename' && r.code === 'module_rename_busy' && r.busy?.length) {
    const rows = r.busy.slice(0, 5).map((item) => `${item.group_id} — ${t(`rename.server.busyReasons.${item.reason}`, item.reason)}`)
    const more = r.busy.length > 5 ? t('rename.server.busyMore', { n: r.busy.length - 5 }) : ''
    return t('rename.server.busyDetailed', { items: rows.join(', '), more })
  }
  // `subject` is what the server named: the group holding the identifier, the busy Flows, the existing branches.
  return own ? t(own, { group: r.subject ?? '' }) : t(`refusal.${r.reason}`)
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
  identText.value = ''
  tried.value = false
  reasonTouched.value = false
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
  serverFlowCount.value = null
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
  if (target.kind === 'group.rename') identText.value = currentIdent.value
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
    case 'group.rename': {
      // Only a changed identifier is sent; an unchanged one leaves the server's identifier alone.
      const ident = identText.value.trim()
      answer = await server.renameGroup(target.projectId, target.groupId, name, ident !== currentIdent.value ? ident : undefined)
      break
    }
    case 'group.archive':
      answer = server.archiveGroup
        ? await server.archiveGroup(target.projectId, target.groupId)
        : { ok: false, refusal: { reason: 'failed', status: null, code: null } }
      break
    case 'group.delete':
      answer = server.deleteGroup
        ? await server.deleteGroup(target.projectId, target.groupId)
        : { ok: false, refusal: { reason: 'failed', status: null, code: null } }
      break
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
    const refused = answer.refusal ?? { reason: 'failed', status: null, code: null }
    // v02gui.0027: the Group still has Flows — the window turns into the refusal with 대신 아카이브 (nothing deleted).
    if (target.kind === 'group.delete' && refused.code === 'module_not_empty') {
      serverFlowCount.value = Math.max(1, refused.count ?? groupNow.value?.flowTotal ?? 1)
      return
    }
    // The server's refusal is final: it is shown here and nothing on screen changes.
    refusal.value = refused
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

/** 그룹 삭제 → 대신 아카이브 (v02gui.0027 0008-T): the existing Group archive, right here; a refusal stays in the window. */
async function archiveInstead() {
  const target = shown.value
  const server = props.actions.server
  if (target?.kind !== 'group.delete' || busy.value || !server.archiveGroup || !canArchiveInstead.value) return
  if (!stillValid()) return
  const win = windowSeq
  busy.value = true
  refusal.value = null
  const answer = await server.archiveGroup(target.projectId, target.groupId)
  if (win !== windowSeq) return
  busy.value = false
  if (!answer.ok) {
    refusal.value = answer.refusal
    return
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
  font-size: var(--fg2-fs-title);
  font-weight: 700;
  color: var(--fg2-text);
}
.dma-close {
  color: var(--fg2-text-3);
}
/* ── 0016-CH (시안 basodrr4 B안): the Group archive / delete confirm ──────────────────────────────────────────── */
:global(.ui-modal-surface.ui-modal-surface--md.dma-hero-surface) {
  max-width: 460px;
  background: var(--fg2-pop-surface);
  border-radius: var(--fg2-pop-radius);
  box-shadow: var(--fg2-pop-shadow);
}
.dma-head.is-hero {
  position: relative;
  flex-direction: column;
  align-items: flex-start;
  gap: 14px;
  padding: 24px 24px 0;
}
.dma-head.is-hero .dma-close {
  position: absolute;
  top: 14px;
  right: 14px;
}
.dma-head.is-hero .dma-title {
  font-size: 18px;
}
.dma-badge {
  display: grid;
  place-items: center;
  width: 44px;
  height: 44px;
  border-radius: 12px;
  background: rgba(75, 163, 255, 0.14);
  color: #8fc8ff;
}
.dma-badge :deep(svg) {
  width: 22px;
  height: 22px;
}
.dma-head.is-danger .dma-badge {
  background: rgba(255, 100, 120, 0.14);
  color: #ff8c9c;
}
/* v02gui.0029: an input window — the smaller badge beside its title, the close button kept at the corner. */
.dma-head.is-compact {
  flex-direction: row;
  align-items: center;
  gap: 12px;
  padding-right: 52px;
}
.dma-head.is-compact .dma-title {
  font-size: 16px;
}
.dma-head.is-compact .dma-badge {
  width: 34px;
  height: 34px;
  border-radius: 10px;
}
.dma-head.is-compact .dma-badge :deep(svg) {
  width: 18px;
  height: 18px;
}
.dma-target {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  padding: 12px 14px;
  border: 1px solid var(--fg2-line);
  border-radius: var(--fg2-inset-radius);
  background: var(--fg2-inset-bg);
  font-size: var(--fg2-fs-body);
}
.dma-target.is-danger {
  border-color: rgba(255, 120, 140, 0.28);
  background: rgba(255, 90, 110, 0.06);
}
.dma-target-icon {
  color: #a98bff;
}
.dma-target-icon.is-file {
  color: #8fc8ff;
}
.dma-target-name {
  min-width: 0;
  overflow: hidden;
  color: var(--fg2-text);
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.dma-target-meta {
  flex: none;
  margin-left: auto;
  color: var(--fg2-text-3);
  font-size: var(--fg2-fs-xs);
}
.dma-points {
  display: grid;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.dma-points li {
  display: flex;
  gap: 10px;
  color: var(--fg2-text-2);
  font-size: var(--fg2-fs-sub);
  line-height: 1.5;
}
.dma-points li :deep(svg) {
  width: 15px;
  height: 15px;
  margin-top: 2px;
  color: var(--fg2-cyan);
}
.dma-points li + li :deep(svg) {
  color: var(--fg2-text-3);
}
/* v02gui.0029: an info point is quiet; a delete's points are red (what goes with it, that it cannot be undone). */
.dma-points li.is-info :deep(svg) {
  color: var(--fg2-text-3);
}
.dma-points.is-warn li {
  color: #ffc2cc;
}
.dma-points.is-warn li :deep(svg) {
  color: #ff8c9c;
}
.dma-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.dma-required {
  color: var(--fg2-status-danger-fg);
}
.dma-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.dma-chip {
  padding: 3px 10px;
  border: 1px dashed rgba(154, 181, 210, 0.3);
  border-radius: 999px;
  background: transparent;
  color: var(--fg2-text-2);
  font: inherit;
  font-size: var(--fg2-fs-xs);
  cursor: pointer;
}
.dma-chip:hover:not(:disabled),
.dma-chip:focus-visible {
  border-style: solid;
  border-color: #4ba3ff;
  color: var(--fg2-text);
  outline: none;
}
.dma-chip:disabled {
  opacity: 0.5;
  cursor: not-allowed;
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
/* v02gui.0029: every window in the Group archive's look (all but 수정 확인) spaces its body the same way. */
.dma-body:not([data-kind='file.git.changes']) {
  gap: 14px;
  padding: 10px 0 18px;
}
.dma-body:not([data-kind='file.git.changes']) .dma-lead {
  color: var(--fg2-text-2);
  font-size: 14px;
  line-height: 1.55;
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
/* B안: two equal, taller buttons across the confirm's width. */
.dma-actions.is-wide {
  padding: 4px 24px 24px;
}
.dma-actions.is-wide > :deep(.ui-btn) {
  flex: 1;
  height: 40px;
  border-radius: var(--fg2-inset-radius);
}
.dma-actions :deep(.ui-btn svg) {
  width: 15px;
  height: 15px;
}
</style>
