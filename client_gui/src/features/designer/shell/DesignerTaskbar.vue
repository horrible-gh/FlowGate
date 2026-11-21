<template>
  <div ref="root" class="designer-taskbar" :data-taskbar-style="modelessStyle" data-test="designer-taskbar" :aria-label="t('gui.taskbar.label')">
    <button ref="menuTrigger" class="taskbar-item menu-trigger" type="button" :aria-expanded="popup === 'menu'" aria-haspopup="menu" @click="toggle('menu')"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="4" width="6" height="6" rx="1"/><rect x="14" y="4" width="6" height="6" rx="1"/><rect x="4" y="14" width="6" height="6" rx="1"/><rect x="14" y="14" width="6" height="6" rx="1"/></svg>{{ t('gui.taskbar.menu') }}</button>
    <button class="taskbar-item designer-item" type="button" aria-pressed="true"><span class="item-icon">▦</span><span class="item-text"><small>{{ t('gui.taskbar.screen') }}</small><b>{{ t('gui.nav.designer.title') }}</b></span><i /></button>
    <span v-if="minimized.length" class="taskbar-separator" role="separator" />
    <button v-for="entry in visible" :key="entry.id" type="button" class="taskbar-item minimized-item" :style="{ '--kind-color': entry.color }" :title="entry.title" @click="restore(entry)"><span class="item-icon">□</span><span class="item-text"><b>{{ entry.title }}</b></span></button>
    <button v-if="overflow.length" ref="overflowTrigger" type="button" class="taskbar-item overflow-trigger" :aria-expanded="popup === 'overflow'" aria-haspopup="menu" :aria-label="t('gui.taskbar.more', { count: overflow.length })" @click="toggle('overflow')">▾ +{{ overflow.length }}</button>
    <div v-if="popup" ref="popupRoot" role="menu" class="taskbar-popup" :class="{ 'menu-board': popup === 'menu', 'overflow-list': popup === 'overflow' }" @keydown="menuKey">
      <template v-if="popup === 'menu'">
        <template v-for="group in menuGroups" :key="group.key">
          <div class="menu-group-label" :style="{ '--card-color': group.color }"><i aria-hidden="true" />{{ t(group.label) }}</div>
          <div class="menu-group-row" role="group" :aria-label="t(group.label)">
            <button v-for="card in group.cards" :key="card.key" type="button" role="menuitem" class="menu-card" :style="{ '--card-color': group.color }" :data-card="card.key" :data-card-state="cardState(card.key)" @click="openCard(card.key)"><span class="menu-card-heading">{{ card.icon }} {{ t(card.title) }}<i v-if="cardState(card.key) !== 'closed'" class="menu-card-dot" data-test="menu-card-dot" aria-hidden="true" /></span><span class="menu-card-body">{{ t(card.body) }}</span><i class="card-port" /></button>
          </div>
        </template>
      </template>
      <button v-for="entry in popup === 'overflow' ? overflow : []" :key="entry.id" type="button" role="menuitem" :title="entry.title" :style="{ '--kind-color': entry.color }" @click="restore(entry)">{{ entry.title }}</button>
    </div>
    <MyAiWindow :ref="(el) => setWindowRef('my-ai', el)" :open="windows['my-ai'].open" v-model:minimized="windows['my-ai'].minimized" @close="windows['my-ai'].open = false" />
    <QuestionPolicyWindow :ref="(el) => setWindowRef('question-policy', el)" :open="windows['question-policy'].open" v-model:minimized="windows['question-policy'].minimized" @close="windows['question-policy'].open = false" />
    <MeThemeWindow :ref="(el) => setWindowRef('theme', el)" :open="windows.theme.open" v-model:minimized="windows.theme.minimized" @close="windows.theme.open = false" />
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getRequest } from '@shared/api'
import { UI_SETTINGS_PATH, type UiSettingsResponse } from '@shared/aiFinishedCardRetention'
import { useCurrentUser } from '@/core/auth/currentUser'
import { MyAiWindow, QuestionPolicyWindow } from '@/features/ai'
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
// The board lists only what belongs to "me", one group per source screen; cards are added one at a time.
type CardKey = 'my-ai' | 'question-policy' | 'theme'
const menuGroups: { key: string; label: string; color: string; cards: { key: CardKey; icon: string; title: string; body: string }[] }[] = [
  { key: 'ai', label: 'gui.taskbar.group_ai', color: '#a78bfa', cards: [{ key: 'my-ai', icon: '✦', title: 'gui.taskbar.my_ai', body: 'gui.taskbar.my_ai_desc' }, { key: 'question-policy', icon: '✎', title: 'gui.taskbar.question_policy', body: 'gui.taskbar.question_policy_desc' }] },
  { key: 'settings', label: 'gui.taskbar.group_settings', color: '#94a3b8', cards: [{ key: 'theme', icon: '◐', title: 'gui.taskbar.theme', body: 'gui.taskbar.style' }] },
]
const windows = reactive<Record<CardKey, { open: boolean; minimized: boolean }>>({ 'my-ai': { open: false, minimized: false }, 'question-policy': { open: false, minimized: false }, theme: { open: false, minimized: false } })
const windowRefs: Partial<Record<CardKey, { bringToFront?: () => void } | null>> = {}
function setWindowRef(key: CardKey, el: unknown) { windowRefs[key] = el as { bringToFront?: () => void } | null }
function cardState(key: CardKey) { return !windows[key].open ? 'closed' : windows[key].minimized ? 'minimized' : 'open' }
function openCard(key: CardKey) {
  close()
  if (!windows[key].open) { windows[key].open = true; return }
  windows[key].minimized = false
  windowRefs[key]?.bringToFront?.()
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
/* Cards keep the canvas-card size on a column grid instead of stretching across the board; the board is two cards wide. */
.menu-board { left: 10px; width: 392px; max-width: calc(100vw - 48px); max-height: calc(100vh - 140px); overflow-y: auto; min-height: 150px; box-sizing: border-box; background-image: radial-gradient(var(--fg2-line-strong) 1px, transparent 1px); background-size: 16px 16px; }
.menu-group-label { display: flex; align-items: center; gap: 7px; margin: 12px 2px 8px; font-size: 12px; font-weight: 700; color: var(--fg2-text); }
.menu-group-label:first-child { margin-top: 0; }
.menu-group-label i { width: 8px; height: 8px; border-radius: 50%; background: var(--card-color, #94a3b8); flex: none; }
.menu-group-row { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 174px)); gap: 12px; }
.taskbar-popup button { display: block; padding: 12px 16px; border: 1px solid var(--fg2-line-strong); border-left: 4px solid var(--kind-color, #6478dc); border-radius: 6px; background: var(--fg2-panel); color: var(--fg2-text); font: inherit; font-size: 12px; cursor: pointer; text-align: left; }
.taskbar-popup .menu-card { position: relative; display: flex; flex-direction: column; width: 100%; height: 92px; padding: 0; border: 1.5px solid var(--card-color, #94a3b8); border-radius: 7px; box-shadow: 0 6px 18px #0003; }
.menu-card-heading { display: flex; align-items: center; width: 100%; height: 28px; padding: 0 9px; box-sizing: border-box; font-weight: 700; background: color-mix(in srgb, var(--card-color, #94a3b8) 16%, transparent); }
.menu-card-body { padding: 8px 10px 0; line-height: 1.35; color: var(--fg2-muted); }
/* A window that is open or minimized shows as one dot on its card, with no status wording. */
.menu-card-dot { width: 6px; height: 6px; margin-left: auto; border-radius: 50%; background: #28b878; box-shadow: 0 0 6px #28b878; flex: none; }
.card-port { position: absolute; right: -5px; top: 50%; width: 8px; height: 8px; border-radius: 50%; background: var(--fg2-panel); border: 1.5px solid var(--card-color, #94a3b8); }
.overflow-list { right: 10px; width: min(360px, calc(100vw - 48px)); max-height: min(400px, 60vh); overflow: auto; }
.overflow-list button { width: 100%; margin: 4px 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
