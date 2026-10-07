<template>
  <!-- flowgate.v02.0037 — right Tool Panel (R0001 §2): the final container for the right Dock
       now assembles the existing right parts behind one [팔레트] [대화] [Git] header row —
       0030 Palette, 0031/0032 Conversation and 0033 Git. Each part keeps its own content; this
       container owns only the tab row, the body that gives the part its height, and the mount
       policy. Mount policy (as the left Dock): a part is mounted the first time its tab is
       shown and is only hidden afterwards, so its session (chat, Git badge, Palette items)
       survives a tab switch. Part intents with no target screen yet (Palette select / drag,
       Conversation open-change, Git open-diff / confirm settings / windows / badge) are
       intentionally not handled here — later Feature groups wire them. -->
  <aside
    class="designer-right-dock"
    :aria-label="t('gui.designer.shell.tool_tabs_label')"
    data-test="designer-right-dock"
    :data-collapsed="collapsed ? 'true' : 'false'"
    :data-tool="tool"
  >
    <header v-show="!collapsed" class="designer-right-header" data-test="designer-right-header">
      <Tabs
        class="designer-right-tabs"
        :tabs="tabs"
        :model-value="tool"
        :aria-label="t('gui.designer.shell.tool_tabs_label')"
        data-test="designer-right-tool-tabs"
        @update:model-value="onSelect"
      />
    </header>

    <div v-show="!collapsed" class="designer-right-body" data-test="designer-right-body">
      <div v-if="mountedTools.palette" v-show="tool === 'palette'" class="designer-right-pane" data-test="designer-right-pane-palette">
        <PalettePanel />
      </div>
      <div
        v-if="mountedTools.conversation"
        v-show="tool === 'conversation'"
        class="designer-right-pane is-fill"
        data-test="designer-right-pane-conversation"
      >
        <ConversationPanel :project-id="projectId" :group-id="activeFlow?.groupId ?? null" />
      </div>
      <div v-if="mountedTools.git" v-show="tool === 'git'" class="designer-right-pane" data-test="designer-right-pane-git">
        <DesignerGitPanel :project-id="projectId" :flow="activeFlow" :active="!collapsed && tool === 'git'" />
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
import { DESIGNER_RIGHT_TOOLS, type DesignerRightTool } from './designerLayout'

const props = defineProps<{
  projectId: string | null
  activeFlow: FlowRef | null
  tool: DesignerRightTool
  collapsed: boolean
}>()

const emit = defineEmits<{ 'update:tool': [tool: DesignerRightTool] }>()

const { t } = useI18n()

const tabs = computed<TabItem[]>(() =>
  DESIGNER_RIGHT_TOOLS.map((tool) => ({ key: tool, label: t(`gui.designer.shell.tool_${tool}`) })),
)

function onSelect(key: string) {
  const tool = DESIGNER_RIGHT_TOOLS.find((k) => k === key)
  if (tool) emit('update:tool', tool)
}

// Which parts have been shown at least once. Rendering detail of this Dock, not layout state:
// it only grows, so nothing already mounted is ever torn down by a switch.
const mountedTools = reactive<Record<DesignerRightTool, boolean>>({ palette: false, conversation: false, git: false })

watch(
  () => props.tool,
  (tool) => {
    mountedTools[tool] = true
  },
  { immediate: true },
)
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

/* The header draws the single bottom rule; the tab strip keeps only its active underline. */
.designer-right-tabs {
  flex: 1 1 auto;
  min-width: 0;
  border-bottom: 0;
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
