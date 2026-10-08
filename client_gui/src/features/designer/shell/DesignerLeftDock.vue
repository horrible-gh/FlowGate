<template>
  <!-- flowgate.v02.0025 — left navigation Dock (0003-D §3.3/§3.4). Assembles the real 0023
       Project Explorer and 0024 File Explorer behind a [프로젝트] [파일] switch, which is also
       the Dock's one header row (with the collapse button on its right).
       Mount policy: an Explorer is mounted the first time its mode is shown and is only
       hidden afterwards (mode switch or collapse), so its search / expansion / selection /
       loaded tree survive. The Dock element itself always stays; collapsing turns it into a
       rail that keeps the expand button and the current mode name.
       flowgate.v02.0039 T#3: while the Designer has no work target (`unselected`) the mode
       switch, the collapse button and the Explorer refresh are unavailable as the confirmed
       initial-state design draws them, but the Project tree stays shown and clickable — a
       click there is how the user makes the first selection (0001-R §2). The Explorer search
       is available whenever a Project is open (flowgate.v02.0044).
       flowgate.v02.0043: the Shell's Explorer state request (File menu: Group / Flow 열기,
       Group 닫기) and refresh token (a new Group / Flow) pass through to the Project Explorer. -->
  <aside
    class="designer-left-dock"
    :class="{ 'is-collapsed': collapsed, 'is-unselected': unselected }"
    :aria-label="t('gui.designer.shell.explorer_title')"
    data-test="designer-left-dock"
    :data-collapsed="collapsed ? 'true' : 'false'"
    :data-mode="mode"
    :data-unselected="unselected ? 'true' : 'false'"
  >
    <div v-if="collapsed" class="designer-left-rail" data-test="designer-left-rail">
      <IconButton
        size="sm"
        :label="t('gui.designer.shell.expand_left')"
        :title="t('gui.designer.shell.expand_left')"
        data-test="designer-left-expand"
        @click="emit('expand')"
      >
        <svg viewBox="0 0 16 16" aria-hidden="true">
          <path d="M6 4l4 4-4 4" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </IconButton>
      <span class="designer-left-rail-mode" data-test="designer-left-rail-mode">{{ t(`gui.designer.shell.mode_${mode}`) }}</span>
    </div>

    <ExplorerModeTabs
      v-show="!collapsed"
      :model-value="mode"
      :disabled="unselected"
      @update:model-value="emit('update:mode', $event)"
      @collapse="emit('collapse')"
    />

    <div v-show="!collapsed" class="designer-left-explorers" data-test="designer-left-explorers">
      <ProjectExplorer
        v-if="mountedModes.project"
        v-show="mode === 'project'"
        :project-id="projectId"
        :active-flow-id="activeFlow?.groupId ?? null"
        :tools-disabled="unselected"
        :request="explorerRequest"
        :refresh-token="refreshToken"
        @activate-flow="emit('activate-flow', $event)"
        @update:selection="emit('update:selection', $event)"
      />
      <DesignerFileExplorer
        v-if="mountedModes.file"
        v-show="mode === 'file'"
        :project-id="projectId"
        :flow="activeFlow"
      />
    </div>
  </aside>
</template>

<script setup lang="ts">
import { reactive, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { FlowRef } from '@/features/project'
import IconButton from '../../../components/ui/IconButton.vue'
import DesignerFileExplorer from '../fileExplorer/DesignerFileExplorer.vue'
import ProjectExplorer from '../projectExplorer/ProjectExplorer.vue'
import type { ExplorerSelection, ExplorerStateRequest, ProjectExplorerFlowEvent } from '../projectExplorer/projectExplorerTree'
import ExplorerModeTabs from './ExplorerModeTabs.vue'
import type { DesignerLeftMode } from './designerLayout'

const props = withDefaults(
  defineProps<{
    projectId: string | null
    activeFlow: FlowRef | null
    mode: DesignerLeftMode
    collapsed: boolean
    /** No work target yet (0039 T#3): navigation chrome unavailable, tree still usable. */
    unselected?: boolean
    /** What the Designer asks the Project Explorer to select / open / close (0043). */
    explorerRequest?: ExplorerStateRequest | null
    /** Bumped to make the Project Explorer re-read its tree (0043). */
    refreshToken?: number
  }>(),
  { unselected: false, explorerRequest: null, refreshToken: 0 },
)

const emit = defineEmits<{
  'update:mode': [mode: DesignerLeftMode]
  collapse: []
  expand: []
  'activate-flow': [payload: ProjectExplorerFlowEvent]
  'update:selection': [selection: ExplorerSelection]
}>()

const { t } = useI18n()

// Which Explorers have been shown at least once. Rendering detail of this Dock, not layout
// state: it only grows, so nothing already mounted is ever torn down by a switch.
const mountedModes = reactive<Record<DesignerLeftMode, boolean>>({ project: false, file: false })

watch(
  () => props.mode,
  (mode) => {
    mountedModes[mode] = true
  },
  { immediate: true },
)
</script>

<style scoped>
.designer-left-dock {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  border-right: 1px solid var(--fg2-line-strong);
  background: var(--fg2-panel);
}

.designer-left-explorers {
  flex: 1 1 auto;
  min-height: 0;
  overflow: hidden;
}

.designer-left-rail {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 6px 0;
  min-height: 0;
}

.designer-left-rail-mode {
  writing-mode: vertical-rl;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.08em;
  color: var(--fg2-muted);
}
</style>
