<template>
  <div ref="root" class="designer-taskbar" :data-taskbar-style="modelessStyle" data-test="designer-taskbar" :aria-label="t('gui.taskbar.label')">
    <div class="taskbar-group">
    <button ref="menuTrigger" class="taskbar-item menu-trigger" type="button" :aria-expanded="popup === 'menu'" aria-haspopup="menu" :title="t('gui.taskbar.menu')" @click="toggle('menu')"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="4" width="6" height="6" rx="1"/><rect x="14" y="4" width="6" height="6" rx="1"/><rect x="4" y="14" width="6" height="6" rx="1"/><rect x="14" y="14" width="6" height="6" rx="1"/></svg><span class="item-label">{{ t('gui.taskbar.menu') }}</span></button>
    <button class="taskbar-item designer-item" type="button" aria-pressed="true" :title="t('gui.nav.designer.title')"><span class="item-icon">▦</span><span class="item-text"><small>{{ t('gui.taskbar.screen') }}</small><b>{{ t('gui.nav.designer.title') }}</b></span><i /></button>
    <span v-if="minimized.length" class="taskbar-separator" role="separator" />
    <button v-for="entry in visible" :key="entry.id" type="button" class="taskbar-item minimized-item" :style="{ '--kind-color': entry.color }" :title="entry.title" @click="restore(entry)"><span class="item-icon">□</span><span class="item-text"><b>{{ entry.title }}</b></span></button>
    <button v-if="overflow.length" ref="overflowTrigger" type="button" class="taskbar-item overflow-trigger" :aria-expanded="popup === 'overflow'" aria-haspopup="menu" :aria-label="t('gui.taskbar.more', { count: overflow.length })" @click="toggle('overflow')">▾ +{{ overflow.length }}</button>
    </div>
    <Teleport to="body" :disabled="!launchpad">
    <div v-if="popup" ref="popupRoot" role="menu" class="taskbar-popup" :class="{ 'menu-board': popup === 'menu' && modelessStyle === 'flowgate', 'start-box': popup === 'menu' && modelessStyle === 'modern', launchpad, 'overflow-list': popup === 'overflow' }" :data-blur="launchpad ? (plainBackdrop ? 'off' : 'on') : undefined" :aria-label="launchpad ? t('gui.taskbar.menu') : undefined" @keydown="menuKey" @pointerdown="launchpadPointer">
      <template v-if="launchpad">
        <div class="launch-groups">
          <section v-for="group in menuGroups" :key="group.key" class="launch-group">
            <div class="launch-group-label">{{ t(group.label) }}</div>
            <div class="launch-group-row" role="group" :aria-label="t(group.label)">
              <button v-for="card in group.cards" :key="card.key" type="button" role="menuitem" class="launch-tile" :style="{ '--card-color': group.color }" :title="t(card.body)" :data-card="card.key" :data-card-state="cardState(card.key)" @click="openCard(card.key)"><span class="launch-tile-icon" aria-hidden="true">{{ card.icon }}</span><span class="launch-tile-name">{{ t(card.title) }}</span><i v-if="cardState(card.key) !== 'closed'" class="menu-card-dot" data-test="menu-card-dot" aria-hidden="true" /></button>
            </div>
          </section>
        </div>
        <p class="launchpad-hint">{{ t('gui.taskbar.launchpad_hint') }}</p>
      </template>
      <template v-else-if="popup === 'menu' && modelessStyle === 'modern'">
        <template v-for="group in menuGroups" :key="group.key">
          <div class="start-group-label">{{ t(group.label) }}</div>
          <div class="start-group-row" role="group" :aria-label="t(group.label)">
            <button v-for="card in group.cards" :key="card.key" type="button" role="menuitem" class="start-tile" :style="{ '--card-color': group.color }" :title="t(card.body)" :data-card="card.key" :data-card-state="cardState(card.key)" @click="openCard(card.key)"><span class="start-tile-icon" aria-hidden="true">{{ card.icon }}</span><span class="start-tile-name">{{ t(card.title) }}</span><i v-if="cardState(card.key) !== 'closed'" class="menu-card-dot" data-test="menu-card-dot" aria-hidden="true" /></button>
          </div>
        </template>
      </template>
      <template v-else-if="popup === 'menu'">
        <template v-for="group in menuGroups" :key="group.key">
          <div class="menu-group-label" :style="{ '--card-color': group.color }"><i aria-hidden="true" />{{ t(group.label) }}</div>
          <div class="menu-group-row" role="group" :aria-label="t(group.label)">
            <button v-for="card in group.cards" :key="card.key" type="button" role="menuitem" class="menu-card" :style="{ '--card-color': group.color }" :data-card="card.key" :data-card-state="cardState(card.key)" @click="openCard(card.key)"><span class="menu-card-heading">{{ card.icon }} {{ t(card.title) }}<i v-if="cardState(card.key) !== 'closed'" class="menu-card-dot" data-test="menu-card-dot" aria-hidden="true" /></span><span class="menu-card-body">{{ t(card.body) }}</span><i class="card-port" /></button>
          </div>
        </template>
      </template>
      <button v-for="entry in popup === 'overflow' ? overflow : []" :key="entry.id" type="button" role="menuitem" :title="entry.title" :style="{ '--kind-color': entry.color }" @click="restore(entry)">{{ entry.title }}</button>
    </div>
    </Teleport>
    <MyAiWindow :ref="(el) => setWindowRef('my-ai', el)" :open="windows['my-ai'].open" v-model:minimized="windows['my-ai'].minimized" @close="windows['my-ai'].open = false" />
    <QuestionPolicyWindow :ref="(el) => setWindowRef('question-policy', el)" :open="windows['question-policy'].open" v-model:minimized="windows['question-policy'].minimized" @close="windows['question-policy'].open = false" />
    <MeThemeWindow :ref="(el) => setWindowRef('theme', el)" :open="windows.theme.open" v-model:minimized="windows.theme.minimized" @close="windows.theme.open = false" />
    <MeAgentsWindow :ref="(el) => setWindowRef('agents', el)" :open="windows.agents.open" v-model:minimized="windows.agents.minimized" @close="windows.agents.open = false" />
    <MeAccountWindow :ref="(el) => setWindowRef('account', el)" :open="windows.account.open" v-model:minimized="windows.account.minimized" @close="windows.account.open = false" />
    <MeSecurityWindow :ref="(el) => setWindowRef('security', el)" :open="windows.security.open" v-model:minimized="windows.security.minimized" @close="windows.security.open = false" />
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
import MeAgentsWindow from '@/features/settings/windows/MeAgentsWindow.vue'
import MeAccountWindow from '@/features/settings/windows/MeAccountWindow.vue'
import MeSecurityWindow from '@/features/settings/windows/MeSecurityWindow.vue'
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
const capacity = computed(() => taskbarCapacity(width.value, minimized.value.length, modelessStyle.value))
const visible = computed(() => minimized.value.slice(0, capacity.value))
const overflow = computed(() => minimized.value.slice(capacity.value))
watch(overflow, (entries) => { if (!entries.length && popup.value === 'overflow') close() })
// Dock opens the menu as a full-screen launchpad (teleported to body so no ancestor clips it).
const launchpad = computed(() => popup.value === 'menu' && modelessStyle.value === 'dock')
// The launchpad blurs everything behind it. Where that is likely to stutter (few cores or little
// memory) or the user asked for less transparency, it only darkens the screen instead.
const plainBackdrop = (() => {
  if (typeof window === 'undefined') return false
  const nav = navigator as Navigator & { deviceMemory?: number }
  const reduced = typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-transparency: reduce)').matches
  return reduced || (nav.hardwareConcurrency > 0 && nav.hardwareConcurrency <= 2) || (typeof nav.deviceMemory === 'number' && nav.deviceMemory <= 2)
})()
function launchpadPointer(event: PointerEvent) {
  // Pressing an empty part of the launchpad (anything but an icon) closes it.
  if (launchpad.value && event.target instanceof Element && !event.target.closest('button')) close(true)
}

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
type CardKey = 'my-ai' | 'question-policy' | 'theme' | 'agents' | 'account' | 'security'
const menuGroups: { key: string; label: string; color: string; cards: { key: CardKey; icon: string; title: string; body: string }[] }[] = [
  { key: 'ai', label: 'gui.taskbar.group_ai', color: '#a78bfa', cards: [{ key: 'my-ai', icon: '✦', title: 'gui.taskbar.my_ai', body: 'gui.taskbar.my_ai_desc' }, { key: 'question-policy', icon: '✎', title: 'gui.taskbar.question_policy', body: 'gui.taskbar.question_policy_desc' }] },
  { key: 'settings', label: 'gui.taskbar.group_settings', color: '#94a3b8', cards: [{ key: 'theme', icon: '◐', title: 'gui.taskbar.theme', body: 'gui.taskbar.style' }, { key: 'agents', icon: '▣', title: 'gui.taskbar.agents', body: 'gui.taskbar.agents_desc' }, { key: 'account', icon: '☺', title: 'gui.taskbar.account', body: 'gui.taskbar.account_desc' }, { key: 'security', icon: '◈', title: 'gui.taskbar.security', body: 'gui.taskbar.security_desc' }] },
]
const windows = reactive<Record<CardKey, { open: boolean; minimized: boolean }>>({ 'my-ai': { open: false, minimized: false }, 'question-policy': { open: false, minimized: false }, theme: { open: false, minimized: false }, agents: { open: false, minimized: false }, account: { open: false, minimized: false }, security: { open: false, minimized: false } })
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
  if (event.target instanceof Node && !root.value?.contains(event.target) && !popupRoot.value?.contains(event.target)) close()
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

/* Modern: a flat bar whose icon-only items gather in the middle; names show on hover (title). */
.designer-taskbar[data-taskbar-style="modern"] { justify-content: center; gap: 4px; background: color-mix(in srgb, var(--fg2-panel) 88%, var(--fg2-bg)); border-top-color: var(--fg2-line); }
[data-taskbar-style="modern"] .taskbar-item { position: relative; width: 44px; height: 40px; padding: 0; display: grid; place-items: center; border: 1px solid transparent; border-radius: 8px; background: transparent; box-shadow: none; text-align: center; }
[data-taskbar-style="modern"] .taskbar-item:hover { background: color-mix(in srgb, var(--fg2-text) 8%, transparent); }
[data-taskbar-style="modern"] .item-text, [data-taskbar-style="modern"] .item-label, [data-taskbar-style="modern"] .designer-item i { display: none; }
[data-taskbar-style="modern"] .menu-trigger svg { width: 20px; height: 20px; stroke: var(--fg2-accent, #3b82f6); }
[data-taskbar-style="modern"] .menu-trigger[aria-expanded="true"] { background: color-mix(in srgb, var(--fg2-text) 10%, transparent); }
[data-taskbar-style="modern"] .item-icon { width: 24px; height: 24px; border-radius: 6px; font-size: 14px; }
[data-taskbar-style="modern"] .designer-item { background: color-mix(in srgb, var(--fg2-text) 8%, transparent); }
[data-taskbar-style="modern"] .designer-item::after { content: ''; position: absolute; left: 50%; bottom: 2px; width: 16px; height: 3px; margin-left: -8px; border-radius: 2px; background: var(--fg2-accent, #3b82f6); }
[data-taskbar-style="modern"] .minimized-item::after { content: ''; position: absolute; left: 50%; bottom: 3px; width: 6px; height: 3px; margin-left: -3px; border-radius: 2px; background: var(--fg2-muted, #94a3b8); }
[data-taskbar-style="modern"] .overflow-trigger { width: 48px; font-size: 11px; }
[data-taskbar-style="modern"] .taskbar-separator { height: 20px; margin: 0 2px; }
[data-taskbar-style="modern"] .taskbar-popup { border-radius: 12px; }
[data-taskbar-style="modern"] .overflow-list { right: auto; left: 50%; transform: translateX(-50%); }
/* Dock: a floating rounded group in the middle with large icon tiles; open windows get a dot below. */
.taskbar-group { display: contents; }
.designer-taskbar[data-taskbar-style="dock"] { justify-content: center; height: 54px; flex-basis: 54px; padding: 3px 10px; background: transparent; border-top-color: var(--fg2-line); }
[data-taskbar-style="dock"] .taskbar-group { display: flex; align-items: center; gap: 4px; min-width: 0; height: 48px; padding: 0 8px; box-sizing: border-box; border: 1px solid color-mix(in srgb, var(--fg2-text) 14%, transparent); border-radius: 14px; background: color-mix(in srgb, var(--fg2-panel) 72%, transparent); backdrop-filter: blur(20px); box-shadow: 0 6px 18px #0003; }
[data-taskbar-style="dock"] .taskbar-item { position: relative; width: 44px; height: 42px; padding: 0; display: grid; place-items: center; border: 0; border-radius: 9px; background: transparent; box-shadow: none; text-align: center; overflow: visible; }
[data-taskbar-style="dock"] .taskbar-item:hover { background: transparent; }
[data-taskbar-style="dock"] .item-text, [data-taskbar-style="dock"] .item-label, [data-taskbar-style="dock"] .designer-item i { display: none; }
[data-taskbar-style="dock"] .menu-trigger svg { width: 32px; height: 32px; box-sizing: border-box; padding: 7px; border-radius: 8px; color: #fff; background: linear-gradient(160deg, #5b6b82, #2d3a4f); box-shadow: 0 2px 6px #0006; }
[data-taskbar-style="dock"] .item-icon { width: 32px; height: 32px; border-radius: 8px; font-size: 15px; box-shadow: 0 2px 6px #0006; }
[data-taskbar-style="dock"] .designer-item .item-icon { color: #fff; background: linear-gradient(160deg, #2b5ea8, #163a6e); }
[data-taskbar-style="dock"] .minimized-item .item-icon { color: #fff; background: linear-gradient(160deg, color-mix(in srgb, var(--kind-color, #4ba3ff) 85%, #fff), color-mix(in srgb, var(--kind-color, #4ba3ff) 70%, #0a1a33)); }
[data-taskbar-style="dock"] .minimized-item { opacity: .85; }
[data-taskbar-style="dock"] .menu-trigger svg, [data-taskbar-style="dock"] .item-icon { transition: transform .15s; }
[data-taskbar-style="dock"] .taskbar-item:hover svg, [data-taskbar-style="dock"] .taskbar-item:hover .item-icon, [data-taskbar-style="dock"] .menu-trigger[aria-expanded="true"] svg { transform: translateY(-3px) scale(1.12); }
[data-taskbar-style="dock"] .designer-item::after { content: ''; position: absolute; left: 50%; bottom: 0; width: 4px; height: 4px; margin-left: -2px; border-radius: 50%; background: var(--fg2-text); }
[data-taskbar-style="dock"] .taskbar-separator { height: 30px; margin: 0 6px; background: color-mix(in srgb, var(--fg2-text) 22%, transparent); }
[data-taskbar-style="dock"] .overflow-trigger { width: 48px; height: 38px; border-radius: 10px; font-size: 11px; background: color-mix(in srgb, var(--fg2-text) 8%, transparent); }
[data-taskbar-style="dock"] .taskbar-popup { border-radius: 14px; }
[data-taskbar-style="dock"] .overflow-list { right: auto; left: 50%; transform: translateX(-50%); }
@media (prefers-reduced-transparency: reduce) { [data-taskbar-style="dock"] .taskbar-group { backdrop-filter: none; background: var(--fg2-panel); } }
.taskbar-popup.launchpad { position: fixed; inset: 0; bottom: 0; z-index: 100000; display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 40px 8%; border: 0; border-radius: 0; box-shadow: none; color: #f2f6fc; background-color: rgba(10, 18, 32, .38); backdrop-filter: blur(26px) saturate(1.4) brightness(.8); -webkit-backdrop-filter: blur(26px) saturate(1.4) brightness(.8); overflow: auto; animation: launchpad-in .2s ease-out; }
.taskbar-popup.launchpad[data-blur="off"] { backdrop-filter: none; -webkit-backdrop-filter: none; background-color: rgba(6, 12, 24, .82); }
@supports not ((backdrop-filter: blur(1px)) or (-webkit-backdrop-filter: blur(1px))) { .taskbar-popup.launchpad { background-color: rgba(6, 12, 24, .82); } }
@keyframes launchpad-in { from { opacity: 0; transform: scale(1.04); } to { opacity: 1; transform: none; } }
@media (prefers-reduced-motion: reduce) { .taskbar-popup.launchpad { animation: none; } }
.launch-groups { display: flex; flex-wrap: wrap; justify-content: center; align-items: flex-start; gap: 36px 72px; max-width: 100%; }
.launch-group-label { margin: 0 0 18px; font-size: 14px; font-weight: 700; text-align: center; color: #f2f6fc; text-shadow: 0 1px 3px #000b; }
.launch-group-row { display: flex; flex-wrap: wrap; justify-content: center; gap: 28px 40px; }
.taskbar-popup .launch-tile { position: relative; display: flex; flex-direction: column; align-items: center; gap: 10px; width: 112px; padding: 6px 0 12px; border: 0; border-radius: 14px; background: transparent; color: inherit; text-align: center; }
.taskbar-popup .launch-tile:hover { background: transparent; }
.launch-tile-icon { display: grid; place-items: center; width: 84px; height: 84px; border-radius: 20px; font-size: 34px; color: #fff; background: linear-gradient(160deg, color-mix(in srgb, var(--card-color, #94a3b8) 85%, #fff), color-mix(in srgb, var(--card-color, #94a3b8) 70%, #0a1a33)); box-shadow: 0 8px 22px #0007, inset 0 1px 0 #fff6; transition: transform .15s; }
.launch-tile:hover .launch-tile-icon, .launch-tile:focus-visible .launch-tile-icon { transform: scale(1.06); }
.launch-tile:active .launch-tile-icon { filter: brightness(.75); }
.launch-tile-name { max-width: 112px; font-size: 13px; color: #f2f6fc; text-shadow: 0 1px 3px #000b; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.launch-tile .menu-card-dot { position: absolute; left: 50%; bottom: 0; width: 5px; height: 5px; margin: 0 0 0 -2.5px; background: #fff; box-shadow: none; }
.launchpad-hint { position: absolute; left: 0; right: 0; bottom: 14px; margin: 0; font-size: 11px; text-align: center; color: rgba(255, 255, 255, .6); pointer-events: none; }
.start-box { left: 50%; transform: translateX(-50%); width: 440px; max-width: calc(100vw - 48px); max-height: calc(100vh - 140px); overflow-y: auto; box-sizing: border-box; padding: 18px 22px; }
.start-group-label { margin: 14px 4px 8px; font-size: 12px; font-weight: 700; color: var(--fg2-text); }
.start-group-label:first-child { margin-top: 0; }
.start-group-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; }
.taskbar-popup .start-tile { position: relative; display: flex; flex-direction: column; align-items: center; gap: 6px; padding: 10px 4px 8px; border: 1px solid transparent; border-radius: 8px; background: transparent; text-align: center; }
.taskbar-popup .start-tile:hover { background: color-mix(in srgb, var(--fg2-text) 8%, transparent); }
.start-tile-icon { display: grid; place-items: center; width: 36px; height: 36px; border-radius: 9px; font-size: 18px; color: var(--card-color, #94a3b8); background: color-mix(in srgb, var(--card-color, #94a3b8) 18%, var(--fg2-panel)); }
.start-tile-name { max-width: 100%; font-size: 11.5px; line-height: 1.25; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.start-tile .menu-card-dot { position: absolute; top: 8px; right: 12px; margin: 0; }
</style>

