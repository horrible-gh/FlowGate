<template>
  <!-- flowgate.v02.0043 — File › Group 열기… / Flow 열기…: the current Project's Groups, or its
       Flows under their Groups, from the same group tree the Project Explorer shows (the store's
       cached read). Choosing a row emits it; the Shell does the opening (Explorer selection,
       Center tab). No text beyond the title, the search field and the rows. -->
  <ModalShell :open="open" size="md" close-on-backdrop @close="emit('close')">
    <template #header="{ titleId }">
      <div class="dfm-head">
        <h2 :id="titleId" class="dfm-title">{{ title }}</h2>
        <IconButton :label="t('gui.designer.file_menu.close')" variant="ghost" @click="emit('close')">✕</IconButton>
      </div>
    </template>

    <template #default>
      <div class="dod-body" data-test="designer-open-dialog" :data-kind="kind">
        <Input
          v-model="query"
          type="search"
          :placeholder="t('gui.designer.file_menu.search_placeholder')"
          :aria-label="t('gui.designer.file_menu.search_placeholder')"
          autocomplete="off"
          data-modal-autofocus
          data-test="designer-open-search"
        />

        <LoadingState v-if="loading" size="sm" :label="t('gui.designer.file_menu.loading')" data-test="designer-open-loading" />
        <ErrorState
          v-else-if="error !== null"
          compact
          :title="t('gui.designer.file_menu.load_failed')"
          :message="error"
          data-test="designer-open-error"
        >
          <template #action>
            <Button size="sm" data-test="designer-open-retry" @click="load(true)">{{ t('gui.designer.file_menu.retry') }}</Button>
          </template>
        </ErrorState>

        <ul v-else class="dod-list" role="listbox" :aria-label="title" data-test="designer-open-list">
          <template v-if="kind === 'group'">
            <li v-for="group in groups" :key="group.id" role="none">
              <button
                type="button"
                role="option"
                class="dod-item"
                data-test="designer-open-item"
                :data-id="group.id"
                @click="emit('open-group', group)"
              >
                <span class="dod-item-name">{{ group.name }}</span>
                <span class="dod-item-count" aria-hidden="true">{{ group.flows.length }}</span>
              </button>
            </li>
            <li v-if="groups.length === 0" class="dod-empty" role="none" data-test="designer-open-empty">
              {{ t('gui.designer.project_explorer.no_groups') }}
            </li>
          </template>
          <template v-else>
            <template v-for="group in groups" :key="group.id">
              <li class="dod-group" role="presentation" data-test="designer-open-group" :data-id="group.id">{{ group.name }}</li>
              <li v-for="flow in group.flows" :key="flow.id" role="none">
                <button
                  type="button"
                  role="option"
                  class="dod-item is-flow"
                  data-test="designer-open-item"
                  :data-id="flow.id"
                  @click="emit('open-flow', flow)"
                >
                  <span class="dod-item-name">{{ flow.name }}</span>
                </button>
              </li>
            </template>
            <li v-if="groups.length === 0" class="dod-empty" role="none" data-test="designer-open-empty">
              {{ t('gui.designer.project_explorer.no_flows') }}
            </li>
          </template>
        </ul>
      </div>
    </template>
  </ModalShell>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '../../../components/ui/Button.vue'
import ErrorState from '../../../components/ui/ErrorState.vue'
import IconButton from '../../../components/ui/IconButton.vue'
import Input from '../../../components/ui/Input.vue'
import LoadingState from '../../../components/ui/LoadingState.vue'
import ModalShell from '../../../components/ui/ModalShell.vue'
import { useProjectStore, type GroupTreeNode } from '@/features/project'
import {
  buildExplorerProject,
  filterExplorerProject,
  normalizeExplorerQuery,
  projectNodeId,
  type ExplorerFlow,
  type ExplorerGroup,
} from '../projectExplorer/projectExplorerTree'

const props = defineProps<{
  open: boolean
  kind: 'group' | 'flow'
  projectId: string | null
}>()

const emit = defineEmits<{
  close: []
  'open-group': [group: ExplorerGroup]
  'open-flow': [flow: ExplorerFlow]
}>()

const { t } = useI18n()
const projectStore = useProjectStore()

const title = computed(() =>
  t(props.kind === 'group' ? 'gui.designer.file_menu.group_open_title' : 'gui.designer.file_menu.flow_open_title'),
)

const nodes = ref<GroupTreeNode[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
const query = ref('')

// A read that lands after the dialog closed or reopened is dropped.
let readSeq = 0

async function load(force = false) {
  const pid = props.projectId
  if (!pid) return
  const seq = ++readSeq
  loading.value = true
  error.value = null
  try {
    const next = await projectStore.fetchGroupTree(pid, { force })
    if (seq !== readSeq) return
    nodes.value = next
  } catch (err) {
    if (seq !== readSeq) return
    error.value = err instanceof Error && err.message ? err.message : t('gui.designer.file_menu.load_failed')
  } finally {
    if (seq === readSeq) loading.value = false
  }
}

watch(
  () => props.open,
  (isOpen) => {
    if (!isOpen) {
      readSeq += 1
      return
    }
    query.value = ''
    nodes.value = []
    error.value = null
    void load(false)
  },
  { immediate: true },
)

const project = computed(() => (props.projectId ? buildExplorerProject(nodes.value, projectNodeId(props.projectId)) : null))

// Group 열기 filters the Groups by name; Flow 열기 keeps a Flow's Group around it (the Explorer's
// own search rule) and shows only Groups that have a Flow to choose.
const groups = computed<ExplorerGroup[]>(() => {
  if (!project.value) return []
  if (props.kind === 'group') {
    const q = normalizeExplorerQuery(query.value)
    return q ? project.value.groups.filter((group) => group.name.toLocaleLowerCase().includes(q)) : project.value.groups
  }
  const filtered = filterExplorerProject(project.value, query.value).project
  return filtered ? filtered.groups.filter((group) => group.flows.length > 0) : []
})
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

.dod-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px 0 8px;
}

.dod-list {
  margin: 0;
  padding: 0;
  max-height: 50vh;
  overflow: auto;
  list-style: none;
}

.dod-group {
  padding: 8px 8px 4px;
  font-size: 11px;
  font-weight: 650;
  letter-spacing: 0.04em;
  color: var(--fg2-muted);
}

.dod-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  min-height: 32px;
  padding: 0 10px;
  border: 0;
  border-radius: var(--fg2-control-radius-sm);
  background: transparent;
  color: var(--fg2-control-fg);
  font: inherit;
  font-size: 13px;
  text-align: left;
  cursor: pointer;
}

.dod-item.is-flow {
  padding-left: 22px;
}

.dod-item:hover,
.dod-item:focus-visible {
  outline: 0;
  background: var(--fg2-line);
  color: var(--fg2-text);
}

.dod-item-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dod-item-count {
  flex: none;
  font-size: 11px;
  color: var(--fg2-muted-2);
}

.dod-empty {
  padding: 8px 10px;
  font-size: 12px;
  font-style: italic;
  color: var(--fg2-muted-2);
}
</style>
