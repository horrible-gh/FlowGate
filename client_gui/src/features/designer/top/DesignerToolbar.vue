<template>
  <!-- flowgate.v02.0038 — Designer Toolbar (0001-R §4): undo / redo, 정렬 / 배치 ▾, the two run
       buttons, fit / zoom out / zoom in, the zoom value and the layout density. No select / add
       node / connect modes (0001-R §4.2) and no standing stop button: the run button that was
       used turns into ■ 중지 in place and the other one is disabled until the run stops
       (0001-R §4.1.1). 정렬 / 배치 ▾ is the same entry as the Edit menu's 정렬 / 배치… and emits
       the same command. flowgate.v02.0039 T#3: `disabled` (no work target yet) keeps every tool
       on screen but dimmed and unavailable, as the confirmed initial-state design draws it.
       flowgate.v02.0045 TR0020 rev2: 검증 sits right before the run buttons (it checks what 실행
       would take); it is available while the open Flow's edited structure can be checked
       (`canValidate`).
       flowgate.v02.0053 T#9: the run buttons drive the server run (the Shell sends the commands to the
       Center session); `runMode` is the server's run as this tab knows it. Next to them one dot shows the
       Flow's run — ● running, ❚❚ stopped, ✓ completed — its name in the tooltip only.
       flowgate.v02.0059 T#1 (0011-CH contract A): [선택] is back for multi-selection only (no add
       node / connect mode): a toggle whose ON / OFF (`selectMode`, the Center session's) shows on the
       button; it emits `edit.select-mode` and the Shell switches the mode.
       flowgate.v02.0059 T#2 (0011-CH contract D): 정렬 / 배치 ▾ opens a drop-down of the same commands as
       the Edit menu's 정렬 / 배치 submenu — six 정렬 and two 균등 분배 of the selected cards, then 자동 배치 —
       each unavailable while too few cards are selected (`targets`, the Menu Bar's). A choice, Escape or a
       press outside closes it.
       flowgate.v02.0059 0017-T: the zoom is one control — it shows the Canvas zoom (also one that is no
       preset, e.g. 115%) and chooses a preset; the zoom is never shown twice. The density radios set the
       step a card drag moves in. -->
  <div
    class="designer-toolbar"
    :class="{ 'is-unavailable': disabled }"
    role="toolbar"
    :aria-label="t('gui.designer.top.toolbar_label')"
    :aria-disabled="disabled ? 'true' : undefined"
    data-test="designer-toolbar"
  >
    <div class="designer-tool-group" data-test="designer-tool-group">
      <button type="button" class="designer-tool" data-test="designer-tool" data-command="edit.undo" :disabled="disabled || !canUndo" @click="emit('command', 'edit.undo')">
        <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M7 5L3 9l4 4" /><path d="M3 9h9a5 5 0 0 1 0 10h-2" /></svg>
        <span>{{ t('gui.designer.top.tools.undo') }}</span>
      </button>
      <button type="button" class="designer-tool" data-test="designer-tool" data-command="edit.redo" :disabled="disabled || !canRedo" @click="emit('command', 'edit.redo')">
        <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M13 5l4 4-4 4" /><path d="M17 9H8a5 5 0 0 0 0 10h2" /></svg>
        <span>{{ t('gui.designer.top.tools.redo') }}</span>
      </button>
    </div>

    <div class="designer-tool-group" data-test="designer-tool-group">
      <button
        type="button"
        class="designer-tool"
        :class="{ 'is-on': selectMode && !disabled }"
        data-test="designer-tool"
        data-command="edit.select-mode"
        :disabled="disabled"
        :aria-pressed="selectMode && !disabled ? 'true' : 'false'"
        :title="t(selectMode && !disabled ? 'gui.designer.top.tools.select_mode_on' : 'gui.designer.top.tools.select_mode_off')"
        @click="emit('command', 'edit.select-mode')"
      >
        <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3 3h4M3 3v4M17 3h-4M17 3v4M3 17h4M3 17v-4" /><path d="M10 9l7 3-3 1.2-1.2 3z" /></svg>
        <span>{{ t('gui.designer.top.tools.select_mode') }}</span>
      </button>
      <div ref="arrangeRoot" class="designer-tool-menu">
        <button
          ref="arrangeButton"
          type="button"
          class="designer-tool"
          :class="{ 'is-open': arrangeOpen }"
          data-test="designer-tool"
          data-tool="arrange"
          aria-haspopup="menu"
          :aria-expanded="arrangeOpen ? 'true' : 'false'"
          :disabled="disabled"
          @click="toggleArrange"
          @keydown.esc="arrangeOpen = false"
        >
          <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3 4h14M3 8h9M3 12h14M3 16h9" /></svg>
          <span>{{ t('gui.designer.top.tools.arrange') }}</span>
        </button>
        <div
          v-if="arrangeOpen && !disabled"
          class="designer-tool-pop"
          role="menu"
          :aria-label="t('gui.designer.top.items.arrange')"
          :style="arrangeStyle"
          data-test="designer-arrange-menu"
          @keydown.esc="closeArrange(true)"
        >
          <template v-for="(group, groupIndex) in ARRANGE_MENU" :key="`arrange-${groupIndex}`">
            <div v-if="groupIndex > 0" class="designer-tool-pop-sep" role="separator" />
            <button
              v-for="command in group"
              :key="command"
              type="button"
              role="menuitem"
              class="designer-tool-pop-item"
              :disabled="!isMenuCommandEnabled(command, runMode, targets)"
              data-test="designer-arrange-item"
              :data-arrange="command"
              @click="chooseArrange(command)"
            >
              {{ command === 'edit.auto-layout' ? t('gui.designer.top.items.auto_layout') : t(`gui.designer.top.arrange.${command.slice(5)}`) }}
            </button>
          </template>
        </div>
      </div>
    </div>

    <div class="designer-tool-group" data-test="designer-tool-group">
      <button
        type="button"
        class="designer-tool"
        data-test="designer-tool"
        data-command="run.validate"
        :disabled="disabled || !canValidate || runMode !== null"
        @click="emit('command', 'run.validate')"
      >
        <svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="7.5" /><path d="M6.5 10.2l2.4 2.4 4.6-5" /></svg>
        <span>{{ t('gui.designer.top.tools.validate') }}</span>
      </button>
      <button
        v-for="run in RUN_BUTTONS"
        :key="run.mode"
        type="button"
        class="designer-tool"
        :class="{ 'is-running': runMode === run.mode }"
        :disabled="disabled || (runMode !== null && runMode !== run.mode)"
        :aria-pressed="runMode === run.mode ? 'true' : 'false'"
        data-test="designer-tool-run"
        :data-run="run.mode"
        @click="emit('command', runMode === run.mode ? 'run.stop' : run.command)"
      >
        <svg v-if="runMode === run.mode" class="is-stop" viewBox="0 0 20 20" aria-hidden="true"><rect x="5" y="5" width="10" height="10" rx="1" /></svg>
        <svg v-else-if="run.mode === 'next'" class="is-run" viewBox="0 0 20 20" aria-hidden="true"><path d="M6 4l9 6-9 6z" /></svg>
        <svg v-else class="is-run" viewBox="0 0 20 20" aria-hidden="true"><path d="M2 4l8 6-8 6zM10 4l8 6-8 6z" /></svg>
        <span>{{ runMode === run.mode ? t('gui.designer.top.tools.stop') : t(`gui.designer.top.tools.${run.label}`) }}</span>
      </button>
      <span
        v-if="shownRunStatus"
        class="designer-run-dot"
        :class="`is-${shownRunStatus}`"
        role="img"
        :aria-label="t(`gui.designer.top.run_status.${shownRunStatus}`)"
        :title="t(`gui.designer.top.run_status.${shownRunStatus}`)"
        data-test="designer-run-status"
        :data-status="shownRunStatus"
      >
        <svg v-if="shownRunStatus === 'stopped'" viewBox="0 0 12 12" aria-hidden="true"><path d="M3.5 2.5v7M8.5 2.5v7" /></svg>
        <svg v-else-if="shownRunStatus === 'completed'" viewBox="0 0 12 12" aria-hidden="true"><path d="M2.5 6.3l2.4 2.4 4.6-5" /></svg>
      </span>
    </div>

    <div class="designer-tool-group" data-test="designer-tool-group">
      <button type="button" class="designer-tool is-compact" data-test="designer-tool" data-command="view.fit" :disabled="disabled" @click="emit('command', 'view.fit')">
        <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3 7V3h4M13 3h4v4M17 13v4h-4M7 17H3v-4" /></svg>
        <span>{{ t('gui.designer.top.tools.zoom_fit') }}</span>
      </button>
      <button type="button" class="designer-tool is-compact" data-test="designer-tool" data-command="view.zoom-out" :disabled="disabled" @click="emit('command', 'view.zoom-out')">
        <svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="7" /><path d="M7 10h6" /></svg>
        <span>{{ t('gui.designer.top.tools.zoom_out') }}</span>
      </button>
      <button type="button" class="designer-tool is-compact" data-test="designer-tool" data-command="view.zoom-in" :disabled="disabled" @click="emit('command', 'view.zoom-in')">
        <svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="7" /><path d="M7 10h6M10 7v6" /></svg>
        <span>{{ t('gui.designer.top.tools.zoom_in') }}</span>
      </button>
      <select
        class="designer-tool-zoom"
        :value="String(zoom)"
        :disabled="disabled"
        :aria-label="t('gui.designer.top.tools.zoom_value')"
        :title="t('gui.designer.top.tools.zoom_preset')"
        data-test="designer-tool-zoom"
        :data-zoom="zoom"
        @change="chooseZoom"
      >
        <option v-for="percent in zoomOptions" :key="percent" :value="String(percent)" :selected="percent === zoom">{{ percent }}%</option>
      </select>
      <div class="designer-tool-density" role="radiogroup" :aria-label="t('gui.designer.top.tools.density')" :title="t('gui.designer.top.tools.density')" data-test="designer-tool-density">
        <button
          v-for="(level, index) in DESIGNER_DENSITIES"
          :key="level"
          type="button"
          role="radio"
          :class="{ 'is-on': density === level }"
          :aria-checked="density === level ? 'true' : 'false'"
          :aria-label="t(`gui.designer.top.densities.${level}`)"
          :title="t(`gui.designer.top.densities.${level}`)"
          :data-density="level"
          :disabled="disabled"
          @click="emit('update:density', level)"
        >
          <span v-if="level === 'free'" class="designer-density-free" aria-hidden="true" />
          <span v-else class="designer-density-dots" :style="{ gap: `${4 - index}px` }" aria-hidden="true"><i v-for="dot in 9" :key="dot" /></span>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  ALL_MENU_TARGETS,
  DESIGNER_ARRANGE_GROUPS,
  DESIGNER_DENSITIES,
  isMenuCommandEnabled,
  type DesignerDensity,
  type DesignerMenuTargets,
  type DesignerRunMode,
  type DesignerTopCommand,
} from './designerTop'

const props = withDefaults(
  defineProps<{
    runMode: DesignerRunMode
    density: DesignerDensity
    /** The Canvas zoom, in percent: what the zoom control shows. */
    zoom: number
    /** True while the Designer has no work target (0039 T#3): every tool is unavailable. */
    disabled?: boolean
    /** flowgate.v02.0045 TR0020 rev2: the open Flow's edited structure can be checked now. */
    canValidate?: boolean
    /** flowgate.v02.0059 T#2: the open Flow's structure edits can be undone / redone (Edit menu follows the same). */
    canUndo?: boolean
    canRedo?: boolean
    /** flowgate.v02.0053 T#9: the open Flow's server run status (`running` | `stopped` | `completed`), or null. */
    runStatus?: string | null
    /** flowgate.v02.0059 T#1: the [선택] mode is on. */
    selectMode?: boolean
    /** flowgate.v02.0059 T#2: what the 정렬 / 배치 ▾ commands act on (the Menu Bar's targets). */
    targets?: DesignerMenuTargets
  }>(),
  { disabled: false, canValidate: false, canUndo: true, canRedo: true, runStatus: null, selectMode: false, targets: () => ALL_MENU_TARGETS },
)

const emit = defineEmits<{
  command: [command: DesignerTopCommand]
  'update:density': [density: DesignerDensity]
  /** A zoom preset was chosen, in percent. */
  'update:zoom': [percent: number]
}>()

/** Zoom presets, in percent; the current value joins the list while it is none of them. */
const ZOOM_PRESETS: readonly number[] = [25, 50, 75, 100, 150, 200]
const zoomOptions = computed(() =>
  ZOOM_PRESETS.includes(props.zoom) ? ZOOM_PRESETS : [...ZOOM_PRESETS, props.zoom].sort((a, b) => a - b),
)

// flowgate.v02.0059 0017-T: a chosen preset goes to the Canvas; the control then shows the zoom the Canvas
// reports back (`zoom`), so it never keeps showing a choice the Canvas did not take.
function chooseZoom(event: Event) {
  const select = event.target as HTMLSelectElement
  emit('update:zoom', Number(select.value))
  void nextTick(() => {
    select.value = String(props.zoom)
  })
}

const { t } = useI18n()

/** Run states the dot shows; any other value (or none) shows no dot. */
const RUN_DOT_STATES: readonly string[] = ['running', 'stopped', 'completed']
const shownRunStatus = computed(() => (!props.disabled && props.runStatus && RUN_DOT_STATES.includes(props.runStatus) ? props.runStatus : null))

const RUN_BUTTONS = [
  { mode: 'next', command: 'run.next', label: 'run_next' },
  { mode: 'all', command: 'run.all', label: 'run_all' },
] as const

// flowgate.v02.0059 T#2: 정렬 / 배치 ▾ — the Edit menu's 정렬 / 배치 commands, then 자동 배치. The drop-down
// is placed under its button in the viewport (fixed), so the Toolbar's own scrolling never clips it.
const ARRANGE_MENU: readonly (readonly DesignerTopCommand[])[] = [...DESIGNER_ARRANGE_GROUPS, ['edit.auto-layout']]
const arrangeOpen = ref(false)
const arrangeRoot = ref<HTMLElement | null>(null)
const arrangeButton = ref<HTMLButtonElement | null>(null)
const arrangeStyle = ref<Record<string, string>>({})

function toggleArrange() {
  if (arrangeOpen.value) {
    arrangeOpen.value = false
    return
  }
  const rect = arrangeButton.value?.getBoundingClientRect()
  arrangeStyle.value = rect ? { top: `${Math.round(rect.bottom + 4)}px`, left: `${Math.round(rect.left)}px` } : {}
  arrangeOpen.value = true
}

function closeArrange(refocus = false) {
  arrangeOpen.value = false
  if (refocus) arrangeButton.value?.focus()
}

function chooseArrange(command: DesignerTopCommand) {
  closeArrange()
  emit('command', command)
}

function onDocumentPointerDown(event: Event) {
  if (arrangeOpen.value && arrangeRoot.value && !arrangeRoot.value.contains(event.target as Node)) closeArrange()
}

onMounted(() => document.addEventListener('pointerdown', onDocumentPointerDown))
onBeforeUnmount(() => document.removeEventListener('pointerdown', onDocumentPointerDown))
</script>

<style scoped>
.designer-toolbar {
  display: flex;
  align-items: stretch;
  gap: 8px;
  height: 100%;
  min-width: 0;
  padding: 7px 14px;
  overflow-x: auto;
  overflow-y: hidden;
  box-sizing: border-box;
  border-bottom: 1px solid var(--fg2-line);
  background: #0d1b2e;
}

.designer-tool-group {
  display: flex;
  flex: none;
  align-items: center;
  gap: 4px;
  padding-right: 10px;
  border-right: 1px solid var(--fg2-line);
}

.designer-tool-group:last-child {
  border-right: 0;
}

.designer-tool {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  min-width: 56px;
  height: 50px;
  padding: 0 6px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: #aebfd2;
  font: inherit;
  font-size: 11px;
  white-space: nowrap;
  cursor: pointer;
}

.designer-tool.is-compact {
  min-width: 46px;
}

.designer-tool svg {
  width: 19px;
  height: 19px;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.6;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.designer-tool svg.is-run {
  fill: #2bddad;
  stroke: #2bddad;
}

.designer-tool svg.is-stop {
  fill: currentColor;
}

.designer-tool:hover:not(:disabled),
.designer-tool.is-running {
  border-color: #244a79;
  background: #173157;
  color: #fff;
}

/* flowgate.v02.0059 T#2: 정렬 / 배치 ▾ and its drop-down (fixed under the button, over the work area). */
.designer-tool-menu {
  display: flex;
}

.designer-tool.is-open {
  border-color: #244a79;
  background: #173157;
  color: #fff;
}

.designer-tool-pop {
  position: fixed;
  z-index: 30;
  min-width: 190px;
  padding: 6px;
  box-sizing: border-box;
  border: 1px solid #31516f;
  border-radius: 8px;
  background: #0c1a2b;
  box-shadow: 0 14px 34px rgba(0, 0, 0, 0.6);
}

.designer-tool-pop-item {
  display: flex;
  align-items: center;
  width: 100%;
  height: 30px;
  padding: 0 10px;
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

.designer-tool-pop-item:hover:not(:disabled),
.designer-tool-pop-item:focus-visible {
  outline: 0;
  background: #15304d;
  color: #fff;
}

.designer-tool-pop-item:disabled {
  color: #50677e;
  cursor: default;
  opacity: 0.72;
}

.designer-tool-pop-sep {
  height: 1px;
  margin: 5px 3px;
  background: #20364e;
}

/* flowgate.v02.0059 T#1: [선택] ON stays lit until it is turned off. */
.designer-tool.is-on,
.designer-tool.is-on:hover:not(:disabled) {
  border-color: #58a6ff;
  background: #18437b;
  color: #fff;
}

.designer-tool.is-on svg {
  stroke: #58a6ff;
}

.designer-tool:disabled {
  cursor: not-allowed;
  opacity: var(--fg2-control-disabled-opacity-strong);
}

.designer-tool:focus-visible,
.designer-tool-density button:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 1px;
}

/* No work target (0039 T#3, confirmed design v7): the whole band dims once; the buttons inside
   do not dim a second time. */
.designer-toolbar.is-unavailable > .designer-tool-group {
  opacity: 0.38;
  filter: saturate(0.45);
  user-select: none;
}

.designer-toolbar.is-unavailable .designer-tool:disabled {
  opacity: 1;
  color: #66788d;
  cursor: default;
}

.designer-toolbar.is-unavailable .designer-tool svg.is-run {
  fill: #66788d;
  stroke: #66788d;
}

.designer-toolbar.is-unavailable .designer-tool-density button {
  cursor: default;
}

.designer-run-dot {
  display: grid;
  flex: none;
  place-items: center;
  width: 14px;
  height: 14px;
  margin: 0 2px;
  border-radius: 50%;
}

.designer-run-dot svg {
  width: 10px;
  height: 10px;
  fill: none;
  stroke: #0d1b2e;
  stroke-width: 2;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.designer-run-dot.is-running {
  width: 10px;
  height: 10px;
  background: #2bddad;
  box-shadow: 0 0 0 3px rgba(43, 221, 173, 0.22);
}

.designer-run-dot.is-stopped {
  background: #f5a524;
}

.designer-run-dot.is-completed {
  background: #58a6ff;
}

/* flowgate.v02.0059 0017-T: the one zoom control — the current zoom in a box, presets on opening it. */
.designer-tool-zoom {
  height: 36px;
  margin: auto 2px;
  padding: 0 6px 0 10px;
  box-sizing: border-box;
  border: 1px solid var(--fg2-line-strong);
  border-radius: 6px;
  background: #102139;
  color: var(--fg2-text);
  font: inherit;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  cursor: pointer;
}

.designer-tool-zoom option {
  background: #0c1a2b;
  color: var(--fg2-text);
}

.designer-tool-zoom:hover:not(:disabled) {
  border-color: #244a79;
  background: #173157;
}

.designer-tool-zoom:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 1px;
}

.designer-tool-zoom:disabled {
  cursor: default;
}

.designer-tool-density {
  display: flex;
  height: 36px;
  margin: auto 0;
  overflow: hidden;
  box-sizing: border-box;
  border: 1px solid var(--fg2-line-strong);
  border-radius: 6px;
}

.designer-tool-density button {
  display: grid;
  place-items: center;
  width: 39px;
  border: 0;
  border-right: 1px solid var(--fg2-line);
  background: #0f2036;
  color: #7e99b6;
  cursor: pointer;
}

.designer-tool-density button:last-child {
  border-right: 0;
}

.designer-tool-density button:hover:not(:disabled),
.designer-tool-density button.is-on {
  background: #18437b;
  color: #58a6ff;
}

.designer-density-dots {
  display: grid;
  grid-template-columns: repeat(3, 3px);
}

.designer-density-dots i {
  width: 3px;
  height: 3px;
  border-radius: 50%;
  background: currentColor;
}

.designer-density-free {
  display: block;
  width: 15px;
  height: 15px;
  box-sizing: border-box;
  border: 1.5px solid currentColor;
  border-radius: 3px;
}
</style>
