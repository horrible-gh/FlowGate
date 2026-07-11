<template>
  <div class="tabs-bar-wrap">
    <div class="tabs-bar" role="tablist" :aria-label="t('main.tabs.new')" ref="tabsBar" @wheel.prevent="onWheel">
      <div
        role="tab"
        tabindex="0"
        :aria-selected="!activeTabId"
        aria-controls="panel-overview"
        id="tab-overview"
        class="tab-item"
        :class="{ active: !activeTabId }"
        @click="tabsStore.activeTabId = null"
        @keydown.enter="tabsStore.activeTabId = null"
      >
        <AppIcon name="house" style="font-size:.75rem;" /> {{ t('main.tab_bar.text_14') }}
      </div>
      <div
        v-for="(tab, idx) in tabs"
        :key="tab.id"
        role="tab"
        tabindex="0"
        :aria-selected="tab.id === activeTabId"
        :aria-controls="`panel-${tab.id}`"
        :id="`tab-${tab.id}`"
        class="tab-item"
        :class="{ active: tab.id === activeTabId }"
        draggable="true"
        @click="tabsStore.activeTabId = tab.id"
        @keydown.enter="tabsStore.activeTabId = tab.id"
        @contextmenu.prevent.stop="openTabContext($event, tab.id)"
        @dragstart="dragStart(idx)"
        @dragover.prevent
        @drop="dragDrop(idx)"
      >
        <span v-if="tab.typeCode" class="doc-tag" :class="`c-${tab.typeCode}`" style="font-size:.6rem; font-weight:700; padding:1px 4px; flex-shrink:0;">{{ tab.typeCode }}</span>
        <span class="tab-title">{{ getTabDisplayTitle(tab) }}</span>
        <span
          class="tab-x"
          :aria-label="t('main.tabs.close')"
          @click.stop="tabsStore.closeTab(tab.id)"
        >
          <AppIcon name="x" />
        </span>
      </div>
    </div>
    <ContextMenu v-model:visible="showTabContext" :x="ctxX" :y="ctxY">
      <ContextMenuItem icon="x" @click="closeContextTab">
        {{ t('main.tab_bar.close') }}
      </ContextMenuItem>
      <ContextMenuItem icon="x-square" @click="closeAllTabs">
        {{ t('main.tab_bar.close_all') }}
      </ContextMenuItem>
      <ContextMenuItem icon="browsers" @click="closeOtherTabs">
        {{ t('main.tab_bar.close_others') }}
      </ContextMenuItem>
    </ContextMenu>
    <button v-if="showButtons" class="tabs-scroll-btn tabs-scroll-left" :disabled="leftDisabled" @click="scrollByLeft" :aria-label="t('main.tabs.scroll_left')">&#9664;</button>
    <button v-if="showButtons" class="tabs-scroll-btn tabs-scroll-right" :disabled="rightDisabled" @click="scrollByRight" :aria-label="t('main.tabs.scroll_right')">&#9654;</button>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import { useTabsStore } from '../stores/tabs'
import { useDocTypeStore } from '../stores/docTypeStore'
import type { Tab } from '../stores/tabs'
import ContextMenu from './common/ContextMenu.vue'
import ContextMenuItem from './common/ContextMenuItem.vue'
import AppIcon from '@shared/AppIcon.vue'

defineEmits<{ newTab: [] }>()
const { t } = useI18n()
const tabsStore = useTabsStore()
const docTypeStore = useDocTypeStore()

function getTabDisplayTitle(tab: Tab): string {
  // Strip legacy "[TypeLabel]: " prefix that was embedded by the server (always English).
  const rawTitle = tab.title.replace(/^\[.*?\]:\s*/, '')
  if (tab.typeCode) {
    return `[${docTypeStore.getLabel(tab.typeCode)}]: ${rawTitle}`
  }
  return tab.title
}
const tabs = computed(() => tabsStore.tabs)
const activeTabId = computed(() => tabsStore.activeTabId)

const tabsBar = ref<HTMLElement | null>(null)
const showButtons = ref(false)
const leftDisabled = ref(true)
const rightDisabled = ref(false)
const showTabContext = ref(false)
const ctxX = ref(0)
const ctxY = ref(0)
const contextTabId = ref<string | null>(null)
const scrollEdgeTolerance = 1

function updateScrollBtns() {
  const el = tabsBar.value
  if (!el) return
  const maxScrollLeft = el.scrollWidth - el.clientWidth
  const hasOverflow = maxScrollLeft > scrollEdgeTolerance
  const tabs = getTabItems()
  const viewportLeft = el.scrollLeft
  const viewportRight = viewportLeft + el.clientWidth

  showButtons.value = hasOverflow
  leftDisabled.value = !hasOverflow || !tabs.some(t => t.offsetLeft < viewportLeft - scrollEdgeTolerance)
  rightDisabled.value = !hasOverflow || !tabs.some(t => t.offsetLeft + t.offsetWidth > viewportRight + scrollEdgeTolerance)
}

function onWheel(e: WheelEvent) {
  if (tabsBar.value) {
    tabsBar.value.scrollLeft += e.deltaY
  }
}

function openTabContext(e: MouseEvent, tabId: string) {
  contextTabId.value = tabId
  ctxX.value = e.clientX
  ctxY.value = e.clientY
  showTabContext.value = true
}

function closeContextTab() {
  if (contextTabId.value) tabsStore.closeTab(contextTabId.value)
  showTabContext.value = false
}

function closeAllTabs() {
  tabsStore.closeAll()
  showTabContext.value = false
}

function closeOtherTabs() {
  if (contextTabId.value) tabsStore.closeOthers(contextTabId.value)
  showTabContext.value = false
}

function getTabItems(): HTMLElement[] {
  return tabsBar.value
    ? Array.from(tabsBar.value.querySelectorAll<HTMLElement>('.tab-item'))
    : []
}

function revealActiveTab() {
  const el = tabsBar.value
  if (!el) return

  const activeId = activeTabId.value ? `tab-${activeTabId.value}` : 'tab-overview'
  const activeTab = getTabItems().find(tab => tab.id === activeId)
  if (!activeTab) {
    updateScrollBtns()
    return
  }

  const viewportRect = el.getBoundingClientRect()
  const tabRect = activeTab.getBoundingClientRect()
  let targetLeft: number | null = null

  if (tabRect.left < viewportRect.left) {
    targetLeft = el.scrollLeft + tabRect.left - viewportRect.left
  } else if (tabRect.right > viewportRect.right) {
    targetLeft = el.scrollLeft + tabRect.right - viewportRect.right
  }

  if (targetLeft != null) {
    el.scrollTo({ left: targetLeft, behavior: 'smooth' })
  }
  updateScrollBtns()
}

async function trackActiveTab() {
  await nextTick()
  updateScrollBtns()
  // Overflow buttons reduce the tab viewport after they render.
  await nextTick()
  revealActiveTab()
}

function scrollByLeft() {
  const el = tabsBar.value
  if (!el) return
  const tabs = getTabItems()
  const scrollLeft = el.scrollLeft
  // Find the rightmost tab that is clipped on the left (offsetLeft < scrollLeft)
  const clipped = tabs.filter(t => t.offsetLeft < scrollLeft)
  if (clipped.length === 0) return
  const target = clipped.reduce((a, b) => (b.offsetLeft > a.offsetLeft ? b : a))
  el.scrollTo({ left: target.offsetLeft, behavior: 'smooth' })
}

function scrollByRight() {
  const el = tabsBar.value
  if (!el) return
  const tabs = getTabItems()
  const scrollLeft = el.scrollLeft
  const clientWidth = el.clientWidth
  // Find the first tab clipped on the right (right edge exceeds viewport)
  const target = tabs.find(t => t.offsetLeft + t.offsetWidth > scrollLeft + clientWidth)
  if (!target) return
  el.scrollTo({ left: target.offsetLeft + target.offsetWidth - clientWidth, behavior: 'smooth' })
}

let resizeObserver: ResizeObserver | null = null

onMounted(() => {
  const el = tabsBar.value
  if (el) {
    el.addEventListener('scroll', updateScrollBtns)
    resizeObserver = new ResizeObserver(() => {
      void trackActiveTab()
    })
    resizeObserver.observe(el)
    void trackActiveTab()
  }
})

onUnmounted(() => {
  tabsBar.value?.removeEventListener('scroll', updateScrollBtns)
  resizeObserver?.disconnect()
})

watch(
  [() => tabs.value.length, activeTabId, () => tabsStore.openTabRequestSeq],
  () => {
    void trackActiveTab()
  },
)

let dragFromIdx = -1

function dragStart(idx: number) {
  dragFromIdx = idx
}

function dragDrop(toIdx: number) {
  if (dragFromIdx !== -1 && dragFromIdx !== toIdx) {
    tabsStore.reorderTabs(dragFromIdx, toIdx)
  }
  dragFromIdx = -1
}
</script>
