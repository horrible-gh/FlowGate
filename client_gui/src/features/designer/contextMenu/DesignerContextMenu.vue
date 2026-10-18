<!-- flowgate.v02.0060 — Designer common context menu (0006-D rev2 §2.1, §6, §7.1): draws the one menu the
     host owns — icon column, label, › for a submenu, separators, danger and disabled looks of the
     approved mockup (MirageGlass 1jnmunhp v14 screen 17) — and handles its own position, one-level
     submenu, keyboard, Escape and close conditions. It runs no command: a chosen leaf goes back
     through host.select and leaves as one `request` (command name + the snapshot taken at open).
     A disabled item stays focusable but never requests; Escape closes only the menu. Not mounted
     anywhere in this step (1단계): the Designer shell installs it in 2-A.
     flowgate.v02.0060 T#1 (WP 0012, Q item 459): the mockup's look — .fg-ctx / .fg-ctx-item / .danger / :disabled /
     .fg-ctx-icon / .fg-ctx-label / .fg-ctx-next / .fg-ctx-line sizes as the mockup renders them, colours derived from
     the theme tokens (no colour literal). A disabled item's reason (Git state, 연결 예정, 미구현) is its tooltip and
     its accessible description. -->
<template>
  <Teleport to="body">
    <div v-if="session" ref="root" data-designer-context-menu
      :data-overlay-focus-owner="session.focusOwner"
      @contextmenu.prevent.stop @pointerdown.stop @mousedown.stop @pointermove.stop @mousemove.stop>
      <div v-for="panel in panels" :key="String(panel.generation) + ': ' + panel.level" :data-level="panel.level"
        :id="panel.level ? submenuId : undefined" class="designer-context-panel" role="menu"
        :aria-label="panel.level && activeSubmenu ? label(activeSubmenu) : t('menu')"
        :style="panel.style" @keydown="onKeydown($event, panel.level, panel.generation)">
        <template v-for="(item, index) in panel.items" :key="item.key">
          <div v-if="item.kind === 'separator'" class="designer-context-separator" role="separator" />
          <template v-else>
            <button type="button" role="menuitem" tabindex="-1" class="designer-context-item"
              :data-key="item.key" :class="{ danger: item.danger }"
              :aria-disabled="!!item.disabled" :aria-haspopup="item.kind === 'submenu' ? 'menu' : undefined"
              :aria-expanded="item.kind === 'submenu' ? subKey === item.key : undefined"
              :aria-controls="item.kind === 'submenu' && subKey === item.key ? submenuId : undefined"
              :title="reason(item)"
              :aria-describedby="item.disabledReason ? reasonId(panel.level, index) : undefined"
              @mouseenter="onHover(item, panel.level, $event, panel.generation)"
              @click="choose(item, $event.currentTarget as HTMLElement, panel.generation)">
              <span class="designer-context-icon" aria-hidden="true">{{ item.icon }}</span>
              <span class="designer-context-label">{{ label(item) }}</span>
              <span v-if="item.kind === 'submenu'" class="designer-context-arrow" aria-hidden="true">›</span>
            </button>
            <span v-if="item.disabledReason" :id="reasonId(panel.level, index)" hidden>{{ reason(item) }}</span>
          </template>
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
const reason = (item: MenuAction | MenuSubmenu) => (item.disabledReason ? t('reasons.' + item.disabledReason) : undefined)
const reasonId = (level: number, index: number) => `designer-context-reason-${session.value?.generation}-${level}-${index}`
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
/* The mockup's menu (flowgate-v02-context-menu-v2.html .fg-ctx*): its sizes as rendered, its colours as theme-token mixes. */
.designer-context-panel {
  position: fixed;
  box-sizing: border-box;
  min-width: min(186px, calc(100vw - 16px));
  max-width: min(236px, calc(100vw - 16px));
  max-height: calc(100vh - 16px);
  overflow-y: auto;
  padding: 5px;
  border: 1px solid color-mix(in srgb, var(--fg2-focus) 35%, var(--fg2-panel));
  border-radius: var(--fg2-control-radius-lg);
  background: color-mix(in srgb, var(--fg2-focus) 13%, var(--fg2-bg));
  box-shadow: 0 14px 38px color-mix(in srgb, var(--fg2-bg) 67%, transparent);
  color: color-mix(in srgb, var(--fg2-text) 53%, var(--fg2-control-fg));
  font-family: var(--fg2-font);
  /* The mockup's `font: 12px/1.4 inherit` is not a valid shorthand, so it renders the 14px it inherits. */
  font-size: 14px;
  line-height: normal;
  user-select: none;
}
.designer-context-item {
  box-sizing: border-box;
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  min-height: 33px;
  padding: 7px 9px;
  border: 0;
  border-radius: 5px;
  background: none;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.designer-context-item:hover, .designer-context-item:focus-visible {
  background: color-mix(in srgb, var(--fg2-focus) 31%, var(--fg2-bg));
  outline: none;
}
.designer-context-item[aria-disabled="true"] { opacity: 0.4; cursor: default; }
.designer-context-item[aria-disabled="true"]:not(.danger):hover { background: none; }
.designer-context-item.danger { color: color-mix(in srgb, var(--fg2-status-danger-fg) 76%, var(--fg2-text)); }
.designer-context-item.danger:hover, .designer-context-item.danger:focus-visible {
  background: color-mix(in srgb, var(--fg2-status-danger-fg) 29%, var(--fg2-bg));
}
.designer-context-icon {
  width: 16px;
  flex: 0 0 16px;
  text-align: center;
  color: color-mix(in srgb, var(--fg2-control-fg) 36%, var(--fg2-muted));
  font-size: 13px;
}
.designer-context-label { flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.designer-context-arrow { color: color-mix(in srgb, var(--fg2-muted) 84%, var(--fg2-focus)); }
.designer-context-separator {
  height: 1px;
  margin: 5px;
  background: color-mix(in srgb, var(--fg2-muted-2) 45%, var(--fg2-panel));
}
</style>
