<template>
  <li
    role="treeitem"
    :aria-expanded="node.type === 'folder' ? expanded : undefined"
    :aria-selected="isSelected"
    :tabindex="0"
    class="tree-node"
    @click.stop="handleClick"
    @dblclick.stop="handleDblClick"
    @keydown.enter.space.prevent="handleDblClick"
    @keydown.right.prevent="expanded = true"
    @keydown.left.prevent="expanded = false"
    @contextmenu.prevent.stop="onContextMenu($event)"
    @dragover.prevent="onNodeDragOver"
    @dragleave="onNodeDragLeave"
    @drop.prevent="onNodeDrop"
  >
    <div class="tree-row" :class="{ active: isSelected, 'drag-over': nodeDragOver }">
      <span class="tree-caret" :class="{ open: expanded, leaf: node.type !== 'folder' }">
        <span v-if="node.type === 'folder'">▶</span>
      </span>
      <span class="tree-ico"><AppIcon :name="iconFA.name" :style="{ color: iconFA.color }" /></span>
      <span
        v-if="isNew"
        class="tree-new-marker"
        :title="t('main.file_tree_node.new_badge')"
        :aria-label="t('main.file_tree_node.new_badge')"
      >U</span>
      <span
        v-else-if="isDirty"
        class="tree-dirty-marker"
        :title="t('main.file_tree_node.modified_badge')"
        :aria-label="t('main.file_tree_node.modified_badge')"
      >></span>
      <span class="tree-lbl" :class="{ 'tree-lbl--dirty': isDirty && !isNew, 'tree-lbl--new': isNew }">{{ node.label }}</span>
      <span v-if="downloading" class="tree-loading"><AppIcon name="spinner" spin /></span>
    </div>
    <ul v-if="node.type === 'folder' && expanded" class="tree-children">
      <FileTreeNode
        v-for="child in children"
        :key="child.id"
        :node="child"
        :all-nodes="allNodes"
        :project-id="projectId"
        :readonly="readonly"
        :group-id="groupId"
        @open="$emit('open', $event)"
        @open-diff="$emit('open-diff', $event)"
        @tree-changed="$emit('tree-changed')"
      />
    </ul>
    <ContextMenu v-model:visible="showCtx" :x="ctxX" :y="ctxY">
      <ContextMenuItem v-if="node.type === 'file'" icon="arrow-up-right" @click="openFile">
        {{ t('main.file_tree_node.open') }}
      </ContextMenuItem>
      <!-- 0326 R0001 / N0004 §1 — the ONLY new tree affordance: a menu entry directly
           under "열기", on changed files only. Click/double-click/Enter and the tree's
           badges stay exactly as they were (N0004 unapproved TR0003 §3-1·§3-2-a). -->
      <ContextMenuItem
        v-if="node.type === 'file' && (isDirty || isNew)"
        icon="git-diff"
        @click="openDiff"
      >
        {{ t('main.file_tree_node.view_changes') }}
      </ContextMenuItem>
      <template v-if="node.type === 'folder'">
        <ContextMenuItem icon="caret-right" @click="toggleExpand">
          {{ t('main.file_tree_node.open') }}
        </ContextMenuItem>
        <template v-if="!readonly">
          <ContextMenuItem icon="folder-simple-plus" @click="openCreateFolder">
            {{ t('main.file_tree_node.new_folder') }}
          </ContextMenuItem>
          <ContextMenuItem icon="file-plus" @click="openCreateFile">
            {{ t('main.file_tree_node.new_file') }}
          </ContextMenuItem>
        </template>
        <ContextMenuItem icon="arrow-clockwise" @click="doRefresh">
          {{ t('main.file_tree_node.refresh') }}
        </ContextMenuItem>
        <template v-if="!readonly">
          <ContextMenuItem icon="upload-simple" @click="openUploadFiles">
            {{ t('main.file_tree_node.upload_files') }}
          </ContextMenuItem>
          <ContextMenuItem icon="upload-simple" @click="openUploadFolder">
            {{ t('main.file_tree_node.upload_folder') }}
          </ContextMenuItem>
        </template>
      </template>
      <ContextMenuItem icon="link" @click="copyLink">
        {{ t('main.file_tree_node.copy_link') }}
      </ContextMenuItem>
      <!-- 0327 T0004 (NR0003 권고 3): downloading is a read and is offered in every
           view; a group context downloads that group's worktree copy, not the base one. -->
      <ContextMenuItem icon="download-simple" @click="downloadNode">
        {{ t('main.file_tree_node.download') }}
      </ContextMenuItem>
      <ContextMenuItem v-if="canDelete" icon="trash" :danger="true" @click="deleteNode">
        {{ t('common.delete') }}
      </ContextMenuItem>
    </ContextMenu>

    <ConfirmModal
      v-model:visible="showDeleteConfirm"
      :title="deleteConfirmTitle"
      :message="deleteConfirmMessage"
      :confirm-label="t('common.delete')"
      :danger="true"
      @confirm="confirmDelete"
    />

    <CreateFileFolderModal
      v-model:visible="showModal"
      :type="modalType"
      :project-id="projectId"
      :parent-path="node.path"
      :group-id="groupId"
      @saved="onCreated"
    />
    <template v-if="node.type === 'folder'">
      <input ref="nodeFileInputRef" type="file" multiple style="display:none" @change="onNodeFileSelected" />
      <input ref="nodeFolderInputRef" type="file" multiple style="display:none" @change="onNodeFolderSelected" />
    </template>
  </li>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useExplorerStore, type FileNode } from '../stores/explorer'
import { useTabsStore } from '../stores/tabs'
import { useToast } from './common/useToast'
import ConfirmModal from './ConfirmModal.vue'
import { useFileUpload } from '../composables/useFileUpload'
import { copyToClipboard } from '../utils/clipboard'
import { openClipboardFallback } from '../composables/useClipboardFallback'
import api, { downloadBlobRequest } from '@shared/api'
import ContextMenu from './common/ContextMenu.vue'
import ContextMenuItem from './common/ContextMenuItem.vue'
import CreateFileFolderModal from './CreateFileFolderModal.vue'
import AppIcon from '@shared/AppIcon.vue'

const props = defineProps<{
  node: FileNode
  allNodes: FileNode[]
  projectId: string
  // 0186 P0005 — a structurally read-only tree: suppress create / upload.
  // 0327 T0004 (B0001 / NR0003 권고 1·5) — this no longer means "a group is selected".
  // A group whose worktree is live accepts create/upload straight into that worktree,
  // so only groups WITHOUT one (finalized, disposed, never provisioned) are read-only.
  readonly?: boolean
  groupId?: string | null
}>()

const emit = defineEmits<{
  open: [node: FileNode]
  // 0326 R0001 — diff view, deliberately a SEPARATE event from `open`: the editor
  // entry points (double-click / Enter / 우클릭 "열기") keep emitting `open`.
  'open-diff': [node: FileNode]
  'tree-changed': []
}>()

const { t } = useI18n()
const explorerStore = useExplorerStore()
const tabsStore = useTabsStore()
const { showToast } = useToast()
const { collectDropFiles, uploadFiles } = useFileUpload()
// 0245 R0001 / NR0003 §1 — expansion is owned by the store so that a folder opened
// by "expand all" cascades into children that mount only at that moment. Still
// session-scoped: the file tree has never persisted its open folders.
const expanded = computed({
  get: () => explorerStore.isFileNodeExpanded(props.projectId, props.node.id),
  set: (val: boolean) => explorerStore.setFileNodeExpanded(props.projectId, props.node.id, val),
})
const showCtx = ref(false)
const ctxX = ref(0)
const ctxY = ref(0)
const showModal = ref(false)
const modalType = ref<'folder' | 'file'>('folder')
const downloading = ref(false)
const deleting = ref(false)
const showDeleteConfirm = ref(false)
const nodeDragOver = ref(false)
const nodeFileInputRef = ref<HTMLInputElement | null>(null)
const nodeFolderInputRef = ref<HTMLInputElement | null>(null)

watch(nodeFolderInputRef, (el) => {
  if (el) el.setAttribute('webkitdirectory', '')
}, { immediate: true })

const children = computed(() =>
  props.allNodes.filter((n) => n.parent_id === props.node.id && n.permissions.includes('read')),
)

const isSelected = computed(() => explorerStore.selectedFileNodeId === props.node.id)

// 0327 T0004: delete follows the same rule as create/upload — available wherever the
// tree is writable, which means the base checkout and any group with a live worktree.
// (NR0003 권고 4 kept it blocked on the premise that delete always hit the BASE
// checkout and so could trip the finalize E3 guard; resolved against the group's own
// worktree it never touches base, so that premise no longer holds.) Read-only groups —
// no worktree — keep hiding it.
const canDelete = computed(() => !props.readonly)

// Reuse the established base-dirty marker for either the base checkout or the
// selected group branch's tracked changes. 0192 T0005 §1: the marker now
// propagates to ANCESTOR FOLDERS — a folder is marked when it contains
// a changed file (prefix match over the store's full path list). Without this an
// edit inside a collapsed folder (whose children are not rendered) left no visible
// trace anywhere in the tree.
// 0333 T0004 (B0001 / NR0003 §3.2) — the gate is `groupId` ALONE, never `readonly`.
// 0327 T0004 redefined `readonly` from "a group is selected" to "this group has no
// live worktree", so `readonly && groupId` silently stopped matching the normal case
// — a writable group branch — and every badge there fell back to the BASE checkout's
// change list. The rule is about which data source describes this tree: a group tree
// is described by that group's own change channels whether or not it is still
// writable; only the base checkout (groupId == null) reads the base channels.
const isDirty = computed(() => {
  if (props.groupId) {
    return props.node.type === 'folder'
      ? explorerStore.isGroupChangedDir(props.projectId, props.groupId, props.node.path)
      : explorerStore.isGroupChangedPath(props.projectId, props.groupId, props.node.path)
  }
  return props.node.type === 'folder'
    ? explorerStore.isBaseDirtyDir(props.projectId, props.node.path)
    : explorerStore.isBaseDirtyPath(props.projectId, props.node.path)
})

// 0308 T0004 (NR0003 권고 2·3·5) — new (untracked) file marker, a channel parallel to
// isDirty. base_dirty deliberately excludes untracked files (folding them in would widen
// the E3 merge-finalize guard — git_service.py), so new files need their own store state.
// 0315 TR (NR0003 권고 4) — the read-only group-branch view now surfaces it too: the
// checkout-free tree read used to show committed files only, so a worker's uncommitted
// new files were invisible until finalize (B0001). The group tree read now returns a
// worktree_untracked channel that drives this badge, mirroring the base-checkout one.
// Priority (see template): a node reads as NEW before MODIFIED. For a file the two are
// mutually exclusive (untracked vs tracked-modified); for a folder that holds both, the
// new badge wins so the newly added file is never hidden — the whole point of B0001.
// 0333 T0004 (B0001): same `groupId`-only gate as isDirty above — see that comment.
const isNew = computed(() => {
  if (props.groupId) {
    return props.node.type === 'folder'
      ? explorerStore.isGroupUntrackedDir(props.projectId, props.groupId, props.node.path)
      : explorerStore.isGroupUntrackedPath(props.projectId, props.groupId, props.node.path)
  }
  return props.node.type === 'folder'
    ? explorerStore.isBaseUntrackedDir(props.projectId, props.node.path)
    : explorerStore.isBaseUntrackedPath(props.projectId, props.node.path)
})

const iconFA = computed(() => {
  if (props.node.type === 'folder') {
    return expanded.value
      ? { name: 'folder-open', color: '#f59e0b' }
      : { name: 'folder', color: '#f59e0b' }
  }
  if (props.node.name.endsWith('.md')) return { name: 'file-text', color: '#60a5fa' }
  return { name: 'file', color: '#94a3b8' }
})

function handleClick() {
  if (props.node.type === 'folder') {
    expanded.value = !expanded.value
  } else {
    explorerStore.selectedFileNodeId = props.node.id
  }
}

function handleDblClick() {
  if (props.node.type === 'file') {
    emit('open', props.node)
  }
}

function openFile() {
  handleDblClick()
  showCtx.value = false
}

function openDiff() {
  showCtx.value = false
  if (props.node.type === 'file') emit('open-diff', props.node)
}

function toggleExpand() {
  expanded.value = !expanded.value
  showCtx.value = false
}

function onContextMenu(e: MouseEvent) {
  ctxX.value = e.clientX
  ctxY.value = e.clientY
  showCtx.value = true
}

async function copyLink() {
  showCtx.value = false
  // B0001 / group 0221: the old `navigator.clipboard?.writeText(...)` was a silent no-op on
  // this HTTP LAN deploy (no navigator.clipboard on insecure origins). Use the shared honest
  // write and surface a failure via the manual-copy fallback modal.
  const ok = await copyToClipboard(props.node.path)
  if (!ok) openClipboardFallback(props.node.path)
}

function openCreateFolder() {
  showCtx.value = false
  modalType.value = 'folder'
  showModal.value = true
}

function openCreateFile() {
  showCtx.value = false
  modalType.value = 'file'
  showModal.value = true
}

function doRefresh() {
  showCtx.value = false
  emit('tree-changed')
}

function extractFilenameFromDisposition(disposition: string | undefined, fallback: string): string {
  if (!disposition) return fallback
  const rfc5987Match = disposition.match(/filename\*=UTF-8''([^;]+)/i)
  if (rfc5987Match) return decodeURIComponent(rfc5987Match[1])
  const plainMatch = disposition.match(/filename="([^"]+)"/i)
  if (plainMatch) return plainMatch[1]
  return fallback
}

async function downloadNode() {
  const projectId = props.projectId
  if (!projectId) return
  showCtx.value = false
  downloading.value = true
  try {
    const isFile = props.node.type === 'file'
    const endpoint = isFile ? 'download' : 'download-zip'
    const fallbackName = isFile ? props.node.name : props.node.name + '.zip'
    const url = `/api/v1/projects/${encodeURIComponent(projectId)}/files/${endpoint}`
    // 0327 T0004 (NR0003 권고 3): in a group view the bytes must come from that
    // group's worktree — a base-checkout read would hand back a different file.
    const res = await downloadBlobRequest(url, {
      path: props.node.path,
      ...(props.groupId ? { group_id: props.groupId } : {}),
    })
    const disposition = res.headers['content-disposition'] as string | undefined
    const filename = extractFilenameFromDisposition(disposition, fallbackName)
    const objUrl = URL.createObjectURL(res.data)
    const a = document.createElement('a')
    a.href = objUrl
    a.download = filename
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(objUrl)
  } catch (e: any) {
    const status = e?.response?.status
    if (status === 404) showToast(t('main.file_tree_node.download_not_found'), 'danger')
    else if (status === 403) showToast(t('main.file_tree_node.download_forbidden'), 'danger')
    else if (status === 409) showToast(t('main.file_tree_node.group_worktree_gone'), 'danger')
    else showToast(t('main.file_tree_node.download_failed'), 'danger')
  } finally {
    downloading.value = false
  }
}

const deleteConfirmTitle = computed(() => t('main.file_tree_node.delete_confirm_title'))
// NR0003 권장 6: the confirm text names the target and, for a folder, spells out that the
// deletion is recursive and irreversible. The danger styling comes from ConfirmModal.
const deleteConfirmMessage = computed(() =>
  props.node.type === 'folder'
    ? t('main.file_tree_node.delete_confirm_folder', { path: props.node.path })
    : t('main.file_tree_node.delete_confirm_file', { path: props.node.path }),
)

// NR0003 권장 6·9: replace the native window.confirm with the shared danger-styled
// ConfirmModal. deleteNode only opens the modal; the request runs from confirmDelete.
function deleteNode() {
  if (!props.projectId || deleting.value) return
  showCtx.value = false
  showDeleteConfirm.value = true
}

// NR0003 권장 9: map the server's distinct error codes to translated, user-facing toasts.
function deleteErrorMessage(e: any): string {
  const code = e?.response?.data?.error?.code
  const status = e?.response?.status
  switch (code) {
    case 'INVALID_PATH': return t('main.file_tree_node.delete_invalid_path')
    case 'NOT_FOUND': return t('main.file_tree_node.delete_not_found')
    case 'TYPE_MISMATCH': return t('main.file_tree_node.delete_type_mismatch')
    case 'FORBIDDEN': return t('main.file_tree_node.delete_forbidden')
    case 'DELETE_FAILED': return t('main.file_tree_node.delete_failed')
    // 0327 T0004: the group's worktree went away between render and delete — a distinct
    // situation from "no permission", so it gets its own message.
    case 'WORKTREE_UNAVAILABLE': return t('main.file_tree_node.group_worktree_gone')
    case 'GROUP_NOT_FOUND': return t('main.file_tree_node.delete_not_found')
  }
  if (status === 403) return t('main.file_tree_node.delete_forbidden')
  if (status === 404) return t('main.file_tree_node.delete_not_found')
  return t('main.file_tree_node.delete_failed')
}

// NR0003 권장 7: after a successful delete, close open editor tabs and clear selection for
// the deleted node AND — when a folder is deleted — for everything under it, so a deleted
// file's tab, the selected node, and the pending-select path can never point at a gone path.
function cleanupDeletedRefs() {
  const deletedPath = props.node.path.replace(/\\/g, '/')
  const prefix = deletedPath + '/'
  const isUnder = (p: string | null | undefined) => {
    const np = (p ?? '').replace(/\\/g, '/')
    if (!np) return false
    return np === deletedPath || (props.node.type === 'folder' && np.startsWith(prefix))
  }
  // Snapshot ids first — closeTab() splices the array as we iterate.
  const staleTabIds = tabsStore.tabs
    .filter((tab) => tab.projectId === props.projectId && isUnder(tab.path))
    .map((tab) => tab.id)
  staleTabIds.forEach((id) => tabsStore.closeTab(id))

  const selId = explorerStore.selectedFileNodeId
  if (selId === props.node.id) {
    explorerStore.selectedFileNodeId = null
  } else if (props.node.type === 'folder' && selId) {
    const selNode = props.allNodes.find((n) => n.id === selId)
    if (selNode && isUnder(selNode.path)) explorerStore.selectedFileNodeId = null
  }
  if (isUnder(explorerStore.pendingSelectFilePath)) explorerStore.pendingSelectFilePath = null
}

async function confirmDelete() {
  if (!props.projectId || deleting.value) return
  deleting.value = true
  try {
    const res = await api.delete(
      `/api/v1/projects/${encodeURIComponent(props.projectId)}/files`,
      // 0327 T0004: group_id makes the server resolve the delete against THAT group's
      // worktree (fail-closed — no base fallback); omitted in the base-checkout view.
      { data: { path: props.node.path, type: props.node.type, group_id: props.groupId ?? undefined } },
    )
    cleanupDeletedRefs()
    // NR0003 권장 8: the delete dirtied the base checkout — refresh the base-dirty markers and
    // the Git finalize warning from the returned status, mirroring the src-content save flow.
    const baseGit = res?.data?.base_git
    if (baseGit) {
      explorerStore.setBaseDirtyFiles(props.projectId, Array.isArray(baseGit.files) ? baseGit.files : [])
    }
    explorerStore.invalidateProject(props.projectId)
    emit('tree-changed')
  } catch (e: any) {
    // NR0003 필수 테스트: on failure keep the tree intact (no invalidate/emit) and toast.
    showToast(deleteErrorMessage(e), 'danger')
  } finally {
    deleting.value = false
  }
}

function onCreated(payload: { name: string; type: 'file' | 'folder' }) {
  expanded.value = true
  // pendingSelectFilePath is consumed only by the base-checkout reload; setting it
  // from a group-branch create would leave a stale path to be applied the next time
  // the user switches back to base (0327 T0004).
  if (payload.type === 'file' && !props.groupId) {
    const parentPath = props.node.path.replace(/\\/g, '/')
    explorerStore.pendingSelectFilePath = parentPath + '/' + payload.name
  }
  emit('tree-changed')
}

// ── Drag and drop (folder nodes only as drop target) ─────────────────────────
function onNodeDragOver(e: DragEvent) {
  if (props.node.type !== 'folder') return
  e.stopPropagation()
  nodeDragOver.value = true
}

function onNodeDragLeave(e: DragEvent) {
  if (props.node.type !== 'folder') return
  const related = e.relatedTarget as Node | null
  if (!related || !(e.currentTarget as HTMLElement).contains(related)) {
    nodeDragOver.value = false
  }
}

async function onNodeDrop(e: DragEvent) {
  if (props.node.type !== 'folder' || props.readonly) return
  e.stopPropagation()
  nodeDragOver.value = false
  if (!e.dataTransfer?.items) return
  const files = await collectDropFiles(e.dataTransfer.items)
  await uploadFiles(props.projectId, props.node.path, files, () => {
    expanded.value = true
    emit('tree-changed')
  }, props.groupId)
}

// ── Upload button ────────────────────────────────────────────────────────────
function openUploadFiles() {
  showCtx.value = false
  nodeFileInputRef.value?.click()
}

function openUploadFolder() {
  showCtx.value = false
  nodeFolderInputRef.value?.click()
}

async function onNodeFileSelected(e: Event) {
  const input = e.target as HTMLInputElement
  const fileList = input.files
  if (!fileList || fileList.length === 0) return
  const files = Array.from(fileList).map((f) => {
    ;(f as any)._relativePath = f.name
    return f as File & { _relativePath?: string }
  })
  await uploadFiles(props.projectId, props.node.path, files, () => {
    expanded.value = true
    emit('tree-changed')
  }, props.groupId)
  input.value = ''
}

async function onNodeFolderSelected(e: Event) {
  const input = e.target as HTMLInputElement
  const fileList = input.files
  if (!fileList || fileList.length === 0) return
  const files = Array.from(fileList).map((f) => {
    ;(f as any)._relativePath = f.webkitRelativePath || f.name
    return f as File & { _relativePath?: string }
  })
  await uploadFiles(props.projectId, props.node.path, files, () => {
    expanded.value = true
    emit('tree-changed')
  }, props.groupId)
  input.value = ''
}
</script>

<style scoped>
.tree-lbl--dirty {
  color: var(--git-modified, #e2c08d);
}
.tree-dirty-marker {
  flex: none;
  font-size: 0.72rem;
  font-weight: 700;
  line-height: 1;
  color: var(--git-modified, #e2c08d);
}
.tree-lbl--new {
  color: var(--git-added, #73c991);
}
.tree-new-marker {
  flex: none;
  font-size: 0.72rem;
  font-weight: 700;
  line-height: 1;
  color: var(--git-added, #73c991);
}
</style>
