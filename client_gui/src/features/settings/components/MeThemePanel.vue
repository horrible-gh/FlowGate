<template>
  <div class="me-theme-panel" data-test="me-theme-panel">
    <div v-if="loading" role="status">{{ t('gui.common.loading') }}</div>
    <div v-else-if="loadFailed" role="alert">{{ t('gui.settings.self.errors.load_failed') }} <button type="button" @click="load">{{ t('gui.common.retry') }}</button></div>
    <div v-else ref="group" class="theme-cards" role="radiogroup" :aria-label="t('gui.taskbar.style')" @keydown="cardKey">
      <button v-for="style in TASKBAR_STYLES" :key="style" type="button" role="radio" class="theme-card" :class="{ selected: selected === style }"
        :aria-checked="selected === style" :tabindex="selected === style ? 0 : -1" :data-style="style" data-test="taskbar-style-card" @click="choose(style)">
        <!-- A small drawing of the style: a window title bar above a taskbar, in CSS only. -->
        <span class="pv" :data-preview="style" aria-hidden="true">
          <span class="pv-win"><span class="pv-title"><span class="pv-name" /><span class="pv-btns"><i /><i /></span></span></span>
          <span class="pv-bar"><span class="pv-group"><svg v-if="style === 'modern'" class="pv-menu-node" viewBox="0 0 24 24"><rect x="3" y="9" width="6" height="6" /><rect x="15" y="3" width="6" height="6" /><rect x="15" y="15" width="6" height="6" /></svg><i v-else class="pv-menu" /><i /><i /></span></span>
        </span>
        <span class="theme-name">{{ t(`gui.taskbar.style_${style}`) }}</span>
        <span v-if="selected === style" class="theme-check" aria-hidden="true">✓</span>
      </button>
    </div>
    <p v-if="saveFailed" class="fgs-error theme-error" role="alert">{{ t('gui.settings.self.errors.save_failed') }}</p>
  </div>
</template>
<script setup lang="ts">
// The theme setting itself, without a frame: the settings page and the Designer menu window
// both render this one component, so a change here shows in both places.
// Pressing a card applies the style at once and saves it; there is no separate save button and no
// "saved" wording. Only a failed save speaks up, after putting the last saved style back.
import { nextTick, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import '../settings.css'
import { getUiSettings, saveTaskbarStyle } from '../meApi'
import { modelessStyle, normalizeTaskbarStyle, TASKBAR_STYLES, TASKBAR_STYLE_FIELD, type TaskbarStyle } from '@/core/overlay/modelessRegistry'
const { t } = useI18n()
const group = ref<HTMLElement | null>(null)
const selected = ref<TaskbarStyle>('flowgate')
let confirmed: TaskbarStyle = 'flowgate'
const loading = ref(true)
const loadFailed = ref(false)
const saveFailed = ref(false)
async function load() {
  loading.value = true; loadFailed.value = false
  try {
    const body = await getUiSettings()
    selected.value = confirmed = normalizeTaskbarStyle(body.settings?.[TASKBAR_STYLE_FIELD])
    modelessStyle.value = confirmed
  } catch { loadFailed.value = true }
  finally { loading.value = false }
}
// Saves run one after another so a quick run of choices reaches the server in order; a choice that
// a newer one has already replaced is skipped, so only the last choice is written.
let latest = 0
let queue: Promise<void> = Promise.resolve()
function choose(style: TaskbarStyle) {
  if (style === selected.value) return
  selected.value = style
  modelessStyle.value = style
  saveFailed.value = false
  const mine = ++latest
  queue = queue.then(() => persist(style, mine))
}
async function persist(style: TaskbarStyle, mine: number) {
  if (mine !== latest) return
  try {
    const body = await saveTaskbarStyle(style)
    confirmed = normalizeTaskbarStyle(body.settings?.[TASKBAR_STYLE_FIELD])
    if (mine === latest) { selected.value = confirmed; modelessStyle.value = confirmed }
  } catch {
    if (mine === latest) { selected.value = confirmed; modelessStyle.value = confirmed; saveFailed.value = true }
  }
}
// One radio group: ← → (and ↑ ↓) move to the next card and apply it, Home / End jump to the ends.
function cardKey(event: KeyboardEvent) {
  const index = TASKBAR_STYLES.indexOf(selected.value)
  const last = TASKBAR_STYLES.length - 1
  let next: number
  if (event.key === 'ArrowRight' || event.key === 'ArrowDown') next = index === last ? 0 : index + 1
  else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') next = index === 0 ? last : index - 1
  else if (event.key === 'Home') next = 0
  else if (event.key === 'End') next = last
  else return
  event.preventDefault()
  choose(TASKBAR_STYLES[next])
  void nextTick(() => group.value?.querySelector<HTMLButtonElement>(`[data-style="${TASKBAR_STYLES[next]}"]`)?.focus())
}
onMounted(load)
</script>
<style scoped>
/* Four cards in a row, or two by two when the space is narrower than four readable cards (never three and one). */
.me-theme-panel { container-type: inline-size; }
.theme-cards { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; max-width: 520px; }
@container (max-width: 445px) { .theme-cards { grid-template-columns: repeat(2, minmax(0, 1fr)); max-width: 300px; } }
.theme-card { position: relative; display: flex; flex-direction: column; align-items: stretch; gap: 7px; min-width: 0; padding: 7px 7px 8px; border: 1.5px solid var(--fg2-line-strong); border-radius: 9px; background: var(--fg2-panel); color: var(--fg2-text); font: inherit; cursor: pointer; text-align: center; }
.theme-card:hover { border-color: color-mix(in srgb, var(--fg2-blue, #4ba3ff) 55%, var(--fg2-line-strong)); }
.theme-card:focus-visible { outline: 2px solid var(--fg2-focus, #7cc0ff); outline-offset: 2px; }
.theme-card.selected { border-color: var(--fg2-blue, #4ba3ff); background: color-mix(in srgb, var(--fg2-blue, #4ba3ff) 10%, var(--fg2-panel)); }
.theme-name { font-size: 12px; font-weight: 650; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.theme-check { position: absolute; top: -7px; right: -7px; display: grid; place-items: center; width: 18px; height: 18px; border-radius: 50%; font-size: 11px; font-weight: 800; color: #fff; background: var(--fg2-blue, #4ba3ff); box-shadow: 0 0 0 2px var(--fg2-panel); }
.theme-error { margin: 8px 0 0; }

/* ── Previews: the desktop, one window title bar and the taskbar, drawn with plain boxes. ── */
.pv { position: relative; display: block; height: 58px; border-radius: 5px; overflow: hidden; background: var(--fg2-bg); }
.pv-win { position: absolute; left: 10px; right: 10px; top: 7px; height: 28px; box-sizing: border-box; border: 1px solid var(--fg2-line-strong); border-radius: 3px; background: var(--fg2-panel); overflow: hidden; }
.pv-title { display: flex; align-items: center; gap: 4px; height: 9px; padding: 0 3px 0 5px; border-bottom: 1px solid var(--fg2-line); }
.pv-name { width: 32%; height: 3px; border-radius: 2px; background: color-mix(in srgb, var(--fg2-text) 55%, transparent); }
.pv-btns { display: flex; gap: 2px; margin-left: auto; }
.pv-btns i { width: 4px; height: 4px; border-radius: 1px; background: color-mix(in srgb, var(--fg2-text) 45%, transparent); }
.pv-bar { position: absolute; left: 0; right: 0; bottom: 0; display: flex; align-items: center; height: 13px; padding: 0 4px; box-sizing: border-box; border-top: 1px solid var(--fg2-line-strong); background: var(--fg2-panel); }
.pv-group { display: flex; align-items: center; gap: 3px; }
.pv-group i { width: 13px; height: 7px; border-radius: 2px; background: color-mix(in srgb, var(--fg2-text) 22%, transparent); }

/* FlowGate: coloured stripe on the title bar, a dashed menu box and card-like items on the left. */
[data-preview="flowgate"] .pv-title { box-shadow: inset 0 2px 0 var(--fg2-blue, #4ba3ff); background: color-mix(in srgb, var(--fg2-blue, #4ba3ff) 10%, var(--fg2-panel)); }
[data-preview="flowgate"] .pv-group i { border-left: 2px solid var(--fg2-blue, #4ba3ff); border-radius: 1px; }
[data-preview="flowgate"] .pv-group .pv-menu { width: 11px; border: 1px dashed var(--fg2-blue, #4ba3ff); background: transparent; }

/* Modern: flat title bar, small square icons gathered in the middle of the bar. */
[data-preview="modern"] .pv-win { border-radius: 5px; }
[data-preview="modern"] .pv-title { border-bottom-color: transparent; background: color-mix(in srgb, var(--fg2-text) 5%, var(--fg2-panel)); }
[data-preview="modern"] .pv-bar { justify-content: center; }
[data-preview="modern"] .pv-group i { width: 7px; height: 7px; }
/* The menu mark is the node icon (three linked boxes) the taskbar menu button draws in this style. */
.pv-menu-node { width: 9px; height: 9px; fill: none; stroke: var(--fg2-text); stroke-width: 3; }

/* Dock: centred title, round buttons, and a floating rounded group of icons above the bottom edge. */
[data-preview="dock"] .pv-win { border-radius: 6px; }
[data-preview="dock"] .pv-name { margin-left: auto; }
[data-preview="dock"] .pv-btns { margin-left: auto; }
[data-preview="dock"] .pv-btns i { border-radius: 50%; }
[data-preview="dock"] .pv-bar { justify-content: center; border-top: 0; background: transparent; }
[data-preview="dock"] .pv-group { height: 10px; margin-bottom: 2px; padding: 0 3px; border: 1px solid var(--fg2-line-strong); border-radius: 4px; background: var(--fg2-panel); }
[data-preview="dock"] .pv-group i { width: 6px; height: 6px; border-radius: 2px; background: color-mix(in srgb, var(--fg2-blue, #4ba3ff) 70%, var(--fg2-panel)); }
[data-preview="dock"] .pv-group .pv-menu { background: color-mix(in srgb, var(--fg2-text) 45%, transparent); }

/* Classic: deep navy title bar and taskbar, a named menu button and wide named items on the left. */
[data-preview="classic"] .pv-win { border-color: #34507a; }
[data-preview="classic"] .pv-title { border-bottom-color: #2a4266; background: linear-gradient(180deg, #33527f, #223b5f); }
[data-preview="classic"] .pv-name { background: #fff; }
[data-preview="classic"] .pv-btns i { width: 5px; background: rgba(255, 255, 255, .55); }
[data-preview="classic"] .pv-bar { border-top-color: #3a5680; background: linear-gradient(180deg, #24395a, #142338); }
[data-preview="classic"] .pv-group i { width: 18px; background: rgba(255, 255, 255, .16); }
[data-preview="classic"] .pv-group .pv-menu { width: 14px; background: #2c64a8; }
</style>
