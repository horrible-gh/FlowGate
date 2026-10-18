<template>
  <!-- Assembles Project > Group > Flow from the already-derived display tree (D0002 §4).
       Expansion/selection/active state comes in as props; every row event goes straight
       back up — no Project/Group/Flow business logic lives here.
       flowgate.v02.0060 T#2: a right click on a row goes up as `context` with the row it hit (the innermost row's
       handler runs first; the Explorer decides). Nothing is selected, activated or toggled by it. -->
  <ul
    ref="rootEl"
    class="explorer-tree"
    role="tree"
    :aria-label="label"
    data-test="project-explorer-tree"
    @keydown="onKeydown"
  >
    <ProjectRow
      :project="project"
      :expanded="isExpanded(project.id)"
      :selected="isSelected('project', project.id)"
      @select="emit('select-project', $event)"
      @toggle="emit('toggle', $event)"
      @contextmenu="emit('context', $event, { kind: 'project' })"
    >
      <li
        v-if="project.groups.length === 0"
        role="none"
        class="explorer-hint explorer-hint--group"
        data-test="project-explorer-no-groups"
      >{{ t('gui.designer.project_explorer.no_groups') }}</li>
      <GroupRow
        v-for="group in project.groups"
        :key="group.id"
        :group="group"
        :expanded="isExpanded(group.id)"
        :selected="isSelected('group', group.id)"
        @select="emit('select-group', $event)"
        @toggle="emit('toggle', $event)"
        @contextmenu="emit('context', $event, { kind: 'group', group })"
      >
        <li
          v-if="group.flows.length === 0"
          role="none"
          class="explorer-hint explorer-hint--flow"
          data-test="project-explorer-no-flows"
        >{{ t('gui.designer.project_explorer.no_flows') }}</li>
        <FlowRow
          v-for="flow in group.flows"
          :key="flow.id"
          :flow="flow"
          :selected="isSelected('flow', flow.id)"
          :active="flow.id === activeFlowId"
          @select="emit('select-flow', $event)"
          @activate="emit('activate-flow', $event)"
          @contextmenu="emit('context', $event, { kind: 'flow', flow })"
        />
      </GroupRow>
    </ProjectRow>
  </ul>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import FlowRow from './FlowRow.vue'
import GroupRow from './GroupRow.vue'
import ProjectRow from './ProjectRow.vue'
import type { ExplorerFlow, ExplorerGroup, ExplorerProject, ExplorerSelection } from './projectExplorerTree'

/** flowgate.v02.0060 T#2: the row a right click hit. */
type ExplorerContextRow = { kind: 'project' } | { kind: 'group'; group: ExplorerGroup } | { kind: 'flow'; flow: ExplorerFlow }

const props = defineProps<{
  project: ExplorerProject
  expandedIds: Set<string>
  selection: ExplorerSelection
  activeFlowId: string | null
  label: string
}>()

const emit = defineEmits<{
  toggle: [id: string]
  'select-project': [project: ExplorerProject]
  'select-group': [group: ExplorerGroup]
  'select-flow': [flow: ExplorerFlow]
  'activate-flow': [flow: ExplorerFlow]
  context: [event: MouseEvent, row: ExplorerContextRow]
}>()

const { t } = useI18n()
const rootEl = ref<HTMLElement | null>(null)

function isExpanded(id: string): boolean {
  return props.expandedIds.has(id)
}

function isSelected(kind: 'project' | 'group' | 'flow', id: string): boolean {
  return props.selection?.kind === kind && props.selection.id === id
}

// Up/Down/Home/End move focus across the rows currently rendered (collapsed subtrees are
// not in the DOM, so they are skipped automatically). Left/Right/Enter/Space are handled by
// the focused row itself (ExplorerRowFrame).
function onKeydown(e: KeyboardEvent) {
  if (!['ArrowUp', 'ArrowDown', 'Home', 'End'].includes(e.key)) return
  const rows = Array.from(rootEl.value?.querySelectorAll<HTMLElement>('[data-explorer-row]') ?? [])
  if (rows.length === 0) return
  const current = rows.indexOf(document.activeElement as HTMLElement)
  let next = current
  if (e.key === 'ArrowDown') next = current < 0 ? 0 : Math.min(rows.length - 1, current + 1)
  else if (e.key === 'ArrowUp') next = current < 0 ? 0 : Math.max(0, current - 1)
  else if (e.key === 'Home') next = 0
  else next = rows.length - 1
  e.preventDefault()
  rows[next]?.focus()
}
</script>

<style scoped>
.explorer-tree {
  margin: 0;
  padding: 0;
}

.explorer-hint {
  list-style: none;
  padding: 4px 8px;
  font-size: 12px;
  font-style: italic;
  color: var(--fg2-muted-2);
}

/* Lines up with the rows of the level the hint stands in for (ExplorerRowFrame indent). */
.explorer-hint--group {
  padding-left: 40px;
}

.explorer-hint--flow {
  padding-left: 54px;
}
</style>
