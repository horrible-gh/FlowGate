<template>
  <!-- flowgate.v02.0037 — right Tool Panel (R0001 §2): the final container for the right Dock
       now assembles the existing right parts behind one [팔레트] [대화] [Git] header row —
       0030 Palette, 0031/0032 Conversation and 0033 Git. Each part keeps its own content; this
       container owns only the tab row, the body that gives the part its height, and the mount
       policy. Mount policy (as the left Dock): a part is mounted the first time its tab is
       shown and is only hidden afterwards, so its session (chat, Git badge, Palette items)
       survives a tab switch. Part intents with no target screen yet (Palette select / drag,
       Conversation open-change, Git open-diff / confirm settings / windows / badge) are
       intentionally not handled here — later Feature groups wire them.
       flowgate.v02.0039 T#3: with no work target (`unselected`) the tab row stays on screen
       dimmed and unavailable and the body is an empty frame — no part is shown or mounted
       until there is a target (confirmed initial-state design). A part mounted earlier stays
       mounted but gets no Project / Flow context while there is no target, so a Project switch
       back to the initial state does not read Git / Conversation data for the new Project.
       flowgate.v02.0061 T#14: `sourceLocked` (the open Flow's Graph run is running / paused / stopping) goes to the
       Conversation, whose AI source access mode then cannot change.
       flowgate.v02.0066 T#6 sent the Git part's `open-diff` and `git-center-diff-refresh` up to the Shell's Center
       Diff; flowgate.v02.0067 T#4 shows that diff in the Git part's own modeless, so none of its intents goes up. -->
  <aside
    class="designer-right-dock"
    :class="{ 'is-unselected': unselected }"
    :aria-label="t('gui.designer.shell.tool_tabs_label')"
    data-test="designer-right-dock"
    :data-collapsed="collapsed ? 'true' : 'false'"
    :data-tool="tool"
    :data-unselected="unselected ? 'true' : 'false'"
  >
    <header v-show="!collapsed" class="designer-right-header" data-test="designer-right-header">
      <Tabs
        class="designer-right-tabs"
        :tabs="tabs"
        :model-value="tool"
        :aria-label="t('gui.designer.shell.tool_tabs_label')"
        data-test="designer-right-tool-tabs"
        @update:model-value="onSelect"
      >
        <template #tab="{ tab }">
          <!-- Original vector artwork: palette / chat; the existing Git geometry is retained. -->
          <svg class="designer-right-tab-icon" :class="'is-' + tab.key" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false">
            <g v-if="tab.key === 'palette'">
              <path d="M8 1.25C4.2 1.25 1.25 4 1.25 7.5c0 4 3 7.25 6.5 7.25h1.1c1.2 0 1.9-1.3 1.2-2.3l-.4-.6c-.5-.7 0-1.6.9-1.6H12c1.7 0 2.75-1.2 2.75-2.9 0-3.4-3-6.1-6.75-6.1Z" fill="#ffe0a6" stroke="#d89b58" stroke-width="0.75" />
              <circle cx="4.3" cy="5.4" r="1.2" fill="#f15b68" />
              <circle cx="7.6" cy="3.8" r="1.2" fill="#5e9eff" />
              <circle cx="11.1" cy="5.1" r="1.2" fill="#55c98d" />
              <circle cx="4.2" cy="9" r="1.2" fill="#b584ef" />
              <circle cx="7.1" cy="11.9" r="1.1" fill="#efaa38" />
            </g>
            <g v-else-if="tab.key === 'conversation'">
              <path d="M3 2.5h10a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1H7l-4 3v-3a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1Z" fill="#61c6ff" stroke="#b9eaff" stroke-width="0.75" stroke-linejoin="round" />
              <g fill="#174c78">
                <circle cx="5" cy="7" r="0.85" />
                <circle cx="8" cy="7" r="0.85" />
                <circle cx="11" cy="7" r="0.85" />
              </g>
            </g>
            <g v-else fill="none" stroke="currentColor" stroke-width="1.3">
              <path d="M4 5v6m8-6v1a3 3 0 0 1-3 3H7a3 3 0 0 0-3 3" />
              <circle cx="4" cy="3" r="2" />
              <circle cx="12" cy="3" r="2" />
              <circle cx="4" cy="13" r="2" />
            </g>
          </svg>
          <span class="designer-right-tab-label">{{ tab.label }}</span>
        </template>
      </Tabs>
    </header>

    <div v-show="!collapsed" class="designer-right-body" data-test="designer-right-body">
      <div v-if="mountedTools.palette" v-show="!unselected && tool === 'palette'" class="designer-right-pane" data-test="designer-right-pane-palette">
        <PalettePanel :selected-item-id="selectedPaletteId" @select="emit('palette-select', $event)" @drag-start="emit('palette-drag-start')" />
      </div>
      <div
        v-if="mountedTools.conversation"
        v-show="!unselected && tool === 'conversation'"
        class="designer-right-pane is-fill"
        data-test="designer-right-pane-conversation"
      >
        <ConversationPanel :project-id="partProjectId" :group-id="partFlow?.groupId ?? null" :source-locked="sourceLocked" />
      </div>
      <div v-if="mountedTools.git" v-show="!unselected && tool === 'git'" class="designer-right-pane" data-test="designer-right-pane-git">
        <DesignerGitPanel
          :project-id="partProjectId"
          :flow="partFlow"
          :active="!collapsed && !unselected && tool === 'git'"
        />
      </div>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { computed, reactive, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ConversationPanel } from '@/features/conversation'
import { DesignerGitPanel } from '@/features/git'
import type { FlowRef } from '@/features/project'
import Tabs, { type TabItem } from '../../../components/ui/Tabs.vue'
import PalettePanel from '../palette/PalettePanel.vue'
import type { PaletteItemDefinition } from '../palette/paletteCatalog'
import { DESIGNER_RIGHT_TOOLS, type DesignerRightTool } from './designerLayout'

const props = withDefaults(
  defineProps<{
    projectId: string | null
    activeFlow: FlowRef | null
    tool: DesignerRightTool
    collapsed: boolean
    selectedPaletteId?: string | null
    /** No work target yet (0039 T#3): tabs unavailable, no part shown. */
    unselected?: boolean
    /** flowgate.v02.0061 T#14: the open Flow's Graph run locks the Conversation's source access mode. */
    sourceLocked?: boolean
  }>(),
  { unselected: false, selectedPaletteId: null, sourceLocked: false },
)

const emit = defineEmits<{
  'update:tool': [tool: DesignerRightTool]
  'palette-select': [definition: PaletteItemDefinition]
  'palette-drag-start': []
}>()

const { t } = useI18n()

const tabs = computed<TabItem[]>(() =>
  DESIGNER_RIGHT_TOOLS.map((tool) => ({
    key: tool,
    label: t(`gui.designer.shell.tool_${tool}`),
    disabled: props.unselected,
  })),
)

function onSelect(key: string) {
  const tool = DESIGNER_RIGHT_TOOLS.find((k) => k === key)
  if (tool) emit('update:tool', tool)
}

// Which parts have been shown at least once. Rendering detail of this Dock, not layout state:
// it only grows, so nothing already mounted is ever torn down by a switch. A part is not
// mounted while there is no work target (0039 T#3); it mounts once there is one.
const mountedTools = reactive<Record<DesignerRightTool, boolean>>({ palette: false, conversation: false, git: false })

watch(
  [() => props.tool, () => props.unselected],
  ([tool, unselected]) => {
    if (!unselected) mountedTools[tool] = true
  },
  { immediate: true },
)

// Context handed to the mounted parts: none while there is no work target (0039 T#3). The parts
// reset to their empty state and read nothing; the target's context arrives with the first choice.
const partProjectId = computed(() => (props.unselected ? null : props.projectId))
const partFlow = computed(() => (props.unselected ? null : props.activeFlow))
</script>

<style scoped>
.designer-right-dock {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  border-left: 1px solid var(--fg2-line-strong);
  background: var(--fg2-panel);
}

.designer-right-header {
  display: flex;
  flex: none;
  align-items: center;
  padding: 0 4px 0 6px;
  border-bottom: 1px solid var(--fg2-line);
}

/* No work target (0039 T#3, confirmed design v7): dimmed and unavailable. */
.designer-right-dock.is-unselected .designer-right-header {
  opacity: 0.46;
  filter: saturate(0.55);
  user-select: none;
}

/* NR §4.2: boxed choices; the shared Tabs default remains unchanged elsewhere. */
.designer-right-tabs {
  flex: 1 1 auto;
  min-width: 0;
  border-bottom: 0;
  gap: 6px;
  padding: 6px 0;
  flex-wrap: nowrap;
}

.designer-right-tabs :deep(.ui-tab) {
  flex: 1 1 0;
  box-sizing: border-box;
  min-width: 0;
  height: 32px;
  padding: 0 4px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  border: 1px solid var(--fg2-line-strong);
  border-radius: 6px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  background: var(--fg2-control-surface-flat);
  font-size: 12px;
}

.designer-right-tabs :deep(.ui-tab.is-active) {
  background: var(--fg2-status-info-bg);
  border-color: var(--fg2-blue);
}

.designer-right-tab-icon {
  flex: none;
  width: 14px;
  height: 14px;
}

/* Warm Git accent preserves the branch shape; selection brightens every vector icon. */
.designer-right-tab-icon.is-git {
  color: #f7a278;
}

.designer-right-tabs :deep(.ui-tab.is-active) .designer-right-tab-icon {
  filter: saturate(1.15) drop-shadow(0 0 2px rgba(87, 167, 255, 0.38));
}

.designer-right-tabs :deep(.ui-tab.is-active) .designer-right-tab-icon.is-git {
  color: #ffd0a6;
}

.designer-right-tab-label {
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.designer-right-body {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}

/* Palette and Git flow to their natural height and scroll here; the Conversation fills the
   pane and scrolls its own timeline. */
.designer-right-pane {
  box-sizing: border-box;
  height: 100%;
  min-width: 0;
  min-height: 0;
  overflow: auto;
}

.designer-right-pane.is-fill {
  overflow: hidden;
}
</style>
