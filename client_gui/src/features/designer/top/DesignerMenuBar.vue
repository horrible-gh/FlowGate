<template>
  <!-- flowgate.v02.0038 — Designer Menu Bar (0001-R §3): 파일 | 편집 | 실행 | 보기 | 창 | 도움말,
       no AI menu and no Save. A menu opens on click; while one is open, hovering another title
       switches to it. A choice, Escape or a press outside closes it. Arrow keys move between
       menus and items. Items emit commands (or the panel / tab / density / recent events) — the
       Shell connects what already has a target, the rest is the boundary for later Features.
       No shortcut is shown next to any item (0007-T §7). flowgate.v02.0039 T#3 / v02.0043: item
       availability follows the `targets` the Shell reports (Project context, chosen Group, open
       Flow — designerTop.ts isMenuCommandEnabled / isSubmenuEnabled); the Window menu, Help and
       the menu titles themselves always open. -->
  <nav
    ref="root"
    class="designer-menubar"
    role="menubar"
    :aria-label="t('gui.designer.top.menubar_label')"
    data-test="designer-menubar"
  >
    <div v-for="menu in DESIGNER_MENUS" :key="menu.key" class="designer-menu-wrap">
      <button
        :ref="(el) => setTitleRef(menu.key, el)"
        type="button"
        role="menuitem"
        class="designer-menu-title"
        :class="{ 'is-open': openKey === menu.key }"
        aria-haspopup="menu"
        :aria-expanded="openKey === menu.key ? 'true' : 'false'"
        data-test="designer-menu"
        :data-menu="menu.key"
        @click="toggle(menu.key)"
        @mouseenter="onTitleHover(menu.key)"
        @keydown="onTitleKeydown($event, menu.key)"
      >
        {{ t(`gui.designer.top.menus.${menu.key}`) }}
      </button>

      <div
        v-if="openKey === menu.key"
        class="designer-menu-pop"
        role="menu"
        :aria-label="t(`gui.designer.top.menus.${menu.key}`)"
        data-test="designer-menu-pop"
        :data-menu="menu.key"
        @keydown="onMenuKeydown($event, menu.key)"
      >
        <template v-for="(entry, index) in menu.entries" :key="`${menu.key}-${index}`">
          <div v-if="entry.kind === 'separator'" class="designer-menu-sep" role="separator" />

          <button
            v-else-if="entry.kind === 'command'"
            type="button"
            role="menuitem"
            class="designer-menu-item"
            :disabled="!isMenuCommandEnabled(entry.command, runMode, targets)"
            data-test="designer-menu-item"
            :data-command="entry.command"
            @click="choose(() => emit('command', entry.command))"
          >
            <span class="designer-menu-mark" aria-hidden="true" />{{ t(`gui.designer.top.items.${entry.label}`) }}
          </button>

          <button
            v-else-if="entry.kind === 'panel'"
            type="button"
            role="menuitemcheckbox"
            class="designer-menu-item"
            :aria-checked="windowState.panels[entry.panel] ? 'true' : 'false'"
            data-test="designer-menu-panel"
            :data-panel="entry.panel"
            @click="choose(() => emit('toggle-panel', entry.panel))"
          >
            <span class="designer-menu-mark" aria-hidden="true">{{ windowState.panels[entry.panel] ? '✓' : '' }}</span>{{ t(`gui.designer.top.panels.${entry.panel}`) }}
          </button>

          <button
            v-else-if="entry.kind === 'tab'"
            type="button"
            role="menuitemradio"
            class="designer-menu-item"
            :aria-checked="windowState.activeTabs.includes(entry.tab) ? 'true' : 'false'"
            data-test="designer-menu-tab"
            :data-tab="entry.tab"
            @click="choose(() => emit('show-tab', entry.tab))"
          >
            <span class="designer-menu-mark" aria-hidden="true">{{ windowState.activeTabs.includes(entry.tab) ? '●' : '' }}</span>{{ t(`gui.designer.top.tabs.${entry.tab}`) }}
          </button>

          <div
            v-else
            class="designer-menu-subwrap"
            @mouseenter="openSub = isSubmenuEnabled(entry, targets) ? subKey(entry) : null"
            @mouseleave="openSub = null"
          >
            <button
              type="button"
              role="menuitem"
              class="designer-menu-item"
              aria-haspopup="menu"
              :aria-expanded="openSub === subKey(entry) ? 'true' : 'false'"
              :disabled="!isSubmenuEnabled(entry, targets)"
              data-test="designer-menu-sub"
              :data-sub="subKey(entry)"
              @click="openSubmenu(subKey(entry), $event)"
              @keydown.right.prevent.stop="openSubmenu(subKey(entry), $event)"
            >
              <span class="designer-menu-mark" aria-hidden="true" />{{ t(`gui.designer.top.items.${entry.label}`) }}
              <span class="designer-menu-arrow" aria-hidden="true">›</span>
            </button>

            <div
              v-if="openSub === subKey(entry)"
              class="designer-menu-pop is-sub"
              role="menu"
              :aria-label="t(`gui.designer.top.items.${entry.label}`)"
              data-test="designer-menu-subpop"
              :data-sub="subKey(entry)"
              @keydown.left.prevent.stop="closeSubmenu"
            >
              <template v-if="entry.kind === 'density'">
                <button
                  v-for="level in DESIGNER_DENSITIES"
                  :key="level"
                  type="button"
                  role="menuitemradio"
                  class="designer-menu-item"
                  :aria-checked="density === level ? 'true' : 'false'"
                  data-test="designer-menu-density"
                  :data-density="level"
                  @click="choose(() => emit('update:density', level))"
                >
                  <span class="designer-menu-mark" aria-hidden="true">{{ density === level ? '✓' : '' }}</span>{{ t(`gui.designer.top.densities.${level}`) }}
                </button>
              </template>
              <template v-else-if="recent[entry.recent].length">
                <button
                  v-for="recentItem in recent[entry.recent]"
                  :key="recentItem.id"
                  type="button"
                  role="menuitem"
                  class="designer-menu-item"
                  data-test="designer-menu-recent"
                  :data-id="recentItem.id"
                  @click="choose(() => emit('open-recent', { kind: entry.recent, id: recentItem.id }))"
                >
                  <span class="designer-menu-mark" aria-hidden="true" />{{ recentItem.name }}
                </button>
              </template>
              <button v-else type="button" role="menuitem" class="designer-menu-item" disabled data-test="designer-menu-recent-empty">
                <span class="designer-menu-mark" aria-hidden="true" />{{ t('gui.designer.top.items.recent_empty') }}
              </button>
            </div>
          </div>
        </template>
      </div>
    </div>
  </nav>
</template>

<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, type ComponentPublicInstance } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  ALL_MENU_TARGETS,
  DESIGNER_DENSITIES,
  DESIGNER_MENUS,
  EMPTY_RECENT_LISTS,
  isMenuCommandEnabled,
  isSubmenuEnabled,
  type DesignerDensity,
  type DesignerMenuEntry,
  type DesignerMenuKey,
  type DesignerMenuTargets,
  type DesignerRecentLists,
  type DesignerRecentRequest,
  type DesignerRunMode,
  type DesignerTopCommand,
  type DesignerWindowPanel,
  type DesignerWindowState,
  type DesignerWindowTab,
} from './designerTop'

withDefaults(
  defineProps<{
    runMode: DesignerRunMode
    density: DesignerDensity
    windowState: DesignerWindowState
    /** Recent Project / Group / Flow entries, fed by the Shell (0043). */
    recent?: DesignerRecentLists
    /** Project context / chosen Group / open Flow (0039 T#3, 0042, 0043). All true by default. */
    targets?: DesignerMenuTargets
  }>(),
  { recent: () => EMPTY_RECENT_LISTS, targets: () => ALL_MENU_TARGETS },
)

const emit = defineEmits<{
  command: [command: DesignerTopCommand]
  'toggle-panel': [panel: DesignerWindowPanel]
  'show-tab': [tab: DesignerWindowTab]
  'update:density': [density: DesignerDensity]
  'open-recent': [request: DesignerRecentRequest]
}>()

const { t } = useI18n()

const root = ref<HTMLElement | null>(null)
const openKey = ref<DesignerMenuKey | null>(null)
const openSub = ref<string | null>(null)

const titles = new Map<DesignerMenuKey, HTMLElement>()
function setTitleRef(key: DesignerMenuKey, el: Element | ComponentPublicInstance | null) {
  if (el instanceof HTMLElement) titles.set(key, el)
  else titles.delete(key)
}

const subKey = (entry: DesignerMenuEntry) => (entry.kind === 'recent' ? `recent-${entry.recent}` : 'density')

function open(key: DesignerMenuKey) {
  openKey.value = key
  openSub.value = null
}

function closeAll() {
  openKey.value = null
  openSub.value = null
}

function toggle(key: DesignerMenuKey) {
  if (openKey.value === key) closeAll()
  else open(key)
}

function onTitleHover(key: DesignerMenuKey) {
  if (openKey.value !== null && openKey.value !== key) open(key)
}

function choose(action: () => void) {
  closeAll()
  action()
}

// --- keyboard ---------------------------------------------------------------------------------

const MENU_KEYS = DESIGNER_MENUS.map((menu) => menu.key)

function neighbour(key: DesignerMenuKey, step: number): DesignerMenuKey {
  const index = MENU_KEYS.indexOf(key)
  return MENU_KEYS[(index + step + MENU_KEYS.length) % MENU_KEYS.length]
}

/** Enabled items that belong to `menu` itself (not to a submenu inside it). */
function itemsOf(menu: Element): HTMLButtonElement[] {
  return [...menu.querySelectorAll<HTMLButtonElement>('.designer-menu-item')].filter(
    (el) => !el.disabled && el.closest('[role="menu"]') === menu,
  )
}

async function focusFirstItem(key: DesignerMenuKey) {
  await nextTick()
  const menu = root.value?.querySelector(`.designer-menu-pop[data-menu="${key}"]`)
  if (menu) itemsOf(menu)[0]?.focus()
}

function moveTo(key: DesignerMenuKey, withMenu: boolean) {
  titles.get(key)?.focus()
  if (withMenu) {
    open(key)
    void focusFirstItem(key)
  }
}

function onTitleKeydown(event: KeyboardEvent, key: DesignerMenuKey) {
  if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
    event.preventDefault()
    moveTo(neighbour(key, event.key === 'ArrowRight' ? 1 : -1), openKey.value !== null)
  } else if (event.key === 'ArrowDown') {
    event.preventDefault()
    open(key)
    void focusFirstItem(key)
  } else if (event.key === 'Escape') {
    closeAll()
  }
}

function onMenuKeydown(event: KeyboardEvent, key: DesignerMenuKey) {
  const menu = (event.target as Element).closest('[role="menu"]')
  if (!menu) return
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault()
    const items = itemsOf(menu)
    if (!items.length) return
    const at = items.indexOf(event.target as HTMLButtonElement)
    const step = event.key === 'ArrowDown' ? 1 : -1
    items[(at + step + items.length) % items.length].focus()
  } else if (event.key === 'Escape') {
    event.preventDefault()
    closeAll()
    titles.get(key)?.focus()
  } else if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
    event.preventDefault()
    moveTo(neighbour(key, event.key === 'ArrowRight' ? 1 : -1), true)
  }
}

async function openSubmenu(sub: string, event: Event) {
  openSub.value = sub
  if (!(event instanceof KeyboardEvent)) return
  await nextTick()
  const menu = root.value?.querySelector(`.designer-menu-pop.is-sub[data-sub="${sub}"]`)
  if (menu) itemsOf(menu)[0]?.focus()
}

function closeSubmenu() {
  const sub = openSub.value
  openSub.value = null
  root.value?.querySelector<HTMLElement>(`[data-test="designer-menu-sub"][data-sub="${sub}"]`)?.focus()
}

function onDocumentPointerDown(event: Event) {
  if (openKey.value !== null && root.value && !root.value.contains(event.target as Node)) closeAll()
}

onMounted(() => document.addEventListener('pointerdown', onDocumentPointerDown))
onBeforeUnmount(() => document.removeEventListener('pointerdown', onDocumentPointerDown))
</script>

<style scoped>
.designer-menubar {
  display: flex;
  align-items: center;
  gap: 2px;
  height: 100%;
  min-width: 0;
  padding: 0 14px;
  box-sizing: border-box;
  border-bottom: 1px solid var(--fg2-line);
  background: #0b182a;
}

.designer-menu-wrap {
  position: relative;
  display: flex;
  align-items: center;
  height: 100%;
}

.designer-menu-title {
  height: 28px;
  padding: 0 12px;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: #ced9e7;
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}

.designer-menu-title:hover,
.designer-menu-title.is-open {
  background: #152945;
  color: #fff;
}

.designer-menu-pop {
  position: absolute;
  top: 30px;
  left: 0;
  z-index: 10;
  min-width: 220px;
  padding: 6px;
  box-sizing: border-box;
  border: 1px solid #31516f;
  border-radius: 8px;
  background: #0c1a2b;
  box-shadow: 0 14px 34px rgba(0, 0, 0, 0.6);
}

.designer-menu-pop.is-sub {
  top: -6px;
  left: 100%;
  min-width: 210px;
}

.designer-menu-item {
  display: flex;
  align-items: center;
  gap: 9px;
  width: 100%;
  height: 31px;
  padding: 0 9px;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: #b9c9d9;
  font: inherit;
  font-size: 12px;
  text-align: left;
  white-space: nowrap;
  cursor: pointer;
}

.designer-menu-item:hover:not(:disabled),
.designer-menu-item:focus-visible {
  outline: 0;
  background: #15304d;
  color: #fff;
}

.designer-menu-item:disabled {
  color: #50677e;
  cursor: default;
  opacity: 0.72;
}

.designer-menu-mark {
  flex: none;
  width: 13px;
  color: #76b9ff;
  text-align: center;
}

.designer-menu-arrow {
  margin-left: auto;
  color: #7089a2;
}

.designer-menu-sep {
  height: 1px;
  margin: 5px 3px;
  background: #20364e;
}

.designer-menu-subwrap {
  position: relative;
}

.designer-menu-title:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 1px;
}
</style>
