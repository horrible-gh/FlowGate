<template>
  <li
    role="treeitem"
    :aria-expanded="isExpandable ? expanded : undefined"
    :tabindex="0"
    class="tree-node"
    @click.stop="handleClick"
    @keydown.enter.space.prevent="handleClick"
    @keydown.right.prevent="expanded = true"
    @keydown.left.prevent="expanded = false"
    @contextmenu.prevent.stop="onContextMenu($event)"
  >
    <div class="tree-row" :class="[rowClass, { active: isSelected }]">
      <span class="tree-caret" :class="{ open: expanded, leaf: !isExpandable }">
        <span v-if="isExpandable">▶</span>
      </span>
      <span v-if="node.node_type === 'document' && node.type_code" class="doc-tag" :class="`c-${node.type_code}`">
        {{ node.type_code }}
      </span>
      <span v-else class="tree-ico"><AppIcon :name="iconFA.cls" :style="{ color: iconFA.color }" /></span>
      <span class="tree-lbl" :class="{ 'tree-lbl--meta': node.node_type === 'module', 'tree-lbl--project': node.node_type === 'project' }">{{ label }}</span>
      <span
        v-if="workflowState?.docClass"
        :class="['dc-badge', `dc-${workflowState.docClass}`]"
        :title="t('main.group_tree_node.dc_badge_title', { docClass: workflowState.docClass })"
      >{{ _DC_LABELS[workflowState.docClass] ?? workflowState.docClass }}</span>
      <span
        v-if="workflowState?.nodeStatus"
        :class="['node-state', workflowState.nodeStatus]"
        :title="_NS_META[workflowState.nodeStatus]?.label"
      >
        <AppIcon :name="_NS_META[workflowState.nodeStatus]?.icon ?? 'circle'" style="font-size:.5rem;" />
        {{ _NS_META[workflowState.nodeStatus]?.label }}
      </span>
    </div>
    <ul v-if="isExpandable && expanded" class="tree-children">
      <GroupTreeNode
        v-for="child in children"
        :key="child.id"
        :node="child"
        :all-nodes="allNodes"
        :tree-nodes="treeNodes"
        :project-id="projectId"
        @open="$emit('open', $event)"
        @tree-changed="$emit('tree-changed', $event)"
        @create-requirement="$emit('create-requirement', $event)"
      />
    </ul>
    <ContextMenu v-model:visible="showCtx" :x="ctxX" :y="ctxY">
      <ContextMenuItem v-if="node.node_type === 'document'" icon="arrow-up-right" @click="toggleOpen">
        {{ t('main.group_tree_node.text_27') }}
      </ContextMenuItem>
      <template v-if="node.node_type === 'group'">
        <ContextMenuItem v-if="isEmptyGroup" icon="file-plus" @click="openCreateRequirement">
          {{ t('main.group_tree_node.new_requirement') }}
        </ContextMenuItem>
        <ContextMenuItem icon="pencil-simple" @click="openEditGroup">
          {{ t('main.group_tree_node.edit_group') }}
        </ContextMenuItem>
        <div class="ctx-separator" role="separator"></div>
        <ContextMenuItem icon="trash" :danger="true" @click="openDisposeConfirm">
          {{ t('main.group_tree_node.dispose_group') }}
        </ContextMenuItem>
      </template>
      <template v-if="node.node_type === 'project'">
        <ContextMenuItem icon="stack" @click="openCreateModule">
          {{ t('main.group_tree_node.new_group') }}
        </ContextMenuItem>
      </template>
      <template v-if="node.node_type === 'module'">
        <ContextMenuItem icon="folder-simple-plus" @click="openCreateGroup">
          {{ t('main.group_tree_node.new_group_child') }}
        </ContextMenuItem>
        <ContextMenuItem icon="pencil-simple" @click="openEditModule">
          {{ t('main.group_tree_node.edit_module') }}
        </ContextMenuItem>
      </template>
    </ContextMenu>

    <CreateEditGroupModal
      v-model:visible="showCreateEditModal"
      :mode="createEditMode"
      :dialog-mode="createGroupDialogMode"
      :project-id="projectId"
      :parent-id="createParentId"
      :module-name="createModuleName"
      :group="editGroupData"
      :edit-module="editModuleData"
      @saved="onGroupSaved"
    />
    <GroupDiscardModal
      v-if="node.node_type === 'group'"
      v-model:visible="showDisposeConfirm"
      :group-title="node.label"
      :documents="disposeDocuments"
      :submitting="disposing"
      @confirm="onConfirmDispose"
    />
  </li>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { postRequest } from '@shared/api'
import { type GroupNode } from '../stores/explorer'
import { useExplorerStore } from '../stores/explorer'
import CreateEditGroupModal from './CreateEditGroupModal.vue'
import GroupDiscardModal from './GroupDiscardModal.vue'
import ContextMenu from './common/ContextMenu.vue'
import ContextMenuItem from './common/ContextMenuItem.vue'
import AppIcon from '@shared/AppIcon.vue'
import { useToast } from './common/useToast'

const props = defineProps<{
  node: GroupNode
  allNodes: GroupNode[]
  treeNodes?: GroupNode[]
  projectId: string
}>()

const emit = defineEmits<{
  open: [node: GroupNode]
  'tree-changed': [revealNodeId?: string]
  'create-requirement': [payload: { groupId: string }]
}>()

const { t } = useI18n()
const { showToast } = useToast()
const explorerStore = useExplorerStore()

const isSelected = computed(() =>
  props.node.node_type === 'document' && explorerStore.selectedGroupNodeId === props.node.id,
)

function _lsKey() {
  return `flowgate:grp-exp:${props.projectId}:${props.node.id}`
}

function _readExpanded(): boolean {
  if (props.node.node_type === 'document') return false
  try {
    const v = localStorage.getItem(_lsKey())
    return v === null ? false : v === '1'
  } catch {
    return false
  }
}

function _saveExpanded(val: boolean): void {
  try {
    localStorage.setItem(_lsKey(), val ? '1' : '0')
  } catch { /* ignore — e.g. private mode quota */ }
}

const expanded = ref(_readExpanded())
watch(expanded, (val) => {
  if (props.node.node_type !== 'document') _saveExpanded(val)
})
const showCtx = ref(false)
const ctxX = ref(0)
const ctxY = ref(0)
const showCreateEditModal = ref(false)
const createEditMode = ref<'create' | 'edit'>('create')
const createParentId = ref<string | null>(null)
const createModuleName = ref<string | null>(null)
const createGroupDialogMode = ref<'module' | 'group'>('group')
const editGroupData = ref<{ group_id: string; title: string; module: string; priority: string | null } | undefined>(undefined)
const editModuleData = ref<{ module_id: string; name: string; title: string } | undefined>(undefined)
const showDisposeConfirm = ref(false)
const disposing = ref(false)

const isExpandable = computed(() => props.node.node_type !== 'document')

// Member documents of this group, for the discard modal's impact chips.
const disposeDocuments = computed(() =>
  props.allNodes
    .filter((n) => n.node_type === 'document' && n.parent_id === props.node.id)
    .map((n) => {
      const tc = n.type_code ?? ''
      const seq = (n.number ?? '').split('-')[0]
      return { id: n.id, typeCode: tc, shortId: `${tc}${seq}` }
    }),
)

const children = computed(() =>
  props.allNodes.filter((n) => n.parent_id === props.node.id),
)

const sourceNodes = computed(() => props.treeNodes ?? props.allNodes)

const isEmptyGroup = computed(() => {
  if (props.node.node_type !== 'group') return false
  return !hasDocumentDescendant(props.node.id)
})

function hasDocumentDescendant(nodeId: string): boolean {
  const stack = sourceNodes.value.filter((n) => n.parent_id === nodeId)
  while (stack.length > 0) {
    const child = stack.pop()
    if (!child) continue
    if (child.node_type === 'document') return true
    stack.push(...sourceNodes.value.filter((n) => n.parent_id === child.id))
  }
  return false
}

const iconFA = computed(() => {
  if (props.node.node_type === 'project') return { cls: 'tree-structure', color: '#34d399' }
  if (props.node.node_type === 'module') return { cls: 'stack', color: '#7c3aed' }
  return expanded.value
    ? { cls: 'folder-open', color: '#f59e0b' }
    : { cls: 'folder', color: '#f59e0b' }
})

const rowClass = computed(() => ({
  'tree-row--module': props.node.node_type === 'module',
}))

const _NS_META = computed(() => ({
  'ns-pending':  { icon: 'clock',         label: t('main.group_tree_node.ns_pending')  },
  'ns-approved': { icon: 'check-circle',    label: t('main.group_tree_node.ns_approved') },
  'ns-advanced': { icon: 'skip-forward',    label: t('main.group_tree_node.ns_advanced') },
  'ns-next-act': { icon: 'arrow-right',     label: t('main.group_tree_node.ns_next_act') },
  'ns-done':     { icon: 'checks',    label: t('main.group_tree_node.ns_done')     },
}))

const _DC_LABELS = computed(() => ({
  R: t('main.group_tree_node.dc_req'),
  Q: t('main.group_tree_node.dc_q'),
  B: t('main.group_tree_node.dc_bug'),
}))

const workflowState = computed(() =>
  props.node.node_type === 'document'
    ? (explorerStore.workflowNodeStates[props.node.id] ?? null)
    : null
)

const extractFileName = (fullPath: string): string => {
  return fullPath.split(/[\\/]/).pop() ?? fullPath
}

const label = computed(() => {
  if (props.node.node_type === 'document' && props.node.number) {
    const fileName = props.node.filename ? extractFileName(props.node.filename) : props.node.label
    return `${props.node.number}: ${fileName}`
  }
  if (props.node.node_type === 'group' && props.node.number) {
    return `${props.node.number}: ${props.node.label}`
  }
  if (props.node.node_type === 'module') {
    return (props.node as unknown as { title?: string }).title || props.node.label
  }
  return props.node.label
})

function handleClick() {
  if (props.node.node_type === 'document') {
    explorerStore.selectedGroupNodeId = props.node.id
    emit('open', props.node)
  } else {
    expanded.value = !expanded.value
  }
}

function toggleOpen() {
  handleClick()
  showCtx.value = false
}

function onContextMenu(e: MouseEvent) {
  ctxX.value = e.clientX
  ctxY.value = e.clientY
  showCtx.value = true
}


function openCreateModule() {
  showCtx.value = false
  createEditMode.value = 'create'
  createParentId.value = null
  createModuleName.value = null
  createGroupDialogMode.value = 'module'
  editGroupData.value = undefined
  showCreateEditModal.value = true
}

function openCreateGroup() {
  showCtx.value = false
  createEditMode.value = 'create'
  createParentId.value = null
  createModuleName.value = props.node.label
  createGroupDialogMode.value = 'group'
  editGroupData.value = undefined
  showCreateEditModal.value = true
}

function openEditGroup() {
  showCtx.value = false
  createEditMode.value = 'edit'
  createParentId.value = null
  createModuleName.value = null
  createGroupDialogMode.value = 'group'
  editGroupData.value = {
    group_id: props.node.id,
    title: props.node.label,
    module: 'none',
    priority: null,
  }
  editModuleData.value = undefined
  showCreateEditModal.value = true
}

function openEditModule() {
  showCtx.value = false
  createEditMode.value = 'edit'
  createParentId.value = null
  createModuleName.value = null
  createGroupDialogMode.value = 'module'
  editGroupData.value = undefined
  editModuleData.value = {
    module_id: props.node.id,
    name: props.node.label,
    title: (props.node as unknown as { title?: string }).title || props.node.label,
  }
  showCreateEditModal.value = true
}

function openCreateRequirement() {
  showCtx.value = false
  emit('create-requirement', { groupId: props.node.id })
}

function openDisposeConfirm() {
  showCtx.value = false
  showDisposeConfirm.value = true
}

function onGroupSaved(groupId: string) {
  expanded.value = true
  _saveExpanded(true)
  emit('tree-changed', groupId)
}

async function onConfirmDispose(reason: string) {
  if (disposing.value) return
  disposing.value = true
  try {
    // Send the required reason as reason_detail (the BE dispose endpoint already
    // accepts it). Previously this POSTed an empty body and swallowed any failure,
    // which is the "press discard → nothing happens / error" symptom in R0001.
    const res = await postRequest<any>(`/api/v1/groups/${encodeURIComponent(props.node.id)}/dispose`, { reason_detail: reason })
    if ((res.data as any)?.status === 'error') {
      showToast((res.data as any)?.message ?? t('main.group_actions.discard_error'), 'error')
      return
    }
    showDisposeConfirm.value = false
    showToast(t('main.group_actions.discard_success'), 'success')
    emit('tree-changed')
  } catch (e: any) {
    // Surface the failure instead of swallowing it (R0001). Keep the modal open so
    // the user can retry without re-entering the reason.
    const msg = e?.response?.data?.detail ?? e?.response?.data?.message ?? t('main.group_actions.discard_error')
    showToast(msg, 'error')
  } finally {
    disposing.value = false
  }
}
</script>

<style scoped>
.tree-lbl--project {
  color: rgba(255, 255, 255, 0.85);
  font-weight: 600;
}

.ctx-separator {
  height: 1px;
  margin: 4px 8px;
  background: var(--border);
}
</style>
