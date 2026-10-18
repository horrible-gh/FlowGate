<template>
  <!-- flowgate.v02.0026 — Flow Tabs strip (0003-D §3.1.3). Stateless: shows the open tabs and
       raises activate / close requests. ui/Tabs cannot hold a close button per tab, so this
       follows the same tab pattern (tablist, aria-selected, roving tabindex, arrow keys with
       wrap-around and focus following) with [tab button][close button] side by side — never a
       button inside a button. Overflowing tabs scroll sideways inside the strip only.
       flowgate.v02.0060 T#2: inside the Designer a right click on a tab (that tab's id — it is not activated) or on the
       strip's blank part goes to the Designer context menu (닫기 / 다른 탭 닫기 / 전체 닫기). -->
  <div
    class="flow-tab-strip"
    role="tablist"
    :aria-label="t('gui.designer.center.tabs_label')"
    data-test="designer-flow-tabs"
    @contextmenu="contextMenu?.tab($event, null)"
  >
    <div
      v-for="tab in tabs"
      :key="tab.flowId"
      class="flow-tab"
      :class="{ 'is-active': tab.flowId === activeFlowId }"
      role="presentation"
      data-test="designer-flow-tab"
      :data-flow-id="tab.flowId"
      @contextmenu="contextMenu?.tab($event, tab.flowId, buttonRefs.get(tab.flowId) ?? null)"
    >
      <button
        :ref="(el) => setButtonRef(tab.flowId, el)"
        type="button"
        role="tab"
        class="flow-tab-button"
        :aria-selected="tab.flowId === activeFlowId"
        :tabindex="tab.flowId === activeFlowId ? 0 : -1"
        :title="tab.name"
        data-test="designer-flow-tab-button"
        @click="emit('activate', tab.flowId)"
        @keydown="onKeydown($event, tab.flowId)"
      >
        {{ tab.name }}
      </button>
      <IconButton
        class="flow-tab-close"
        size="sm"
        :label="t('gui.designer.center.close_tab', { name: tab.name })"
        :title="t('gui.designer.center.close_tab', { name: tab.name })"
        data-test="designer-flow-tab-close"
        @click="emit('close', tab.flowId)"
      >
        <svg viewBox="0 0 16 16" aria-hidden="true">
          <path d="M4.5 4.5l7 7M11.5 4.5l-7 7" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
        </svg>
      </IconButton>
    </div>
  </div>
</template>

<script setup lang="ts">
import { inject, nextTick, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import IconButton from '../../../components/ui/IconButton.vue'

export interface FlowTabStripItem {
  readonly flowId: string
  readonly name: string
}

const props = defineProps<{
  tabs: readonly FlowTabStripItem[]
  activeFlowId: string | null
}>()

const emit = defineEmits<{
  activate: [flowId: string]
  close: [flowId: string]
}>()

const { t } = useI18n()

/** flowgate.v02.0060 T#2: the Designer context menu's tab side, provided by the Designer Shell; null elsewhere. */
interface TabContextMenu {
  tab(event: MouseEvent, flowId: string | null, fallback?: HTMLElement | null): boolean
}
const contextMenu = inject<TabContextMenu | null>(Symbol.for('flowgate.designer.contextMenu'), null)

const buttonRefs = new Map<string, HTMLButtonElement>()

function setButtonRef(flowId: string, el: unknown) {
  if (el) buttonRefs.set(flowId, el as HTMLButtonElement)
  else buttonRefs.delete(flowId)
}

function onKeydown(event: KeyboardEvent, flowId: string) {
  if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') return
  event.preventDefault()
  const count = props.tabs.length
  const index = props.tabs.findIndex((tab) => tab.flowId === flowId)
  if (index === -1 || count === 0) return
  const next = props.tabs[(index + (event.key === 'ArrowRight' ? 1 : -1) + count) % count]
  emit('activate', next.flowId)
  nextTick(() => buttonRefs.get(next.flowId)?.focus())
}

// A newly active tab is scrolled into view inside the strip.
watch(
  () => props.activeFlowId,
  (flowId) => {
    if (flowId === null) return
    nextTick(() => buttonRefs.get(flowId)?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' }))
  },
)
</script>

<style scoped>
.flow-tab-strip {
  display: flex;
  flex: none;
  align-items: stretch;
  gap: 2px;
  min-width: 0;
  height: 36px;
  padding: 0 6px;
  overflow-x: auto;
  overflow-y: hidden;
  border-bottom: 1px solid var(--fg2-line);
  background: var(--fg2-panel);
  scrollbar-width: thin;
}

.flow-tab {
  display: flex;
  flex: none;
  align-items: center;
  gap: 2px;
  max-width: 220px;
  padding: 0 4px 0 2px;
  border-bottom: 2px solid transparent;
  color: var(--fg2-muted);
}

.flow-tab:hover {
  color: var(--fg2-text);
}

.flow-tab.is-active {
  border-bottom-color: var(--fg2-blue);
  background: rgba(75, 163, 255, 0.08);
  color: var(--fg2-text);
}

.flow-tab-button {
  min-width: 0;
  padding: 0 6px 0 10px;
  overflow: hidden;
  border: 0;
  background: none;
  color: inherit;
  font-family: inherit;
  font-size: 12px;
  font-weight: 600;
  line-height: 32px;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
}

.flow-tab-button:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: -2px;
}

.flow-tab-close {
  width: 22px;
  height: 22px;
}
</style>
