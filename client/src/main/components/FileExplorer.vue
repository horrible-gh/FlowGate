<template>
  <div class="sdb-panel" role="tree" :aria-label="t('main.explorer.files')">
    <div class="sdb-ph">
      <span class="sdb-ph-title">{{ t('main.explorer.files') }}</span>
      <div class="sdb-ph-acts">
        <button class="sdb-act-btn" :aria-label="t('main.explorer.retry')" @click="reload">
          <i class="fa-solid fa-rotate-right"></i>
        </button>
        <button
          class="sdb-act-btn"
          :aria-label="collapsed ? t('main.explorer.expand') : t('main.explorer.collapse')"
          @click="collapsed = !collapsed"
        >
          <i :class="collapsed ? 'fa-solid fa-expand' : 'fa-solid fa-compress'"></i>
        </button>
      </div>
    </div>
    <template v-if="!collapsed">
      <div class="sdb-scroll">
        <div v-if="loading" class="sdb-state">⏳ ...</div>
        <div v-else-if="error" class="sdb-state sdb-state--error">
          <span>{{ t('main.error.tree_load_failed') }}</span>
          <button @click="reload">{{ t('main.explorer.retry') }}</button>
        </div>
        <div v-else-if="!projectId" class="sdb-state">
          {{ t('main.state.no_project') }}
        </div>
        <ul v-else class="tree-ul">
          <li
            class="tree-node"
            @contextmenu.prevent.stop="onRootContextMenu($event)"
            @dragover.prevent="onRootDragOver"
            @dragleave="onRootDragLeave"
            @drop.capture="rootDragOver = false"
            @drop.prevent.stop="onRootDrop"
          >
            <div class="tree-row" :class="{ 'drag-over': rootDragOver }">
              <span class="tree-caret open"><span>▶</span></span>
              <span class="tree-ico proj"><i class="fa-solid fa-folder-open"></i></span>
              <span class="tree-lbl tree-lbl--project">{{ projectName }}</span>
            </div>
            <ul class="tree-children">
              <FileTreeNode
                v-for="node in rootNodes"
                :key="node.id"
                :node="node"
                :all-nodes="nodes"
                :project-id="projectId ?? ''"
                @open="openFile"
                @tree-changed="reload"
              />
            </ul>
          </li>
        </ul>
      </div>
    </template>
  </div>

  <ContextMenu v-model:visible="showRootCtx" :x="rootCtxX" :y="rootCtxY">
    <ContextMenuItem icon="fa-solid fa-folder-plus" @click="openRootCreateFolder">
      {{ t('main.file_tree_node.new_folder') }}
    </ContextMenuItem>
    <ContextMenuItem icon="fa-solid fa-file-circle-plus" @click="openRootCreateFile">
      {{ t('main.file_tree_node.new_file') }}
    </ContextMenuItem>
    <ContextMenuItem icon="fa-solid fa-rotate-right" @click="refreshFromMenu">
      {{ t('main.file_tree_node.refresh') }}
    </ContextMenuItem>
    <ContextMenuItem icon="fa-solid fa-upload" @click="triggerRootUploadFiles">
      {{ t('main.file_tree_node.upload_files') }}
    </ContextMenuItem>
    <ContextMenuItem icon="fa-solid fa-folder-arrow-up" @click="triggerRootUploadFolder">
      {{ t('main.file_tree_node.upload_folder') }}
    </ContextMenuItem>
  </ContextMenu>

  <input ref="rootFileInputRef" type="file" multiple style="display:none" @change="onRootFileSelected" />
  <input ref="rootFolderInputRef" type="file" multiple style="display:none" @change="onRootFolderSelected" />

  <CreateFileFolderModal
    v-if="projectId"
    v-model:visible="showModal"
    :type="modalType"
    :project-id="projectId"
    parent-path=""
    @saved="onRootCreated"
  />
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useExplorerStore, type FileNode } from '../stores/explorer'
import { useProjectStore } from '../stores/project'
import { useTabsStore } from '../stores/tabs'
import { useFileUpload } from '../composables/useFileUpload'
import api from '@shared/api'
import FileTreeNode from './FileTreeNode.vue'
import ContextMenu from './common/ContextMenu.vue'
import ContextMenuItem from './common/ContextMenuItem.vue'
import CreateFileFolderModal from './CreateFileFolderModal.vue'

const props = defineProps<{ projectId: string | null }>()
const { t } = useI18n()
const explorerStore = useExplorerStore()
const projectStore = useProjectStore()
const tabsStore = useTabsStore()
const { collectDropFiles, uploadFiles } = useFileUpload()

const nodes = ref<FileNode[]>([])
const loading = ref(false)
const error = ref(false)
const collapsed = ref(false)
const showRootCtx = ref(false)
const rootCtxX = ref(0)
const rootCtxY = ref(0)
const showModal = ref(false)
const modalType = ref<'folder' | 'file'>('folder')
const rootFileInputRef = ref<HTMLInputElement | null>(null)
const rootFolderInputRef = ref<HTMLInputElement | null>(null)
const rootDragOver = ref(false)

onMounted(() => {
  if (rootFolderInputRef.value) {
    rootFolderInputRef.value.setAttribute('webkitdirectory', '')
  }
})

const rootNodes = computed(() => nodes.value.filter((n) => n.parent_id === null))
const projectName = computed(() => projectStore.currentProject?.project_name ?? '')

function normPath(p: string): string {
  return p.replace(/\\/g, '/')
}

async function reload() {
  if (!props.projectId) return
  const silent = nodes.value.length > 0
  if (!silent) loading.value = true
  error.value = false
  try {
    nodes.value = await explorerStore.fetchFileTree(props.projectId, true)
    if (explorerStore.pendingSelectFilePath) {
      const target = normPath(explorerStore.pendingSelectFilePath)
      const found = nodes.value.find((n) => n.type === 'file' && normPath(n.path) === target)
      if (found) explorerStore.selectedFileNodeId = found.id
      explorerStore.pendingSelectFilePath = null
    }
  } catch {
    error.value = true
  } finally {
    loading.value = false
  }
}

const FILE_SIZE_LIMIT = 1024 * 1024
const MARKDOWN_EXTENSIONS = new Set(['.md', '.markdown'])

function fileExtension(path: string): string {
  const filename = normPath(path).split('/').pop() ?? path
  const dotIndex = filename.lastIndexOf('.')
  return dotIndex >= 0 ? filename.slice(dotIndex).toLowerCase() : ''
}

function isMarkdownFile(path: string): boolean {
  return MARKDOWN_EXTENSIONS.has(fileExtension(path))
}

function readContentLength(value: unknown): number | null {
  if (typeof value === 'number') return value
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number.parseInt(value, 10)
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

async function openFile(node: FileNode) {
  const projectId = props.projectId
  if (!projectId) return
  let type: 'md' | 'text' | 'too_large' = isMarkdownFile(node.path) ? 'md' : 'text'
  try {
    const url = `/api/v1/projects/${encodeURIComponent(projectId)}/files/src-content?path=${encodeURIComponent(node.path)}`
    const res = await api.head(url)
    const contentLength = readContentLength(res.headers['content-length'])
    if (contentLength != null && contentLength > FILE_SIZE_LIMIT) {
      type = 'too_large'
    }
  } catch {
    // HEAD failed — keep the extension-based viewer type.
  }
  tabsStore.openTab({
    id: node.id,
    title: node.label,
    path: node.path,
    type,
    mdPath: type === 'md' ? node.path : null,
    projectId,
  })
}

function onRootContextMenu(e: MouseEvent) {
  rootCtxX.value = e.clientX
  rootCtxY.value = e.clientY
  showRootCtx.value = true
}

function openRootCreateFolder() {
  showRootCtx.value = false
  modalType.value = 'folder'
  showModal.value = true
}

function openRootCreateFile() {
  showRootCtx.value = false
  modalType.value = 'file'
  showModal.value = true
}

function refreshFromMenu() {
  showRootCtx.value = false
  reload()
}

function onRootCreated(payload: { name: string; type: 'file' | 'folder' }) {
  if (payload.type === 'file') {
    explorerStore.pendingSelectFilePath = payload.name
  }
  reload()
}

// ── Root drag and drop ───────────────────────────────────────────────────────
function onRootDragOver() {
  rootDragOver.value = true
}

function onRootDragLeave(e: DragEvent) {
  const related = e.relatedTarget as Node | null
  if (!related || !(e.currentTarget as HTMLElement).contains(related)) {
    rootDragOver.value = false
  }
}

async function onRootDrop(e: DragEvent) {
  rootDragOver.value = false
  if (!props.projectId || !e.dataTransfer?.items) return
  const files = await collectDropFiles(e.dataTransfer.items)
  await uploadFiles(props.projectId, '', files, reload)
}

// ── Root upload button ───────────────────────────────────────────────────────
function triggerRootUploadFiles() {
  showRootCtx.value = false
  rootFileInputRef.value?.click()
}

function triggerRootUploadFolder() {
  showRootCtx.value = false
  rootFolderInputRef.value?.click()
}

async function onRootFileSelected(e: Event) {
  if (!props.projectId) return
  const input = e.target as HTMLInputElement
  const fileList = input.files
  if (!fileList || fileList.length === 0) return
  const files = Array.from(fileList).map((f) => {
    ;(f as any)._relativePath = f.name
    return f as File & { _relativePath?: string }
  })
  await uploadFiles(props.projectId, '', files, reload)
  input.value = ''
}

async function onRootFolderSelected(e: Event) {
  if (!props.projectId) return
  const input = e.target as HTMLInputElement
  const fileList = input.files
  if (!fileList || fileList.length === 0) return
  const files = Array.from(fileList).map((f) => {
    ;(f as any)._relativePath = f.webkitRelativePath || f.name
    return f as File & { _relativePath?: string }
  })
  await uploadFiles(props.projectId, '', files, reload)
  input.value = ''
}

watch(() => props.projectId, async (pid) => {
  if (!pid) { nodes.value = []; return }
  loading.value = true
  error.value = false
  try {
    nodes.value = await explorerStore.fetchFileTree(pid)
  } catch {
    error.value = true
  } finally {
    loading.value = false
  }
}, { immediate: true })
</script>

<style scoped>
.tree-lbl--project {
  color: rgba(255, 255, 255, 0.85);
  font-weight: 600;
}
</style>
