<template>
  <!-- flowgate.v02.0043 — File › 새 Flow: the v0.1 group create (features/project createFlow)
       under a Group of the current Project, with the two values that contract reads — the Group
       and the name. The Groups come from the same tree the Project Explorer shows. Emits the
       created Flow with its Group (id and name: the create has just invalidated the cached tree,
       so the Shell cannot look the name up); the Shell re-reads the tree and opens the Flow. -->
  <ModalShell :open="open" size="md" close-on-backdrop :aria-describedby="submitError ? errorId : undefined" @close="emit('close')">
    <template #header="{ titleId }">
      <div class="dfm-head">
        <h2 :id="titleId" class="dfm-title">{{ t('gui.designer.file_menu.flow_new_title') }}</h2>
        <IconButton :label="t('gui.designer.file_menu.close')" variant="ghost" @click="emit('close')">✕</IconButton>
      </div>
    </template>

    <template #default>
      <Alert v-if="submitError" :id="errorId" tone="danger" class="dfm-alert" data-test="designer-flow-create-error">{{ submitError }}</Alert>
      <form class="dfm-form" data-test="designer-flow-create" @submit.prevent="submit">
        <div class="dfm-field">
          <label class="dfm-label" for="dfc-group">{{ t('gui.designer.file_menu.group') }}</label>
          <Select
            id="dfc-group"
            v-model="groupId"
            :options="groupOptions"
            :disabled="loading"
            :invalid="showGroupError"
            data-test="designer-flow-create-group"
          />
          <p v-if="showGroupError" class="dfm-field-error">{{ t('gui.designer.file_menu.group_required') }}</p>
        </div>

        <div class="dfm-field">
          <label class="dfm-label" for="dfc-title">{{ t('gui.designer.file_menu.name') }}</label>
          <Input
            id="dfc-title"
            v-model="title"
            :invalid="showTitleError"
            data-modal-autofocus
            data-test="designer-flow-create-title"
          />
          <p v-if="showTitleError" class="dfm-field-error">{{ t('gui.designer.file_menu.name_required') }}</p>
        </div>
      </form>
    </template>

    <template #footer>
      <div class="dfm-actions">
        <Button variant="secondary" data-test="designer-flow-create-cancel" @click="emit('close')">{{ t('gui.designer.file_menu.cancel') }}</Button>
        <Button variant="primary" :disabled="submitting" :loading="submitting" data-test="designer-flow-create-submit" @click="submit">
          {{ t('gui.designer.file_menu.create') }}
        </Button>
      </div>
    </template>
  </ModalShell>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Alert from '../../../components/ui/Alert.vue'
import Button from '../../../components/ui/Button.vue'
import IconButton from '../../../components/ui/IconButton.vue'
import Input from '../../../components/ui/Input.vue'
import ModalShell from '../../../components/ui/ModalShell.vue'
import Select, { type SelectOption } from '../../../components/ui/Select.vue'
import { useProjectStore, type GroupTreeNode } from '@/features/project'
import { buildExplorerProject, projectNodeId, type ExplorerGroup } from '../projectExplorer/projectExplorerTree'

export interface DesignerCreatedFlow {
  /** The v0.1 group id the server reserved. */
  id: string
  title: string
  /** The Group (tree node id) the Flow was created under. */
  groupId: string
  /** That Group's display name, as the dialog's Group select showed it. */
  groupName: string
}

const props = defineProps<{
  open: boolean
  projectId: string | null
  /** Group preselected when the dialog opens (the Explorer's chosen Group / the active Flow's). */
  groupId?: string | null
}>()

const emit = defineEmits<{
  close: []
  created: [flow: DesignerCreatedFlow]
}>()

const { t } = useI18n()
const projectStore = useProjectStore()

const errorId = 'designer-flow-create-error'

const nodes = ref<GroupTreeNode[]>([])
const loading = ref(false)
const groupId = ref('')
const title = ref('')
const submitting = ref(false)
const submitError = ref<string | null>(null)
const attemptedSubmit = ref(false)

const groups = computed<ExplorerGroup[]>(() =>
  props.projectId ? (buildExplorerProject(nodes.value, projectNodeId(props.projectId))?.groups ?? []) : [],
)

const groupOptions = computed<SelectOption[]>(() => groups.value.map((group) => ({ value: group.id, label: group.name })))

const selectedGroup = computed(() => groups.value.find((group) => group.id === groupId.value) ?? null)

const titleValid = computed(() => title.value.trim() !== '')
const showTitleError = computed(() => attemptedSubmit.value && !titleValid.value)
const showGroupError = computed(() => attemptedSubmit.value && selectedGroup.value === null)

let generation = 0

async function load() {
  const pid = props.projectId
  if (!pid) return
  const myGeneration = generation
  loading.value = true
  try {
    const next = await projectStore.fetchGroupTree(pid)
    if (myGeneration !== generation) return
    nodes.value = next
    // Keep the preselected Group when it exists; otherwise the first Group.
    if (!groups.value.some((group) => group.id === groupId.value)) groupId.value = groups.value[0]?.id ?? ''
  } catch (err) {
    if (myGeneration !== generation) return
    submitError.value = err instanceof Error && err.message ? err.message : t('gui.designer.file_menu.load_failed')
  } finally {
    if (myGeneration === generation) loading.value = false
  }
}

watch(
  () => props.open,
  (isOpen) => {
    generation += 1
    if (!isOpen) return
    nodes.value = []
    groupId.value = props.groupId ?? ''
    title.value = ''
    submitting.value = false
    submitError.value = null
    attemptedSubmit.value = false
    void load()
  },
)

async function submit() {
  attemptedSubmit.value = true
  const pid = props.projectId
  const group = selectedGroup.value
  if (!pid || !group || !titleValid.value || submitting.value) return
  const myGeneration = generation
  submitting.value = true
  submitError.value = null
  try {
    const created = await projectStore.createFlow({ projectId: pid, moduleName: group.moduleName, title: title.value.trim() })
    if (myGeneration !== generation) return
    emit('created', { id: created.id, title: created.title, groupId: group.id, groupName: group.name })
  } catch (err) {
    if (myGeneration !== generation) return
    submitError.value = err instanceof Error && err.message ? err.message : t('gui.designer.file_menu.create_failed')
  } finally {
    if (myGeneration === generation) submitting.value = false
  }
}
</script>

<style scoped>
.dfm-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 20px 24px 0;
}

.dfm-title {
  margin: 0;
  font-size: 16px;
  font-weight: 700;
  color: var(--fg2-text);
}

.dfm-alert {
  margin: 16px 0 0;
}

.dfm-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 16px 0 4px;
}

.dfm-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.dfm-label {
  font-size: 12px;
  font-weight: 650;
  color: var(--fg2-muted);
}

.dfm-field-error {
  margin: 0;
  font-size: 11px;
  color: var(--fg2-status-danger-fg);
}

.dfm-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 16px 24px 20px;
}
</style>
