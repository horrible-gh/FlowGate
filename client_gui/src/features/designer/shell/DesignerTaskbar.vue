<template>
  <div ref="root" class="designer-taskbar" :data-taskbar-style="modelessStyle" data-test="designer-taskbar" :aria-label="t('gui.taskbar.label')">
    <button ref="menuTrigger" class="taskbar-item menu-trigger" type="button" :aria-expanded="popup === 'menu'" aria-haspopup="menu" @click="toggle('menu')"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="4" width="6" height="6" rx="1"/><rect x="14" y="4" width="6" height="6" rx="1"/><rect x="4" y="14" width="6" height="6" rx="1"/><rect x="14" y="14" width="6" height="6" rx="1"/></svg>{{ t('gui.taskbar.menu') }}</button>
    <button class="taskbar-item designer-item" type="button" aria-pressed="true"><span class="item-icon">▦</span><span class="item-text"><small>{{ t('gui.taskbar.screen') }}</small><b>{{ t('gui.nav.designer.title') }}</b></span><i /></button>
    <span v-if="minimized.length" class="taskbar-separator" role="separator" />
    <button v-for="entry in visible" :key="entry.id" type="button" class="taskbar-item minimized-item" :style="{ '--kind-color': entry.color }" :title="entry.title" @click="restore(entry)"><span class="item-icon">□</span><span class="item-text"><b>{{ entry.title }}</b></span></button>
    <button v-if="overflow.length" ref="overflowTrigger" type="button" class="taskbar-item overflow-trigger" :aria-expanded="popup === 'overflow'" aria-haspopup="menu" :aria-label="t('gui.taskbar.more', { count: overflow.length })" @click="toggle('overflow')">▾ +{{ overflow.length }}</button>
    <div v-if="popup" ref="popupRoot" role="menu" class="taskbar-popup" :class="{ 'menu-board': popup === 'menu', 'overflow-list': popup === 'overflow' }" @keydown="menuKey">
      <button v-if="popup === 'menu'" type="button" role="menuitem" class="menu-card" data-card="theme" :data-card-state="themeState" @click="openTheme"><span class="menu-card-heading">◐ {{ t('gui.taskbar.theme') }}<i v-if="themeState !== 'closed'" class="menu-card-dot" data-test="menu-card-dot" aria-hidden="true" /></span><span class="menu-card-body">{{ t('gui.taskbar.style') }}</span><i class="card-port" /></button>
      <button v-for="entry in popup === 'overflow' ? overflow : []" :key="entry.id" type="button" role="menuitem" :title="entry.title" :style="{ '--kind-color': entry.color }" @click="restore(entry)">{{ entry.title }}</button>
    </div>
    <MeThemeWindow ref="themeWindow" :open="themeOpen" v-model:minimized="themeMinimized" @close="themeOpen = false" />
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getRequest } from '@shared/api'
import { UI_SETTINGS_PATH, type UiSettingsResponse } from '@shared/aiFinishedCardRetention'
import { useCurrentUser } from '@/core/auth/currentUser'
import MeThemeWindow from '@/features/settings/windows/MeThemeWindow.vue'
import { modelessWindows, modelessStyle, normalizeTaskbarStyle, TASKBAR_STYLE_FIELD, setModelessTaskbar, removeModelessTaskbar, taskbarCapacity, type ModelessEntry } from '@/core/overlay/modelessRegistry'

const { t } = useI18n()
const { user } = useCurrentUser()
const root = ref<HTMLElement | null>(null)
const menuTrigger = ref<HTMLButtonElement | null>(null)
const overflowTrigger = ref<HTMLButtonElement | null>(null)
const popupRoot = ref<HTMLElement | null>(null)
const width = ref(0)
const popup = ref<'menu' | 'overflow' | null>(null)
const id = Symbol('taskbar')
const minimized = computed(() => modelessWindows.value.filter(entry => entry.minimized))
const capacity = computed(() => taskbarCapacity(width.value, minimized.value.length))
const visible = computed(() => minimized.value.slice(0, capacity.value))
const overflow = computed(() => minimized.value.slice(capacity.value))
watch(overflow, (entries) => { if (!entries.length && popup.value === 'overflow') close() })

function close(focus = false) {
  const trigger = popup.value === 'menu' ? menuTrigger.value : overflowTrigger.value
  popup.value = null
  if (focus) trigger?.focus()
}
async function toggle(which: 'menu' | 'overflow') {
  if (popup.value === which) { close(true); return }
  popup.value = which
  await nextTick()
  popupRoot.value?.querySelector<HTMLButtonElement>('button')?.focus()
}
function restore(entry: ModelessEntry) { close(); entry.restore() }
// Menu cards open a window over the Designer instead of leaving the page. A card whose window
// is already open (or minimized) brings that window back rather than opening a second one.
const themeWindow = ref<InstanceType<typeof MeThemeWindow> | null>(null)
const themeOpen = ref(false)
const themeMinimized = ref(false)
const themeState = computed(() => !themeOpen.value ? 'closed' : themeMinimized.value ? 'minimized' : 'open')
function openTheme() {
  close()
  if (!themeOpen.value) { themeOpen.value = true; return }
  themeMinimized.value = false
  themeWindow.value?.bringToFront()
}
function outside(event: Event) {
  if (event.target instanceof Node && !root.value?.contains(event.target)) close()
}
function escape(event: KeyboardEvent) {
  if (popup.value && event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(true) }
}
function menuKey(event: KeyboardEvent) {
  const buttons = [...(popupRoot.value?.querySelectorAll<HTMLButtonElement>('button') ?? [])]
  const current = buttons.indexOf(document.activeElement as HTMLButtonElement)
  let index: number
  if (event.key === 'ArrowDown') index = (current + 1) % buttons.length
  else if (event.key === 'ArrowUp') index = (current - 1 + buttons.length) % buttons.length
  else if (event.key === 'Home') index = 0
  else if (event.key === 'End') index = buttons.length - 1
  else return
  event.preventDefault()
  buttons[index]?.focus()
}
let observer: ResizeObserver | null = null
function measure() {
  width.value = root.value?.clientWidth ?? 0
  setModelessTaskbar(id, root.value?.getBoundingClientRect().height || 50)
}
let readSeq = 0
watch(user, async () => {
  const seq = ++readSeq
  modelessStyle.value = 'flowgate'
  try {
    const { data } = await getRequest<UiSettingsResponse>(UI_SETTINGS_PATH)
    if (seq === readSeq) modelessStyle.value = normalizeTaskbarStyle(data.settings?.[TASKBAR_STYLE_FIELD])
  } catch { /* The taskbar remains usable with the default if the settings read fails. */ }
}, { immediate: true })
onMounted(() => {
  measure()
  if (typeof ResizeObserver !== 'undefined') { observer = new ResizeObserver(measure); observer.observe(root.value!) }
  window.addEventListener('resize', measure)
  document.addEventListener('pointerdown', outside)
  document.addEventListener('focusin', outside)
  document.addEventListener('keydown', escape, true)
})
onBeforeUnmount(() => {
  readSeq++
  observer?.disconnect()
  removeModelessTaskbar(id)
  window.removeEventListener('resize', measure)
  document.removeEventListener('pointerdown', outside)
  document.removeEventListener('focusin', outside)
  document.removeEventListener('keydown', escape, true)
})
</script>

<style scoped>
.designer-taskbar { position: relative; display: flex; align-items: center; gap: 6px; height: 50px; flex: 0 0 50px; box-sizing: border-box; padding: 6px 10px; border-top: 1px solid var(--fg2-line-strong); background: var(--fg2-panel); color: var(--fg2-text); }
.taskbar-item { height: 40px; flex: none; box-sizing: border-box; border: 1px solid var(--fg2-line-strong); border-left: 4px solid var(--kind-color, #6478dc); border-radius: 6px; background: var(--fg2-panel); color: inherit; padding: 0 12px; font: inherit; font-size: 12px; cursor: pointer; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; text-align: left; }
.taskbar-item:hover, .taskbar-popup button:hover { background: var(--fg2-bg); }
button:focus-visible { outline: 2px solid var(--fg2-accent, #6478dc); outline-offset: 2px; }
.menu-trigger { width: 80px; height: 38px; display: flex; align-items: center; gap: 7px; border: 1px dashed var(--fg2-accent, #3b82f6); border-radius: 8px; background: color-mix(in srgb, var(--fg2-accent, #3b82f6) 10%, var(--fg2-panel)); }
.menu-trigger svg { width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 1.8; flex: none; }
.designer-item { width: 130px; border-color: #4ba3ff; display: flex; align-items: center; gap: 8px; background: linear-gradient(90deg, color-mix(in srgb, #4ba3ff 16%, var(--fg2-panel)), var(--fg2-panel) 70%); box-shadow: 0 0 0 1px #4ba3ff40; }
.designer-item i { width: 6px; height: 6px; border-radius: 50%; background: #28b878; box-shadow: 0 0 6px #28b878; flex: none; margin-left: auto; }
.taskbar-separator { width: 1px; height: 24px; background: var(--fg2-line-strong); margin: 0 4px; flex: none; }
.minimized-item { width: 220px; border-style: dashed; border-left-style: solid; display: flex; align-items: center; gap: 8px; background: transparent; }
.item-icon { flex: none; display: grid; place-items: center; width: 22px; height: 22px; border-radius: 50%; background: color-mix(in srgb, var(--kind-color, #4ba3ff) 20%, var(--fg2-panel)); color: var(--kind-color, #4ba3ff); }
.item-text { display: flex; flex-direction: column; min-width: 0; line-height: 1.2; }
.item-text small { font-size: 9.5px; font-weight: 700; color: var(--kind-color, #4ba3ff); }
.item-text b { font-size: 12px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.overflow-trigger { width: 74px; }
.taskbar-popup { position: absolute; bottom: calc(100% + 8px); z-index: 100000; padding: 14px; border: 1px solid var(--fg2-line-strong); border-radius: 10px; background-color: var(--fg2-panel); box-shadow: var(--fg2-overlay-shadow); }
/* Cards keep the canvas-card size on a column grid instead of stretching across the board. */
.menu-board { left: 10px; width: 360px; max-width: calc(100vw - 48px); min-height: 150px; box-sizing: border-box; display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 174px)); gap: 12px; align-content: start; background-image: radial-gradient(var(--fg2-line-strong) 1px, transparent 1px); background-size: 16px 16px; }
.taskbar-popup button { display: block; padding: 12px 16px; border: 1px solid var(--fg2-line-strong); border-left: 4px solid var(--kind-color, #6478dc); border-radius: 6px; background: var(--fg2-panel); color: var(--fg2-text); font: inherit; font-size: 12px; cursor: pointer; text-align: left; }
.taskbar-popup .menu-card { position: relative; display: flex; flex-direction: column; width: 100%; height: 92px; padding: 0; border: 1.5px solid #94a3b8; border-radius: 7px; box-shadow: 0 6px 18px #0003; }
.menu-card-heading { display: flex; align-items: center; width: 100%; height: 28px; padding: 0 9px; box-sizing: border-box; font-weight: 700; background: color-mix(in srgb, #94a3b8 16%, transparent); }
.menu-card-body { padding: 8px 10px 0; line-height: 1.35; color: var(--fg2-muted); }
/* A window that is open or minimized shows as one dot on its card, with no status wording. */
.menu-card-dot { width: 6px; height: 6px; margin-left: auto; border-radius: 50%; background: #28b878; box-shadow: 0 0 6px #28b878; flex: none; }
.card-port { position: absolute; right: -5px; top: 50%; width: 8px; height: 8px; border-radius: 50%; background: var(--fg2-panel); border: 1.5px solid #94a3b8; }
.overflow-list { right: 10px; width: min(360px, calc(100vw - 48px)); max-height: min(400px, 60vh); overflow: auto; }
.overflow-list button { width: 100%; margin: 4px 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
