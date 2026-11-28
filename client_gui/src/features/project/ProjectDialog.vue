<template>
  <!-- The Designer taskbar's [Open project] opens the same list as a floating window: the canvas and side panels stay
       usable, and minimizing puts it on the taskbar. Everywhere else it stays the modal dialog. -->
  <ModelessShell
    v-if="asWindow"
    ref="windowRef"
    kind="project"
    kind-color="#6478dc"
    :open="store.dialogOpen"
    :minimized="windowMinimized"
    :title="t('gui.project.dialog.title')"
    :initial-x="windowGeometry.x"
    :initial-y="windowGeometry.y"
    :initial-width="windowGeometry.width"
    :initial-height="windowGeometry.height"
    :min-width="320"
    :min-height="260"
    @update:minimized="windowMinimized = $event"
    @close="store.closeDialog()"
  >
    <div ref="windowBodyRef" class="pd-window" data-test="project-window">
      <p class="pd-head-sub pd-window-sub">{{ t('gui.project.dialog.subtitle') }}</p>
      <div v-if="createKnown" class="pd-intro">
        <Button type="button" variant="primary" size="sm" :disabled="!canCreateProject" :title="createReason || undefined" data-test="project-new" @click="createDialogOpen = true">
          {{ t('gui.project.dialog.new_project') }}
        </Button>
      </div>
      <div class="pd-search">
        <Input
          ref="searchInputRef"
          v-model="searchQuery"
          type="search"
          class="pd-search-input"
          :placeholder="t('gui.project.dialog.search_placeholder')"
          :aria-label="t('gui.project.dialog.search_aria')"
          autocomplete="off"
        />
      </div>
      <div class="pd-window-list">
        <ProjectList
          :projects="filteredProjects"
          :loading="store.loading"
          :error="store.error"
          :has-any-projects="store.projects.length > 0"
          :selected-id="selectedId"
          @retry="store.fetchProjects()"
          @select="selectProject"
          @open="openProject"
        />
      </div>
      <div class="pd-foot pd-window-foot">
        <span><b>Enter</b> {{ t('gui.project.dialog.hint_open') }}</span>
        <span><b>Esc</b> {{ t('gui.common.close') }}</span>
      </div>
    </div>
  </ModelessShell>

  <ModalShell v-else :open="store.dialogOpen" size="lg" @close="store.closeDialog()">
    <template #header="{ titleId }">
      <div class="pd-head">
        <div class="pd-head-titles">
          <h2 :id="titleId" class="pd-head-title">{{ t('gui.project.dialog.title') }}</h2>
          <p class="pd-head-sub">{{ t('gui.project.dialog.subtitle') }}</p>
        </div>
        <IconButton :label="t('gui.common.close')" variant="ghost" @click="store.closeDialog()">✕</IconButton>
      </div>
    </template>

    <template #default>
      <div class="pd-body">
        <div v-if="createKnown" class="pd-intro">
          <Button type="button" variant="primary" size="sm" :disabled="!canCreateProject" :title="createReason || undefined" data-test="project-new" @click="createDialogOpen = true">
            {{ t('gui.project.dialog.new_project') }}
          </Button>
        </div>

        <div class="pd-search">
          <Input
            ref="searchInputRef"
            v-model="searchQuery"
            type="search"
            class="pd-search-input"
            :placeholder="t('gui.project.dialog.search_placeholder')"
            :aria-label="t('gui.project.dialog.search_aria')"
            autocomplete="off"
            data-modal-autofocus
          />
        </div>

        <ProjectList
          :projects="filteredProjects"
          :loading="store.loading"
          :error="store.error"
          :has-any-projects="store.projects.length > 0"
          :selected-id="selectedId"
          @retry="store.fetchProjects()"
          @select="selectProject"
          @open="openProject"
        />
      </div>
    </template>

    <template #footer>
      <div class="pd-foot">
        <span><b>Enter</b> {{ t('gui.project.dialog.hint_open') }}</span>
        <span><b>Esc</b> {{ t('gui.common.close') }}</span>
      </div>
    </template>
  </ModalShell>

  <ProjectCreateDialog :open="createDialogOpen" @close="createDialogOpen = false" @created="handleCreated" />

  <ToastHost :toasts="shownToasts" :dismiss-label="t('gui.common.close')" @dismiss="dismissToast" />
</template>

<script setup lang="ts">
// Project Dialog -- Project Feature's top-level Modal. App
// never passes an `open` prop here: the store owns Dialog open/close truth already
// , so opening this Dialog from anywhere (Sidebar/Dashboard in
// ) means calling the public `useProjectStore().openDialog()`, and this component
// only ever reads `store.dialogOpen` reactively.
//
// Dialog-local state lives here, not in the store: `searchQuery` and `selectedId` (the
// candidate a row-click picks, distinct from `store.currentProject` -- the design,
// T#1 §5 doc-comment). Only an explicit "열기"/double-click ever calls
// `store.setCurrentProject()`; a plain row click only ever updates `selectedId`.
//
// nothing is pre-selected or marked current when the Dialog opens —
// `selectedId` stays null until the person picks a row. "열기" also raises the store's
// one-shot explicit-open signal and emits `opened`; this feature stays router-free, so the
// App Shell (the single mount, reached from both Sidebar and Dashboard) does the move to the
// Designer.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch, watchEffect } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '../../components/ui/Button.vue'
import IconButton from '../../components/ui/IconButton.vue'
import Input from '../../components/ui/Input.vue'
import ModalShell from '../../components/ui/ModalShell.vue'
import ModelessShell from '../../components/ui/ModelessShell.vue'
import ToastHost, { type ToastHostItem } from '../../components/ui/ToastHost.vue'
import { denialLines } from '@shared/permissions/denial'
import { ensureWorkspaceCapabilityList, newProjectDecision } from '@shared/permissions/capabilities'
import ProjectCreateDialog from './ProjectCreateDialog.vue'
import ProjectList from './ProjectList.vue'
import { useProjectStore } from './store'
import type { Project } from './types'
import { projectWindowUi } from './windowState'

const emit = defineEmits<{ opened: [project: Project] }>()

const store = useProjectStore()
const { t } = useI18n()
// "+ 새 프로젝트": the server decides (the default workspace's `workspace.project.create`, the workspace a new
// project goes to); the screen shows the button once that answer is in, and turns it off with the reason when
// the answer is no. No `is_admin` here (flowgate.v02test.0090).
const createDecision = computed(() => newProjectDecision())
const createKnown = computed(() => createDecision.value.reason !== 'unknown')
const canCreateProject = computed(() => createDecision.value.allowed)
const createReason = computed(() => denialLines(
  [{ action: 'workspace.project.create', decision: createDecision.value }],
  null,
  (key, params) => t(key, params ?? {}),
  'workspace',
).join(' '))
onMounted(() => { void ensureWorkspaceCapabilityList() })

const searchQuery = ref('')
const selectedId = ref<string | null>(null)
const createDialogOpen = ref(false)
const searchInputRef = ref<InstanceType<typeof Input> | null>(null)

// Window form (the Designer taskbar's [Open project]): which form is showing, whether it sits on the taskbar, and the
// place it first opens at -- in the middle, clear of the header and the taskbar.
const asWindow = computed(() => store.dialogAsWindow)
const windowMinimized = ref(false)
const windowRef = ref<InstanceType<typeof ModelessShell> | null>(null)
const windowBodyRef = ref<HTMLElement | null>(null)
function placeWindow() {
  const width = Math.min(560, Math.max(320, window.innerWidth - 48))
  const height = Math.min(560, Math.max(260, window.innerHeight - 160))
  return { x: Math.max(16, Math.round((window.innerWidth - width) / 2)), y: 70, width, height }
}
const windowGeometry = ref(placeWindow())
function focusSearch(): void {
  ;(searchInputRef.value?.$el as HTMLInputElement | undefined)?.focus()
}
// The taskbar lights [Open project] while the window is open (also while it is minimized).
watchEffect(() => { projectWindowUi.open = store.dialogOpen && asWindow.value })
// Pressing [Open project] again brings the open window back where it was instead of opening a second one.
watch(
  () => store.dialogRequest,
  async () => {
    if (!asWindow.value || !store.dialogOpen) return
    windowMinimized.value = false
    await nextTick()
    windowRef.value?.bringToFront()
    focusSearch()
  },
)
// a toast keeps the created Project's name, not a sentence translated at
// creation time — the sentence is built in the current locale whenever it renders, and the
// Project name itself is only interpolated, never translated.
interface CreatedToast {
  id: string
  projectName: string
}
const toasts = ref<CreatedToast[]>([])
const shownToasts = computed<ToastHostItem[]>(() =>
  toasts.value.map((item) => ({
    id: item.id,
    message: t('gui.project.dialog.created_toast', { name: item.projectName }),
    tone: 'success',
  })),
)

const filteredProjects = computed<Project[]>(() => {
  const q = searchQuery.value.trim().toLowerCase()
  if (!q) return store.projects
  return store.projects.filter(
    (p) => p.name.toLowerCase().includes(q) || p.id.toLowerCase().includes(q) || p.description.toLowerCase().includes(q),
  )
})

// Every time this Dialog opens, Dialog-local state starts clean and search results are
// unfiltered again -- closing it never leaves stale search text or a
// stale selection behind for the next open. Initial focus itself is ModalShell's own
// concern via `data-modal-autofocus` on the search input above, not duplicated here.
//
// Closing also cascades to the create child Modal (child lifecycle): this parent
// can close via more than an explicit "취소"/X on the child itself -- Escape, a backdrop
// click, or a programmatic store.closeDialog() (e.g. from the create-success flow's own
// caller, or a future outside trigger) can all flip `store.dialogOpen` to false while the
// child is still open or its own create request still in flight. Forcing
// `createDialogOpen` closed here too (rather than leaving the early return that used to
// skip this branch entirely) is what changes the child's own `open` prop, which is what
// bumps *its* `generation` and invalidates any create response still in flight -- without
// this, the child Modal is orphaned on top of a Dialog with nothing left rendering behind
// it, and the same stale child session would still be showing on the next open.
watch(
  () => store.dialogOpen,
  async (isOpen) => {
    if (!isOpen) {
      createDialogOpen.value = false
      windowMinimized.value = false
      return
    }
    searchQuery.value = ''
    selectedId.value = null
    if (asWindow.value) windowGeometry.value = placeWindow()
    // the Designer's File › New Project opens this Dialog asking for the create
    // child right away. The entry point follows the server's capabilities, so for anyone it
    // does not allow the request is dropped and the Dialog opens as it always does.
    const wantsCreate = store.takeCreateRequest()
    if (wantsCreate && !createKnown.value) await ensureWorkspaceCapabilityList()
    createDialogOpen.value = wantsCreate && canCreateProject.value
    // The modal focuses its search box itself (data-modal-autofocus); the window does it here.
    if (asWindow.value && !createDialogOpen.value) {
      await nextTick()
      focusSearch()
    }
  },
)

// A search that hides the current selection candidate must not keep it selected out of
// sight.
watch(filteredProjects, (list) => {
  if (selectedId.value && !list.some((p) => p.id === selectedId.value)) {
    selectedId.value = null
  }
})

function selectProject(project: Project): void {
  selectedId.value = project.id
}

// The only place this component calls into the store's current-Project mutation --
// picking a row alone (selectProject above) never does.
function openProject(project: Project): void {
  store.setCurrentProject(project)
  store.requestExplicitOpen(project.id)
  store.closeDialog()
  emit('opened', project)
}

function dismissToast(id: string | number): void {
  toasts.value = toasts.value.filter((t) => t.id !== id)
}

// "/" jumps focus to search, but only while this Dialog is actually topmost (the create
// child Modal is not open) and only while focus is not already inside a text field --
// typing a literal "/" character into the description Textarea, for example, must stay a
// literal "/".
function onDocumentKeydown(event: KeyboardEvent): void {
  if (event.key !== '/') return
  if (!store.dialogOpen || createDialogOpen.value) return
  const active = document.activeElement
  if (active instanceof HTMLInputElement || active instanceof HTMLTextAreaElement) return
  // The window shares the page with the canvas, so "/" only reaches its search box while the keyboard is in the window.
  if (asWindow.value && (windowMinimized.value || !(active instanceof Node && windowBodyRef.value?.contains(active)))) return
  event.preventDefault()
  // The shared Input primitive does not expose a `focus()` method (it never
  // defineExpose()s one) -- `$el` is always available on a component's template ref
  // regardless, and Input's single root element is the native <input> itself.
  ;(searchInputRef.value?.$el as HTMLInputElement | undefined)?.focus()
}

onMounted(() => document.addEventListener('keydown', onDocumentKeydown))
onBeforeUnmount(() => document.removeEventListener('keydown', onDocumentKeydown))

// Create success: close the child Modal, let the person see the
// new Project immediately (clearing search in case active text would hide it), make it
// the Dialog-local selection candidate, keep this parent Dialog open, and leave
// `currentProject` untouched -- only an explicit "열기" changes that.
function handleCreated(project: Project): void {
  createDialogOpen.value = false
  searchQuery.value = ''
  selectedId.value = project.id
  toasts.value = [...toasts.value, { id: `project-created-${project.id}-${Date.now()}`, projectName: project.name }]
}
</script>

<style scoped>
.pd-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 20px 24px 0;
}

.pd-head-title {
  margin: 0;
  font-size: 18px;
  font-weight: 750;
  color: var(--fg2-text);
}

.pd-head-sub {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--fg2-muted);
}

.pd-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 16px 0;
}

/* Action row only (admin "+ 새 프로젝트"); the guidance text lives in the header alone. */
.pd-intro {
  flex: none;
  display: flex;
  justify-content: flex-end;
}

.pd-search {
  flex: none;
}

.pd-foot {
  display: flex;
  gap: 16px;
  padding: 10px 24px 16px;
  color: var(--fg2-muted);
  font-size: 12px;
}

.pd-foot b {
  color: var(--fg2-control-fg);
}

/* Window form: the same parts in a column that fills the window; only the list scrolls. */
.pd-window {
  display: flex;
  flex-direction: column;
  gap: 12px;
  height: 100%;
  min-height: 0;
  box-sizing: border-box;
  padding: 12px 16px 0;
}

.pd-window-sub {
  margin: 0;
}

.pd-window-list {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
}

.pd-window-foot {
  flex: none;
  padding: 8px 0 12px;
}
</style>
