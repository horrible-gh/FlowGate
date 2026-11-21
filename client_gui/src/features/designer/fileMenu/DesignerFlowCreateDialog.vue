<template>
  <!-- Group/tree and work-base reads run independently; only server-confirmed options allow creation. -->
  <ModalShell :open="open" size="md" surface-class="dfm-create-dialog"
    :aria-describedby="submitError ? errorId : undefined" @close="emit('close')">
    <template #header="{ titleId }">
      <div class="dfm-head">
        <h2 :id="titleId" class="dfm-title">{{ t('gui.designer.file_menu.flow_new_title') }}</h2>
        <button type="button" class="dfm-close" :aria-label="t('gui.designer.file_menu.close')"
          :title="t('gui.designer.file_menu.close')" data-test="designer-flow-create-close" @click="emit('close')">
          <svg width="19" height="19" viewBox="0 0 20 20" fill="none" aria-hidden="true">
            <path d="M5 5l10 10M15 5L5 15" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" />
          </svg>
        </button>
      </div>
    </template>

    <template #default>
      <Alert v-if="submitError" :id="errorId" tone="danger" class="dfm-alert" data-test="designer-flow-create-error">{{ submitErrorText }}</Alert>
      <form ref="formElement" class="dfm-form" data-test="designer-flow-create" @submit.prevent="submit">
        <div class="dfm-field">
          <label class="dfm-label" for="dfc-group">{{ t('gui.designer.file_menu.flow_group') }}</label>
          <div class="dfm-control" :class="{ 'is-disabled': loading, 'has-error': showGroupError }">
            <Select id="dfc-group" :model-value="groupId" @update:model-value="pickGroup" :options="groupOptions" :disabled="loading"
              :invalid="showGroupError" :aria-invalid="showGroupError || undefined"
              :aria-describedby="showGroupError ? 'dfc-group-error' : undefined" data-test="designer-flow-create-group" />
            <svg class="dfm-chevron" viewBox="0 0 16 16" fill="none" aria-hidden="true">
              <path d="m4 6 4 4 4-4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
          </div>
          <p v-if="showGroupError" id="dfc-group-error" class="dfm-field-error" role="status">{{ t('gui.designer.file_menu.group_required') }}</p>
        </div>

        <div class="dfm-field">
          <label class="dfm-label" for="dfc-title">{{ t('gui.designer.file_menu.name') }}</label>
          <div class="dfm-control" :class="{ 'has-error': showTitleError }">
            <Input id="dfc-title" v-model="title" :invalid="showTitleError" autocomplete="off"
              :aria-describedby="showTitleError ? 'dfc-title-error' : undefined"
              data-modal-autofocus data-test="designer-flow-create-title" />
          </div>
          <p v-if="showTitleError" id="dfc-title-error" class="dfm-field-error" role="status">{{ t('gui.designer.file_menu.name_required') }}</p>
        </div>

        <div v-if="baseState !== 'ready-disabled'" class="dfm-field" data-test="designer-flow-create-base-field">
          <label class="dfm-label" for="dfc-base">{{ t('gui.designer.file_menu.base_branch') }}</label>
          <!-- 0020 mock ①: own list (local branches, then a "remote only" group with badges) and an
               always-present [Import] beside the field that turns on for a remote branch. -->
          <div class="dfm-branch-row">
            <div class="dfm-control dfm-branch-control"
              :class="{ 'is-disabled': baseTriggerDisabled, 'has-error': baseState === 'error' }"
              :aria-busy="baseBusy || undefined">
              <svg class="dfm-branch-icon" viewBox="0 0 20 20" fill="none" aria-hidden="true">
                <circle cx="5.5" cy="4.5" r="2" stroke="currentColor" stroke-width="1.5" />
                <circle cx="5.5" cy="15.5" r="2" stroke="currentColor" stroke-width="1.5" />
                <circle cx="15" cy="8" r="2" stroke="currentColor" stroke-width="1.5" />
                <path d="M5.5 6.5v7M13 8h-3.2C7.5 8 5.5 10 5.5 12.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
              </svg>
              <button id="dfc-base" ref="baseTrigger" type="button" class="dfm-base-trigger" role="combobox"
                aria-haspopup="listbox" :aria-expanded="listOpen ? 'true' : 'false'" aria-controls="dfc-base-list"
                :aria-activedescendant="listOpen && activeIndex >= 0 ? `dfc-base-opt-${activeIndex}` : undefined"
                :value="baseState === 'ready-enabled' ? workBaseRef : ''" :disabled="baseTriggerDisabled"
                :aria-invalid="baseState === 'error' || undefined"
                :aria-describedby="baseHint ? 'dfc-base-hint' : undefined"
                data-test="designer-flow-create-base" @click="toggleList" @keydown="onTriggerKeydown" @blur="closeList">
                <span class="dfm-base-text" :class="`is-${baseDisplay.tone}`">{{ baseDisplay.text }}</span>
              </button>
              <span v-if="baseBusy" class="dfm-spinner" role="status"
                :aria-label="t('gui.designer.file_menu.base_branch_loading')" data-test="designer-flow-create-base-loading" />
              <button v-else-if="baseState === 'error'" type="button" class="dfm-retry" :disabled="submitting"
                :aria-label="t('gui.designer.file_menu.base_branch_retry')" :title="t('gui.designer.file_menu.base_branch_retry')"
                data-test="designer-flow-create-base-retry" @click="retryOptions">
                <svg viewBox="0 0 20 20" fill="none" aria-hidden="true">
                  <path d="M15.8 6.8A6.2 6.2 0 1 0 16 12M16 3v4.5h-4.5" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" />
                </svg>
              </button>
              <svg v-else class="dfm-chevron" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                <path d="m4 6 4 4 4-4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" />
              </svg>
              <ul v-if="listOpen" id="dfc-base-list" class="dfm-branch-list" role="listbox"
                :aria-label="t('gui.designer.file_menu.base_branch')" data-test="designer-flow-create-base-list"
                @mousedown.prevent>
                <li v-for="(branch, index) in localBranches" :id="`dfc-base-opt-${index}`" :key="branch" role="option"
                  class="dfm-branch-option" :class="{ 'is-selected': branch === workBaseRef, 'is-active': index === activeIndex }"
                  :aria-selected="branch === workBaseRef ? 'true' : 'false'" :data-value="branch"
                  data-test="designer-flow-create-base-option" @click="pickBranch(branch)" @mousemove="activeIndex = index">
                  <span class="dfm-branch-name">{{ branch }}</span>
                </li>
                <li v-if="remoteBranches.length" role="presentation" class="dfm-branch-group"
                  data-test="designer-flow-create-base-remote-group">{{ t('gui.designer.file_menu.remote_group') }}</li>
                <li v-for="(branch, offset) in remoteBranches" :id="`dfc-base-opt-${localBranches.length + offset}`" :key="`remote:${branch}`"
                  role="option" class="dfm-branch-option is-remote"
                  :class="{ 'is-selected': branch === workBaseRef, 'is-active': localBranches.length + offset === activeIndex }"
                  :aria-selected="branch === workBaseRef ? 'true' : 'false'" :data-value="branch"
                  data-test="designer-flow-create-base-option" @click="pickBranch(branch)"
                  @mousemove="activeIndex = localBranches.length + offset">
                  <span class="dfm-branch-name">origin/{{ branch }}</span>
                  <span class="dfm-remote-badge">{{ t('gui.designer.file_menu.remote_needed') }}</span>
                </li>
              </ul>
            </div>
            <span class="dfm-import-wrap" :class="{ 'is-denied': importDenied }">
              <button type="button" class="dfm-import" :class="{ 'is-on': importReady, 'is-busy': importing }"
                :disabled="!importReady" :aria-describedby="importDenied ? 'dfc-import-tip' : undefined"
                data-test="designer-flow-import-branch" @click="importBranch">
                <span v-if="importing" class="dfm-import-spinner" aria-hidden="true" />
                {{ t(importing ? 'gui.designer.file_menu.importing' : 'gui.designer.file_menu.import') }}
              </button>
              <span v-if="importDenied" id="dfc-import-tip" class="dfm-import-tip" role="tooltip"
                data-test="designer-flow-import-tip">{{ t('gui.designer.file_menu.import_permission') }}</span>
            </span>
          </div>
          <p v-show="baseHint" id="dfc-base-hint" class="dfm-hint" :class="`is-${baseHintTone}`" role="status" aria-live="polite"
            :data-test="baseState === 'error' ? 'designer-flow-create-base-error' : 'designer-flow-create-base-hint'">{{ baseHint }}</p>
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
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useCurrentUser } from '@/core/auth/currentUser'
import { useI18n } from 'vue-i18n'
import Alert from '../../../components/ui/Alert.vue'
import Button from '../../../components/ui/Button.vue'
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
const formElement = ref<HTMLFormElement | null>(null)

const nodes = ref<GroupTreeNode[]>([])
const loading = ref(false)
const groupId = ref('')
const groupPicked = ref(false)
function pickGroup(value: string) {
  groupId.value = value
  groupPicked.value = true
}
const title = ref('')
const submitting = ref(false)
// the server's own words raw, otherwise the localized fallback key —
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
const remoteNames = ref<string[]>([])
const canImport = ref(false)
const importing = ref(false)
const importError = ref('')
/** The branch this dialog just imported; "가져왔습니다." stays until another branch is picked. */
const importedName = ref('')
const remoteSelected = computed(() => remoteNames.value.includes(workBaseRef.value))
const importReady = computed(() => baseState.value === 'ready-enabled' && remoteSelected.value && canImport.value
  && !importing.value && !submitting.value)
const importDenied = computed(() => baseState.value === 'ready-enabled' && remoteSelected.value && !canImport.value)
const localBranches = computed(() => branchOptions.value.filter(option => !option.muted).map(option => option.value))
const remoteBranches = computed(() => branchOptions.value.filter(option => option.muted).map(option => option.value))
const flatBranches = computed(() => [...localBranches.value, ...remoteBranches.value])

/** Each failure gets one localized line under the field; server words never reach the screen. */
function importFailureText(error: unknown, name: string): string {
  const { status, code } = (error ?? {}) as { status?: number | null; code?: string | null }
  if (code === 'branch_name_exists') return t('gui.designer.file_menu.import_exists', { name })
  if (status === 403) return t('gui.designer.file_menu.import_permission')
  return t('gui.designer.file_menu.import_failed')
}

async function importBranch() {
  const pid = props.projectId, name = workBaseRef.value, session = generation
  if (!pid || !importReady.value) return
  closeList()
  importing.value = true
  importError.value = ''
  importedName.value = ''
  try {
    await projectStore.importRemoteBranch(pid, name)
    if (session !== generation) return
    await loadOptions(true)
    if (session !== generation) return
    if (localBranches.value.includes(name)) {
      workBaseRef.value = name
      importedName.value = name
    }
  } catch (error) {
    if (session === generation) importError.value = importFailureText(error, name)
  } finally { if (session === generation) importing.value = false }
}

watch(workBaseRef, next => {
  importError.value = ''
  if (next !== importedName.value) importedName.value = ''
})

const baseBusy = computed(() => baseState.value === 'unknown' || baseState.value === 'loading')
const baseTriggerDisabled = computed(() => baseState.value !== 'ready-enabled' || submitting.value || importing.value)
const basePlaceholder = computed(() => t(baseBusy.value
  ? 'gui.designer.file_menu.loading'
  : baseState.value === 'error' ? 'gui.designer.file_menu.base_branch_unavailable' : 'gui.designer.file_menu.base_branch_required'))
const baseDisplay = computed<{ text: string; tone: 'placeholder' | 'dim' | 'value' }>(() => {
  const name = baseState.value === 'ready-enabled' ? workBaseRef.value : ''
  if (!name) return { text: basePlaceholder.value, tone: 'placeholder' }
  if (remoteSelected.value) {
    return { text: importing.value ? `origin/${name}` : `origin/${name} · ${t('gui.designer.file_menu.remote_needed')}`, tone: 'dim' }
  }
  return { text: name, tone: 'value' }
})
// Branch lookup failures stay concise and localized; transport exceptions are never UI copy.
const baseHintState = computed<{ text: string; tone: 'error' | 'info' | 'ok' }>(() => {
  if (baseState.value === 'error') return { text: t('gui.designer.file_menu.base_branch_failed'), tone: 'error' }
  if (baseState.value !== 'ready-enabled') return { text: '', tone: 'info' }
  if (importError.value) return { text: importError.value, tone: 'error' }
  if (importing.value) return { text: '', tone: 'info' }
  if (remoteSelected.value) return { text: t('gui.designer.file_menu.remote_selected'), tone: 'info' }
  if (importedName.value && importedName.value === workBaseRef.value) return { text: t('gui.designer.file_menu.imported'), tone: 'ok' }
  if (!branchValid.value) return { text: t('gui.designer.file_menu.base_branch_required'), tone: 'error' }
  return { text: '', tone: 'info' }
})
const baseHint = computed(() => baseHintState.value.text)
const baseHintTone = computed(() => baseHintState.value.tone)
const branchValid = computed(() => !remoteSelected.value && workBaseRef.value !== '' && branchOptions.value.some((o) => o.value === workBaseRef.value))
const baseReady = computed(() => baseState.value === 'ready-disabled' || (baseState.value === 'ready-enabled' && branchValid.value))

const baseTrigger = ref<HTMLButtonElement | null>(null)
const listOpen = ref(false)
const activeIndex = ref(-1)
function openList() {
  if (baseTriggerDisabled.value || !flatBranches.value.length) return
  listOpen.value = true
  activeIndex.value = Math.max(0, flatBranches.value.indexOf(workBaseRef.value))
}
function closeList() {
  listOpen.value = false
  activeIndex.value = -1
}
function toggleList() {
  if (listOpen.value) closeList()
  else openList()
}
function pickBranch(name: string) {
  workBaseRef.value = name
  closeList()
  baseTrigger.value?.focus()
}
function onTriggerKeydown(event: KeyboardEvent) {
  if (!listOpen.value) {
    if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(event.key)) {
      event.preventDefault()
      openList()
    }
    return
  }
  const last = flatBranches.value.length - 1
  if (event.key === 'ArrowDown') activeIndex.value = Math.min(last, activeIndex.value + 1)
  else if (event.key === 'ArrowUp') activeIndex.value = Math.max(0, activeIndex.value - 1)
  else if (event.key === 'Home') activeIndex.value = 0
  else if (event.key === 'End') activeIndex.value = last
  else if (event.key === 'Enter' || event.key === ' ') {
    const name = flatBranches.value[activeIndex.value]
    if (name !== undefined) pickBranch(name)
  } else if (event.key === 'Escape') {
    // Close only the list; the dialog's own Escape must not fire on the same press.
    event.stopPropagation()
    closeList()
  } else {
    if (event.key === 'Tab') closeList()
    return
  }
  event.preventDefault()
}
watch(baseTriggerDisabled, isDisabled => { if (isDisabled) closeList() })
let selectedOnce = false
let optionsGeneration = 0

async function loadOptions(retry = false) {
  const pid = props.projectId
  if (!props.open || !pid || baseState.value === 'loading' || submitting.value) return
  const session = generation
  const request = ++optionsGeneration
  if (retry) projectStore.invalidateWorkBaseOptions(pid)
  baseState.value = 'loading'
  try {
    const options = await projectStore.loadWorkBaseOptions(pid, { force: true })
    if (session !== generation || request !== optionsGeneration) return
    remoteNames.value = options.branches.filter(branch => branch.remoteOnly).map(branch => branch.name)
    canImport.value = options.canImport === true
    branchOptions.value = options.branches.map((branch) => ({ value: branch.name, label: branch.name, muted: branch.remoteOnly === true }))
    if (selectedOnce) {
      if (!branchOptions.value.some(option => option.value === workBaseRef.value)) workBaseRef.value = ''
    } else {
      workBaseRef.value = options.baseBranch && branchOptions.value.some((b) => b.value === options.baseBranch) ? options.baseBranch : ''
    }
    selectedOnce = true
    baseState.value = options.gitEnabled ? 'ready-enabled' : 'ready-disabled'
  } catch {
    if (session !== generation || request !== optionsGeneration) return
    baseState.value = 'error'
  }
}

async function retryOptions(event: MouseEvent) {
  const restoreFocus = document.activeElement === event.currentTarget
  const session = generation
  await loadOptions(true)
  await nextTick()
  // The focused retry disappears while loading. Restore its logical position only
  // if the user has not moved elsewhere, and never into a newer/closed session.
  if (!restoreFocus || !props.open || session !== generation || document.activeElement !== document.body) return
  const target = formElement.value?.querySelector<HTMLElement>(
    baseState.value === 'error' ? '.dfm-retry' : baseState.value === 'ready-disabled' ? '#dfc-title' : '#dfc-base',
  )
  if (target && !target.hasAttribute('disabled')) target.focus()
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
    // Only default to the first Group without a calling Group or an explicit user choice.
    if (!groups.value.some((group) => group.id === groupId.value)) {
      groupId.value = props.groupId == null && !groupPicked.value ? (groups.value[0]?.id ?? '') : ''
    }
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
    importing.value = false
    importError.value = ''
    importedName.value = ''
    closeList()
    remoteNames.value = []
    canImport.value = false
    if (!isOpen) return
    const opening = !previous?.[0]
    const projectChanged = previous && previous[1] !== pid
    nodes.value = []
    loading.value = false
    if (opening || projectChanged) {
      groupId.value = props.groupId ?? ''
      groupPicked.value = false
    }
    if (opening) {
      title.value = ''
      workBaseRef.value = ''
      selectedOnce = false
    }
    branchOptions.value = []
    submitError.value = null
    attemptedSubmit.value = false
    if (pid) {
      void load()
      void loadOptions()
    }
  },
  { immediate: true, flush: 'sync', deep: true },
)

// Shell assigns open before groupId, so observe the calling Group separately.
// T / the earlier implementation: keep sync timing without restarting loads.
watch(() => props.groupId, next => {
  if (!props.open || groupPicked.value) return
  groupId.value = next ?? ''
  if (!loading.value && nodes.value.length && !groups.value.some(group => group.id === groupId.value)) {
    groupId.value = ''
  }
}, { flush: 'sync' })

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

<style>
/* Explicit surface class keeps the shared shell's layout and focus behavior unchanged elsewhere. */
.dfm-create-dialog.ui-modal-surface {
  max-width: 480px;
  background: linear-gradient(150deg, #132238 0%, #101d30 53%, #101b2c 100%);
  border: 1px solid rgba(143, 169, 203, .21);
  border-radius: 15px;
  box-shadow: 0 28px 80px rgba(0, 0, 0, .32), 0 4px 16px rgba(0, 0, 0, .2), inset 0 1px rgba(255, 255, 255, .035);
  color: var(--fg2-text, #ecf3fb);
  /* The base-branch list drops below the field and may reach past the footer. */
  overflow: visible;
}
.dfm-create-dialog > .ui-modal-body {
  padding: 0 29px;
  overflow: visible;
}
@media (max-width: 500px) {
  .dfm-create-dialog > .ui-modal-body { padding: 0 20px; }
}
</style>

<style scoped>
.dfm-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 27px 29px 25px;
}
.dfm-title {
  margin: 0;
  font-size: 17px;
  line-height: 24px;
  font-weight: 670;
  letter-spacing: -.5px;
}
.dfm-close, .dfm-retry {
  display: inline-grid;
  place-items: center;
  flex: none;
  padding: 0;
  border: 0;
  background: transparent;
  color: #8fa4bb;
  cursor: pointer;
}
.dfm-close {
  width: 30px;
  height: 30px;
  margin: -3px -7px -3px 0;
  border-radius: 8px;
}
.dfm-close:hover, .dfm-retry:hover:not(:disabled) { background: rgba(255, 255, 255, .07); color: #e8f2ff; }
.dfm-close:focus-visible, .dfm-retry:focus-visible {
  outline: 2px solid var(--fg2-focus, #77aaff);
  outline-offset: 2px;
}
.dfm-alert { margin: 0 0 16px; }
.dfm-form { display: grid; gap: 20px; }
.dfm-field { min-width: 0; }
.dfm-label {
  display: block;
  margin: 0 0 8px;
  font-size: 12px;
  line-height: 16px;
  font-weight: 570;
  color: #a6b9cd;
}
.dfm-control {
  position: relative;
  display: flex;
  align-items: center;
  height: 43px;
  box-sizing: border-box;
  border: 1px solid #2b4058;
  border-radius: 9px;
  background: rgba(7, 17, 31, .54);
  transition: border-color .16s, background .16s, box-shadow .16s;
}
.dfm-control:hover:not(.is-disabled) { background: rgba(12, 28, 47, .85); border-color: #38526d; }
.dfm-control.has-error { border-color: rgba(220, 146, 141, .42); }
.dfm-control:focus-within { border-color: #639af4; box-shadow: 0 0 0 3px rgba(72, 139, 242, .10); }
.dfm-control :deep(.ui-input), .dfm-control :deep(.ui-select) {
  display: block;
  width: 100%;
  height: 100%;
  min-width: 0;
  box-sizing: border-box;
  padding: 0 13px;
  background: transparent;
  border: 0;
  border-radius: inherit;
  color: var(--fg2-text, #ecf3fb);
  font-family: inherit;
  font-size: 13px;
  font-weight: 500;
  outline: none;
}
.dfm-control :deep(.ui-select) { padding-right: 42px; cursor: pointer; }
.dfm-control :deep(.ui-select:disabled) { color: #7f96ae; cursor: default; opacity: 1; }
.dfm-control :deep(.ui-select option) { background: #15253a; color: #ecf3fb; }
.dfm-branch-row { display: flex; align-items: center; gap: 8px; }
.dfm-branch-row .dfm-branch-control { flex: 1; min-width: 0; }
.dfm-base-trigger {
  display: flex;
  align-items: center;
  width: 100%;
  height: 100%;
  min-width: 0;
  box-sizing: border-box;
  padding: 0 42px 0 36px;
  background: transparent;
  border: 0;
  border-radius: inherit;
  color: var(--fg2-text, #ecf3fb);
  font-family: inherit;
  font-size: 13px;
  font-weight: 500;
  text-align: left;
  cursor: pointer;
  outline: none;
}
.dfm-base-trigger:disabled { cursor: default; }
.dfm-base-text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dfm-base-text.is-placeholder { color: #7f96ae; }
.dfm-base-text.is-dim { color: #6e8299; }
.dfm-branch-list {
  position: absolute;
  top: calc(100% + 4px);
  left: -1px;
  right: -1px;
  z-index: 5;
  max-height: 248px;
  margin: 0;
  padding: 0;
  overflow-y: auto;
  list-style: none;
  border: 1px solid #2f4864;
  border-radius: 9px;
  background: #13233a;
  box-shadow: 0 12px 30px rgba(0, 0, 0, .7);
  font-size: 13px;
}
.dfm-branch-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 9px 13px;
  color: #ecf3fb;
  cursor: pointer;
}
.dfm-branch-option.is-active { background: #1a3048; }
.dfm-branch-option.is-selected { background: #1d3a5c; }
.dfm-branch-option.is-remote { color: #6e8299; }
.dfm-branch-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dfm-remote-badge {
  flex: none;
  padding: 2px 6px;
  border: 1px solid #2b3d52;
  border-radius: 4px;
  background: #1b2838;
  color: #8a9db3;
  font-size: 10.5px;
  line-height: 14px;
}
.dfm-branch-group {
  padding: 6px 13px 4px;
  border-top: 1px solid #22344a;
  color: #7a8ea6;
  font-size: 10.5px;
  font-weight: 700;
}
.dfm-import-wrap { position: relative; flex: none; display: inline-flex; }
.dfm-import {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  height: 43px;
  padding: 0 14px;
  box-sizing: border-box;
  border: 1px solid #36506b;
  border-radius: 9px;
  background: transparent;
  color: #c2d0df;
  font-family: inherit;
  font-size: 12.5px;
  font-weight: 650;
  white-space: nowrap;
  cursor: pointer;
}
.dfm-import:disabled { opacity: .42; cursor: not-allowed; }
.dfm-import.is-on { background: linear-gradient(#508ff0, #3373d7); border-color: #548fe9; color: #fff; }
.dfm-import.is-on:hover { background: linear-gradient(#69a0f7, #4783e6); }
.dfm-import.is-busy, .dfm-import.is-busy:disabled { opacity: 1; background: #1b3a5a; border-color: #3a6a96; color: #d4ecff; cursor: default; }
.dfm-import:focus-visible { outline: 2px solid var(--fg2-focus, #77aaff); outline-offset: 2px; }
.dfm-import-spinner {
  width: 13px;
  height: 13px;
  box-sizing: border-box;
  border: 1.8px solid rgba(160, 185, 220, .25);
  border-top-color: #8bb5ef;
  border-radius: 50%;
  animation: dfm-spin .8s linear infinite;
}
.dfm-import-tip {
  position: absolute;
  top: calc(100% + 5px);
  right: 0;
  z-index: 6;
  padding: 6px 9px;
  border: 1px solid #3a4d63;
  border-radius: 6px;
  background: #1d2b3c;
  color: #dbe6f2;
  font-size: 11.5px;
  white-space: nowrap;
  visibility: hidden;
  opacity: 0;
  transition: opacity .12s;
  pointer-events: none;
}
.dfm-import-wrap.is-denied:hover .dfm-import-tip,
.dfm-import-wrap.is-denied:focus-within .dfm-import-tip { visibility: visible; opacity: 1; }
.dfm-hint { margin: 6px 0 0; font-size: 11.5px; line-height: 16px; color: #9fb2c6; }
.dfm-hint.is-error { color: #e0a6a1; }
.dfm-hint.is-ok { color: #7fe3d6; }
.dfm-chevron {
  position: absolute;
  right: 13px;
  top: 50%;
  transform: translateY(-50%);
  width: 14px;
  height: 14px;
  color: #92aac4;
  pointer-events: none;
}
.dfm-branch-icon { position: absolute; left: 13px; width: 15px; height: 15px; color: #7b9ac1; pointer-events: none; }
.dfm-spinner {
  position: absolute;
  right: 14px;
  width: 14px;
  height: 14px;
  box-sizing: border-box;
  border: 1.8px solid rgba(160, 185, 220, .2);
  border-top-color: #8bb5ef;
  border-radius: 50%;
  animation: dfm-spin .8s linear infinite;
}
@keyframes dfm-spin { to { transform: rotate(360deg); } }
.dfm-retry { position: absolute; right: 6px; width: 31px; height: 31px; border-radius: 7px; color: #a7bcd3; }
.dfm-retry svg { width: 16px; height: 16px; }
.dfm-retry:disabled { cursor: default; opacity: .43; }
.dfm-field-error { margin: 6px 0 0; color: #e0a6a1; font-size: 11.5px; line-height: 16px; }
.dfm-actions { display: flex; justify-content: flex-end; gap: 9px; padding: 28px 29px 24px; }
.dfm-actions :deep(.ui-btn) { height: 37px; padding: 0 17px; border-radius: 8px; font-size: 12.5px; font-weight: 620; }
.dfm-actions :deep(.ui-btn--secondary) { background: transparent; border-color: #36506b; color: #c2d0df; }
.dfm-actions :deep(.ui-btn--secondary:hover) { background: rgba(255, 255, 255, .05); border-color: #506985; }
.dfm-actions :deep(.ui-btn--primary) { min-width: 74px; background: linear-gradient(#508ff0, #3373d7); border-color: #548fe9; color: #fff; box-shadow: inset 0 1px rgba(255, 255, 255, .15); }
.dfm-actions :deep(.ui-btn--primary:hover:not(:disabled)) { background: linear-gradient(#69a0f7, #4783e6); filter: none; }
.dfm-actions :deep(.ui-btn--primary:disabled) { opacity: .43; box-shadow: none; }
@media (max-width: 500px) {
  .dfm-head { padding: 23px 20px 21px; }
  .dfm-form { gap: 17px; }
  .dfm-actions { padding: 28px 20px 21px; }
}
@media (prefers-reduced-motion: reduce) {
  .dfm-spinner, .dfm-import-spinner { animation: none; }
  .dfm-control, .dfm-close, .dfm-retry, .dfm-actions :deep(.ui-btn) { transition: none; }
}
</style>
