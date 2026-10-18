<!-- flowgate.v02.0060 — Designer common context menu (0006-D rev2 §2.1, §6, §7.1): draws the one menu the
     host owns — icon column, label, › for a submenu, separators, danger and disabled looks of the
     approved mockup (MirageGlass 1jnmunhp v14 screen 17) — and handles its own position, one-level
     submenu, keyboard, Escape and close conditions. It runs no command: a chosen leaf goes back
     through host.select and leaves as one `request` (command name + the snapshot taken at open).
     A disabled item stays focusable but never requests; Escape closes only the menu. Not mounted
     anywhere in this step (1단계): the Designer shell installs it in 2-A. -->
<template>
  <Teleport to="body">
    <div v-if="session" ref="root" data-designer-context-menu
      :data-overlay-focus-owner="session.focusOwner"
      @contextmenu.prevent.stop @pointerdown.stop @mousedown.stop @pointermove.stop @mousemove.stop>
      <div v-for="panel in panels" :key="String(panel.generation) + ': ' + panel.level" :data-level="panel.level"
        :id="panel.level ? submenuId : undefined" class="designer-context-panel" role="menu"
        :aria-label="panel.level && activeSubmenu ? label(activeSubmenu) : t('menu')"
        :style="panel.style" @keydown="onKeydown($event, panel.level, panel.generation)">
        <template v-for="item in panel.items" :key="item.key">
          <div v-if="item.kind === 'separator'" class="designer-context-separator" role="separator" />
          <button v-else type="button" role="menuitem" tabindex="-1" class="designer-context-item"
            :data-key="item.key" :class="{ danger: item.danger }"
            :aria-disabled="!!item.disabled" :aria-haspopup="item.kind === 'submenu' ? 'menu' : undefined"
            :aria-expanded="item.kind === 'submenu' ? subKey === item.key : undefined"
            :aria-controls="item.kind === 'submenu' && subKey === item.key ? submenuId : undefined"
            :title="item.disabledReason ? t('reasons.' + item.disabledReason) : undefined"
            @mouseenter="onHover(item, panel.level, $event, panel.generation)"
            @click="choose(item, $event.currentTarget as HTMLElement, panel.generation)">
            <span class="designer-context-icon" aria-hidden="true">{{ item.icon }}</span>
            <span>{{ label(item) }}</span>
            <span v-if="item.kind === 'submenu'" class="designer-context-arrow" aria-hidden="true">›</span>
          </button>
        </template>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { DesignerMenuHost } from './host'
import type { CommandRequest, MenuAction, MenuItem, MenuSubmenu } from './types'
import { contextMenuMessages } from './messages'
import { menuPosition, submenuPosition } from './position'
import { isRelayedPress } from './gesture'

const props = defineProps<{ host: DesignerMenuHost }>()
const emit = defineEmits<{ request: [request: CommandRequest]; open: [generation: number]; close: [generation: number] }>()
const { t } = useI18n({ useScope: 'local', messages: contextMenuMessages, inheritLocale: true })
const root = ref<HTMLElement | null>(null)
const session = computed(() => props.host.current.value)
const subKey = ref<string | null>(null)
const mainPosition = ref({ x: 0, y: 0 })
const subPosition = ref({ x: 0, y: 0 })
const activeSubmenu = computed(() => {
  const item = session.value?.model.items.find((entry) => entry.key === subKey.value)
  return item?.kind === 'submenu' ? item : null
})
const submenuId = computed(() => 'designer-context-sub-' + session.value?.generation)
const styleAt = (position: { x: number; y: number }) => ({
  left: position.x + 'px', top: position.y + 'px', zIndex: session.value?.zIndex,
})
const panels = computed(() => [
  { level: 0, generation: session.value?.generation, items: session.value?.model.items ?? [], style: styleAt(mainPosition.value) },
  ...(activeSubmenu.value ? [{ level: 1, generation: session.value?.generation, items: activeSubmenu.value.children, style: styleAt(subPosition.value) }] : []),
])
const label = (item: MenuAction | MenuSubmenu) => item.label ?? t(item.labelKey ?? '')
const viewport = () => ({ width: window.innerWidth, height: window.innerHeight })
const panelElement = (level: number) => root.value?.querySelector<HTMLElement>('[data-level="' + level + '"]')
const buttons = (level: number) => Array.from(panelElement(level)?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? [])
function focusFirst(level: number) {
  const list = buttons(level)
  ;(list.find((item) => item.getAttribute('aria-disabled') !== 'true') ?? list[0])?.focus()
}
watch(() => session.value?.generation, async (generation, previous) => {
  if (previous !== undefined) emit('close', previous)
  subKey.value = null
  if (generation === undefined || !session.value) return
  mainPosition.value = { ...session.value.position }
  emit('open', generation)
  await nextTick()
  if (session.value?.generation !== generation) return
  const rect = panelElement(0)?.getBoundingClientRect()
  if (rect) mainPosition.value = menuPosition(session.value.position, rect, viewport())
  focusFirst(0)
}, { immediate: true, flush: 'post' })

async function openSubmenu(item: MenuSubmenu, element: HTMLElement, focus: boolean) {
  if (item.disabled) return
  const generation = session.value?.generation
  subKey.value = item.key
  const rect = element.getBoundingClientRect()
  subPosition.value = { x: rect.right + 4, y: rect.top }
  await nextTick()
  if (session.value?.generation !== generation || subKey.value !== item.key) return
  const size = panelElement(1)?.getBoundingClientRect()
  if (size) subPosition.value = submenuPosition(rect, size, viewport())
  if (focus) focusFirst(1)
}
function closeSubmenu(focus = false) {
  const key = subKey.value
  subKey.value = null
  if (focus) buttons(0).find((button) => button.dataset.key === key)?.focus()
}
function onHover(item: MenuItem, level: number, event: MouseEvent, generation: number | undefined) {
  if (generation !== session.value?.generation) return
  if (level !== 0 || item.kind === 'separator') return
  if (item.kind === 'submenu' && !item.disabled) void openSubmenu(item, event.currentTarget as HTMLElement, false)
  else closeSubmenu()
}
function choose(item: MenuItem, element: HTMLElement, generation: number | undefined) {
  if (generation !== session.value?.generation) return
  if (generation === undefined || item.kind === 'separator' || item.disabled) return
  if (item.kind === 'submenu') { void openSubmenu(item, element, true); return }
  const request = props.host.select(item.key, generation)
  if (request) emit('request', request)
}
function onKeydown(event: KeyboardEvent, level: number, generation: number | undefined) {
  if (generation !== session.value?.generation) return
  const list = buttons(level)
  const at = list.indexOf(document.activeElement as HTMLElement)
  const item = panels.value[level]?.items.find((entry) => entry.key === list[at]?.dataset.key)
  if (['ArrowDown', 'ArrowUp', 'Home', 'End', 'ArrowLeft', 'ArrowRight', 'Enter', ' '].includes(event.key)) {
    event.preventDefault()
    event.stopPropagation()
  }
  if (event.key === 'ArrowDown') list[(at + 1 + list.length) % list.length]?.focus()
  else if (event.key === 'ArrowUp') list[(at - 1 + list.length) % list.length]?.focus()
  else if (event.key === 'Home') list[0]?.focus()
  else if (event.key === 'End') list[list.length - 1]?.focus()
  else if (event.key === 'ArrowLeft' && level === 1) closeSubmenu(true)
  else if (event.key === 'ArrowRight' && item?.kind === 'submenu') void openSubmenu(item, list[at], true)
  else if ((event.key === 'Enter' || event.key === ' ') && item) choose(item, list[at], generation)
  else if (event.key === 'Tab') props.host.close()
}
function inside(event: Event) { return event.target instanceof Node && !!root.value?.contains(event.target) }
function onOutside(event: Event) {
  if (session.value && !isRelayedPress(event) && !inside(event)) props.host.close()
}
function onEscape(event: KeyboardEvent) {
  if (!session.value || event.key !== 'Escape') return
  event.preventDefault()
  event.stopImmediatePropagation()
  props.host.close()
}
function onResize() { props.host.close() }
onMounted(() => {
  document.addEventListener('pointerdown', onOutside, true)
  document.addEventListener('mousedown', onOutside, true)
  document.addEventListener('contextmenu', onOutside, true)
  document.addEventListener('scroll', onOutside, true)
  document.addEventListener('keydown', onEscape, true)
  window.addEventListener('resize', onResize)
})
onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onOutside, true)
  document.removeEventListener('mousedown', onOutside, true)
  document.removeEventListener('contextmenu', onOutside, true)
  document.removeEventListener('scroll', onOutside, true)
  document.removeEventListener('keydown', onEscape, true)
  window.removeEventListener('resize', onResize)
  props.host.close()
})
</script>

<style scoped>
.designer-context-panel {
  position: fixed;
  box-sizing: border-box;
  min-width: min(190px, calc(100vw - 16px));
  max-width: calc(100vw - 16px);
  max-height: calc(100vh - 16px);
  overflow-y: auto;
  padding: 6px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius);
  background: var(--fg2-control-surface-strong);
  color: var(--fg2-control-fg);
  font-family: var(--fg2-font);
  font-size: var(--fg2-control-font-size);
  box-shadow: var(--fg2-overlay-shadow);
}
.designer-context-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 7px 10px;
  border: 0;
  border-radius: var(--fg2-control-radius-sm);
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.designer-context-item:hover, .designer-context-item:focus-visible {
  background: var(--fg2-control-surface);
  outline: 1px solid var(--fg2-line-strong);
}
.designer-context-item.danger { color: var(--fg2-status-danger-fg); }
.designer-context-item.danger:hover, .designer-context-item.danger:focus-visible { background: var(--fg2-status-danger-bg); }
.designer-context-item[aria-disabled="true"] { opacity: var(--fg2-control-disabled-opacity-strong); cursor: default; }
.designer-context-item[aria-disabled="true"]:hover { background: transparent; outline: none; }
.designer-context-icon { width: 18px; flex: 0 0 18px; text-align: center; }
.designer-context-arrow { margin-left: auto; }
.designer-context-separator { border-top: 1px solid var(--fg2-line); margin: 5px 2px; }
</style>

