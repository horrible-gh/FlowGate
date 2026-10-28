<template>
  <!-- Group/tree and work-base reads run independently; only server-confirmed options allow creation. -->
  <ModalShell :open="open" size="md" close-on-backdrop :aria-describedby="submitError ? errorId : undefined" @close="emit('close')">
    <template #header="{ titleId }">
      <div class="dfm-head">
        <h2 :id="titleId" class="dfm-title">{{ t('gui.designer.file_menu.flow_new_title') }}</h2>
        <IconButton :label="t('gui.designer.file_menu.close')" variant="ghost" @click="emit('close')">✕</IconButton>
      </div>
    </template>

    <template #default>
      <Alert v-if="submitError" :id="errorId" tone="danger" class="dfm-alert" data-test="designer-flow-create-error">{{ submitErrorText }}</Alert>
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
        <div v-if="baseState !== 'ready-disabled'" class="dfm-field" data-test="designer-flow-create-base-field">
          <label class="dfm-label" for="dfc-base">{{ t('gui.designer.file_menu.base_branch') }}</label>
          <Select id="dfc-base" v-model="workBaseRef" :options="branchOptions"
            :placeholder="t('gui.designer.file_menu.base_branch_required')"
            :disabled="baseState !== 'ready-enabled' || submitting" data-test="designer-flow-create-base" />
          <p v-if="baseState === 'unknown' || baseState === 'loading'">{{ t('gui.designer.file_menu.loading') }}</p>
          <Alert v-if="baseError" tone="danger" data-test="designer-flow-create-base-error">{{ baseErrorText }}</Alert>
          <p v-if="baseState === 'ready-enabled' && !branchValid" class="dfm-field-error">{{ t('gui.designer.file_menu.base_branch_required') }}</p>
          <Button variant="secondary" :disabled="baseState === 'loading' || submitting"
            data-test="designer-flow-create-base-retry" @click="loadOptions(true)">{{ t('gui.designer.file_menu.retry') }}</Button>
        </div>
      </form>
    </template>

    <template #footer>
      <div class="dfm-actions">
        <Button variant="secondary" data-test="designer-flow-create-cancel" @click="emit('close')">{{ t('gui.designer.file_menu.cancel') }}</Button>
        <Button variant="primary" :disabled="submitting || !baseReady" :loading="submitting" data-test="designer-flow-create-submit" @click="submit">
          {{ t('gui.designer.file_menu.create') }}
        </Button>
      </div>
    </template>
  </ModalShell>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useCurrentUser } from '@/core/auth/currentUser'
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
// flowgate.v02.0088 F10: the server's own words raw, otherwise the localized fallback key —
// rendered in the current locale while it stays on screen. (The adapter leaves only the server's
// words in Error.message; an empty one means there were none.)
type SubmitError = { readonly key: string } | { readonly raw: string }
const submitError = ref<SubmitError | null>(null)
const submitErrorText = computed(() => {
  const error = submitError.value
  return error ? ('raw' in error ? error.raw : t(error.key)) : ''
})

function failureOf(err: unknown, key: string): SubmitError {
  const message = err instanceof Error ? err.message.trim() : ''
  return message ? { raw: message } : { key }
}
const attemptedSubmit = ref(false)
const { user, status: userStatus } = useCurrentUser()
type BaseState = 'unknown' | 'loading' | 'ready-enabled' | 'ready-disabled' | 'error'
const baseState = ref<BaseState>('unknown')
const branchOptions = ref<SelectOption[]>([])
const workBaseRef = ref('')
const baseError = ref<SubmitError | null>(null)
const baseErrorText = computed(() => {
  const error = baseError.value
  return error ? ('raw' in error ? error.raw : t(error.key)) : ''
})
const branchValid = computed(() => workBaseRef.value !== '' && branchOptions.value.some((o) => o.value === workBaseRef.value))
const baseReady = computed(() => baseState.value === 'ready-disabled' || (baseState.value === 'ready-enabled' && branchValid.value))
let selectedOnce = false
let optionsGeneration = 0

async function loadOptions(retry = false) {
  const pid = props.projectId
  if (!props.open || !pid || baseState.value === 'loading') return
  const session = generation
  const request = ++optionsGeneration
  if (retry) projectStore.invalidateWorkBaseOptions(pid)
  baseState.value = 'loading'
  baseError.value = null
  try {
    const options = await projectStore.loadWorkBaseOptions(pid, { force: true })
    if (session !== generation || request !== optionsGeneration) return
    branchOptions.value = options.branches.map((branch) => ({ value: branch.name, label: branch.name }))
    if (selectedOnce) {
      if (!branchValid.value) workBaseRef.value = ''
    } else {
      workBaseRef.value = options.baseBranch && branchOptions.value.some((b) => b.value === options.baseBranch) ? options.baseBranch : ''
    }
    selectedOnce = true
    baseState.value = options.gitEnabled ? 'ready-enabled' : 'ready-disabled'
  } catch (error) {
    if (session !== generation || request !== optionsGeneration) return
    baseState.value = 'error'
    baseError.value = failureOf(error, 'gui.designer.file_menu.base_branch_failed')
  }
}

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
    submitError.value = failureOf(err, 'gui.designer.file_menu.load_failed')
  } finally {
    if (myGeneration === generation) loading.value = false
  }
}

watch(
  [() => props.open, () => props.projectId, user, userStatus],
  ([isOpen, pid], previous) => {
    generation += 1
    optionsGeneration += 1
    baseState.value = 'unknown'
    submitting.value = false
    if (!isOpen) return
    const opening = !previous?.[0]
    nodes.value = []
    loading.value = false
    groupId.value = props.groupId ?? (opening ? '' : groupId.value)
    if (opening) {
      title.value = ''
      workBaseRef.value = ''
      selectedOnce = false
    }
    branchOptions.value = []
    baseError.value = null
    submitError.value = null
    attemptedSubmit.value = false
    if (pid) {
      void load()
      void loadOptions()
    }
  },
  { immediate: true, flush: 'sync', deep: true },
)

onBeforeUnmount(() => { generation += 1 })

async function submit() {
  attemptedSubmit.value = true
  const pid = props.projectId
  const group = selectedGroup.value
  if (!props.open || !pid || !group || !titleValid.value || !baseReady.value || submitting.value) return
  const myGeneration = generation
  submitting.value = true
  submitError.value = null
  try {
    const created = await projectStore.createFlow({
      projectId: pid, moduleName: group.moduleName, title: title.value.trim(),
      ...(baseState.value === 'ready-enabled' ? { workBaseRef: workBaseRef.value } : {}),
    })
    if (myGeneration !== generation) return
    emit('created', { id: created.id, title: created.title, groupId: group.id, groupName: group.name })
  } catch (err) {
    if (myGeneration !== generation) return
    submitError.value = failureOf(err, 'gui.designer.file_menu.create_failed')
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
