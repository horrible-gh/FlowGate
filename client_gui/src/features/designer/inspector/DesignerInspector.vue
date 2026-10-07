<template>
  <!-- flowgate.v02.0036 — bottom Inspector Frame (0035 v7): the common frame every Node shares.
       No Node selected: one line, no header or tabs. A Node selected: header (40px) + tabs
       (32px) fixed above a scrolling Content area — 72px before the content.
       Boundaries for later groups: the header `header-meta` / `header-actions` slots and one
       Content slot per tab (`tab-documents`, `tab-qna`, `tab-attachments`, `tab-settings`),
       each given the selected Node. A tab nobody fills yet shows a placeholder line. The Frame
       owns only the selected tab; it calls no API and holds no Node data of its own.
       flowgate.v02.0039 T#3: `unselected` (the Designer has no work target yet) leaves only the
       bottom frame — an empty header bar, no line, no tabs and no Content. -->
  <section
    class="designer-inspector"
    data-test="designer-inspector"
    :data-selected="node && !unselected ? 'true' : 'false'"
    :data-unselected="unselected ? 'true' : 'false'"
    :aria-label="t('gui.designer.inspector.label')"
  >
    <div v-if="unselected" class="designer-inspector-frame" data-test="designer-inspector-frame" />
    <p v-else-if="!node" class="designer-inspector-empty" data-test="designer-inspector-empty">
      {{ t('gui.designer.inspector.no_selection') }}
    </p>
    <template v-else>
      <InspectorHeader :node="node">
        <template #meta><slot name="header-meta" :node="node" /></template>
        <template v-if="$slots['header-actions']" #actions><slot name="header-actions" :node="node" /></template>
      </InspectorHeader>
      <Tabs
        class="designer-inspector-tabs"
        data-test="designer-inspector-tabs"
        :style="tabStripStyle"
        :tabs="tabItems"
        :model-value="activeTab"
        :aria-label="t('gui.designer.inspector.tabs_label')"
        @update:model-value="selectTab"
      />
      <div class="designer-inspector-body" role="tabpanel" data-test="designer-inspector-body" :data-tab="activeTab">
        <slot :name="`tab-${activeTab}`" :node="node">
          <p class="designer-inspector-placeholder" data-test="designer-inspector-placeholder">
            {{ t('gui.designer.inspector.pane_placeholder', { tab: t(`gui.designer.inspector.tabs.${activeTab}`) }) }}
          </p>
        </slot>
      </div>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Tabs, { type TabItem } from '../../../components/ui/Tabs.vue'
import type { FlowGraphNode } from '../center/graph/flowGraph'
import InspectorHeader from './InspectorHeader.vue'
import {
  DEFAULT_INSPECTOR_TAB,
  INSPECTOR_TAB_GAP,
  INSPECTOR_TAB_MIN_WIDTH,
  INSPECTOR_TAB_STRIP_PADDING_X,
  inspectorTabsFor,
  resolveInspectorTab,
  type InspectorTabKey,
} from './inspectorTabs'

const props = withDefaults(
  defineProps<{
    /** The selected Node of the active Flow, or null when nothing is selected. */
    node: FlowGraphNode | null
    /** False hides the trailing 설정 tab (decided per card definition by a later group). */
    hasSettings?: boolean
    /** The Designer has no work target yet (0039 T#3): frame only. */
    unselected?: boolean
  }>(),
  { hasSettings: true, unselected: false },
)

const { t } = useI18n()

const tabs = computed(() => inspectorTabsFor(props.hasSettings))

const tabItems = computed<TabItem[]>(() =>
  tabs.value.map((key) => ({ key, label: t(`gui.designer.inspector.tabs.${key}`) })),
)

// Kept across Node changes and while nothing is selected (v7).
const selectedTab = ref<InspectorTabKey>(DEFAULT_INSPECTOR_TAB)

const activeTab = computed(() => resolveInspectorTab(selectedTab.value, tabs.value))

// A Node without the selected tab sends the selection back to 문서 for good (v7).
watch(tabs, (next) => {
  selectedTab.value = resolveInspectorTab(selectedTab.value, next)
})

// Strip geometry comes from the tab rules so the Shell's minimum Inspector width and the CSS agree.
const tabStripStyle = {
  gap: `${INSPECTOR_TAB_GAP}px`,
  paddingLeft: `${INSPECTOR_TAB_STRIP_PADDING_X}px`,
  paddingRight: `${INSPECTOR_TAB_STRIP_PADDING_X}px`,
  '--inspector-tab-min-width': `${INSPECTOR_TAB_MIN_WIDTH}px`,
}

function selectTab(key: string) {
  selectedTab.value = resolveInspectorTab(key as InspectorTabKey, tabs.value)
}
</script>

<style scoped>
.designer-inspector {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  background: #091624;
}

.designer-inspector-frame {
  flex: 0 0 48px;
  height: 48px;
  border-bottom: 1px solid var(--fg2-line);
  background: #0c1b2d;
}

.designer-inspector-empty {
  display: grid;
  flex: 1 1 auto;
  place-items: center;
  margin: 0;
  color: var(--fg2-muted-2);
  font-size: 13px;
}

/* The shared Tabs primitive, sized to the v7 32px tab row. Gap and side padding come from
   tabStripStyle. The strip never wraps or scrolls: on a narrow Inspector every tab shrinks to
   --inspector-tab-min-width and ellipsises its label, so all tabs stay visible and clickable. */
.designer-inspector-tabs {
  flex: 0 0 32px;
  flex-wrap: nowrap;
  align-items: flex-end;
  box-sizing: border-box;
  height: 32px;
  min-width: 0;
  overflow: hidden;
  background: #0a1828;
}

.designer-inspector-tabs :deep(.ui-tab) {
  flex: 0 1 auto;
  box-sizing: border-box;
  min-width: var(--inspector-tab-min-width);
  height: 28px;
  overflow: hidden;
  padding: 0 12px;
  border-radius: 5px 5px 0 0;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.designer-inspector-tabs :deep(.ui-tab:hover:not(:disabled)) {
  background: #10233b;
}

.designer-inspector-tabs :deep(.ui-tab.is-active) {
  background: #10243e;
}

.designer-inspector-body {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  padding: 12px 14px;
}

.designer-inspector-placeholder {
  margin: 0;
  padding: 14px;
  border: 1px dashed var(--fg2-line-strong);
  border-radius: 8px;
  color: var(--fg2-muted-2);
  font-size: 12px;
  text-align: center;
}
</style>
