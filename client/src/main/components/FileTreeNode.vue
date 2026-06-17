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
      <span class="tree-ico"><i :class="iconFA.cls" :style="{ color: iconFA.color }"></i></span>
      <span class="tree-lbl">{{ node.label }}</span>
      <span v-if="downloading" class="tree-loading"><i class="fa-solid fa-spinner fa-spin"></i></span>
    </div>
    <ul v-if="node.type === 'folder' && expanded" class="tree-children">
      <FileTreeNode
        v-for="child in children"
        :key="child.id"
        :node="child"
        :all-nodes="allNodes"
        :project-id="projectId"
        @open="$emit('open', $event)"
        @tree-changed="$emit('tree-changed')"
      />
    </ul>
    <ContextMenu v-model:visible="showCtx" :x="ctxX" :y="ctxY">
      <ContextMenuItem v-if="node.type === 'file'" icon="fa-solid fa-arrow-up-right" @click="openFile">
        {{ t('main.file_tree_node.open') }}
      </ContextMenuItem>
      <template v-if="node.type === 'folder'">
        <ContextMenuItem icon="fa-solid fa-chevron-right" @click="toggleExpand">
          {{ t('main.file_tree_node.open') }}
        </ContextMenuItem>
        <ContextMenuItem icon="fa-solid fa-folder-plus" @click="openCreateFolder">
          {{ t('main.file_tree_node.new_folder') }}
        </ContextMenuItem>
        <ContextMenuItem icon="fa-solid fa-file-circle-plus" @click="openCreateFile">
          {{ t('main.file_tree_node.new_file') }}
        </ContextMenuItem>
        <ContextMenuItem icon="fa-solid fa-rotate-right" @click="doRefresh">
          {{ t('main.file_tree_node.refresh') }}
        </ContextMenuItem>
        <ContextMenuItem icon="fa-solid fa-upload" @click="openUploadFiles">
          {{ t('main.file_tree_node.upload_files') }}
        </ContextMenuItem>
        <ContextMenuItem icon="fa-solid fa-folder-arrow-up" @click="openUploadFolder">
          {{ t('main.file_tree_node.upload_folder') }}
        </ContextMenuItem>
      </template>
      <ContextMenuItem icon="fa-solid fa-link" @click="copyLink">
        {{ t('main.file_tree_node.copy_link') }}
      </ContextMenuItem>
      <ContextMenuItem icon="fa-solid fa-download" @click="downloadNode">
        {{ t('main.file_tree_node.download') }}
      </ContextMenuItem>
    </ContextMenu>

    <CreateFileFolderModal
      v-model:visible="showModal"
      :type="modalType"
      :project-id="projectId"
      :parent-path="node.path"
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
import { useToast } from './common/useToast'
import { useFileUpload } from '../composables/useFileUpload'
import { downloadBlobRequest } from '@shared/api'
import ContextMenu from './common/ContextMenu.vue'
import ContextMenuItem from './common/ContextMenuItem.vue'
import CreateFileFolderModal from './CreateFileFolderModal.vue'

const props = defineProps<{
  node: FileNode
  allNodes: FileNode[]
  projectId: string
}>()

const emit = defineEmits<{
  open: [node: FileNode]
  'tree-changed': []
}>()

const { t } = useI18n()
const explorerStore = useExplorerStore()
const { showToast } = useToast()
const { collectDropFiles, uploadFiles } = useFileUpload()
const expanded = ref(false)
const showCtx = ref(false)
const ctxX = ref(0)
const ctxY = ref(0)
const showModal = ref(false)
const modalType = ref<'folder' | 'file'>('folder')
const downloading = ref(false)
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

const iconFA = computed(() => {
  if (props.node.type === 'folder') {
    return expanded.value
      ? { cls: 'fa-solid fa-folder-open', color: '#f59e0b' }
      : { cls: 'fa-solid fa-folder', color: '#f59e0b' }
  }
  if (props.node.name.endsWith('.md')) return { cls: 'fa-solid fa-file-lines', color: '#60a5fa' }
  return { cls: 'fa-solid fa-file', color: '#94a3b8' }
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

function toggleExpand() {
  expanded.value = !expanded.value
  showCtx.value = false
}

function onContextMenu(e: MouseEvent) {
  ctxX.value = e.clientX
  ctxY.value = e.clientY
  showCtx.value = true
}

function copyLink() {
  navigator.clipboard?.writeText(props.node.path).catch(() => {})
  showCtx.value = false
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
    const res = await downloadBlobRequest(url, { path: props.node.path })
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
    else showToast(t('main.file_tree_node.download_failed'), 'danger')
  } finally {
    downloading.value = false
  }
}

function onCreated(payload: { name: string; type: 'file' | 'folder' }) {
  expanded.value = true
  if (payload.type === 'file') {
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
  if (props.node.type !== 'folder') return
  e.stopPropagation()
  nodeDragOver.value = false
  if (!e.dataTransfer?.items) return
  const files = await collectDropFiles(e.dataTransfer.items)
  await uploadFiles(props.projectId, props.node.path, files, () => {
    expanded.value = true
    emit('tree-changed')
  })
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
  })
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
  })
  input.value = ''
}
</script>
