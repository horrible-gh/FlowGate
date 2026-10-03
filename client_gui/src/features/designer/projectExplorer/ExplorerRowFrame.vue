<template>
  <!-- Thin shared row primitive (D0002 §7.1/§7.2): one treeitem with its caret, depth
       indentation, focus and selected/active styling. It knows nothing about Project/Group/
       Flow — each Row decides what select/activate/toggle mean and fills the default slot
       with its own icon/label. -->
  <li
    role="treeitem"
    class="explorer-node"
    :aria-level="level"
    :aria-expanded="expandable ? expanded : undefined"
    :aria-selected="selected"
    :aria-current="active ? 'true' : undefined"
    :aria-label="label"
    tabindex="0"
    data-explorer-row
    :data-row-id="rowId"
    :data-test="testId"
    @keydown="onKeydown"
  >
    <div
      class="explorer-row"
      :class="{ 'is-selected': selected, 'is-active': active }"
      :style="{ paddingLeft: `${indent}px` }"
      :title="label"
      @click.stop="onRowClick"
      @dblclick.stop="onRowDblclick"
    >
      <span
        class="explorer-caret"
        :class="{ 'is-open': expandable && expanded }"
        data-test="project-explorer-caret"
        aria-hidden="true"
        @click.stop="onCaretClick"
        @dblclick.stop
      >
        <svg v-if="expandable" viewBox="0 0 16 16" width="12" height="12">
          <path d="M6 4l4 4-4 4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </span>
      <slot />
    </div>
    <ul v-if="expandable && expanded" role="group" class="explorer-children">
      <slot name="children" />
    </ul>
  </li>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  rowId: string
  /** aria-level: 1 = Project, 2 = Group, 3 = Flow. Also drives the indentation. */
  level: number
  label: string
  expandable: boolean
  expanded: boolean
  selected: boolean
  active?: boolean
  testId?: string
}>()

const emit = defineEmits<{
  select: []
  activate: []
  toggle: []
}>()

const INDENT_STEP = 14
const indent = computed(() => 6 + (props.level - 1) * INDENT_STEP)

// A real double click fires click(detail=1), click(detail=2), dblclick. Single click =
// select, double click = select + activate (0023 TR0004 Q #373): the first click selects at
// once, the second click of the same double click (detail>1) adds nothing, and dblclick adds
// the activate intent on top of the selection already in place. Nothing waits on a timer, so
// the contract does not depend on the OS double-click speed.
function onRowClick(e: MouseEvent) {
  if (e.detail > 1) return
  emit('select')
}

function onRowDblclick() {
  emit('activate')
}

function onCaretClick() {
  if (props.expandable) emit('toggle')
}

// Keys are only handled when this row itself has focus: a nested treeitem's keydown bubbles
// through every ancestor row, which must not react to it. Up/Down/Home/End are left to
// bubble so ProjectTree can move focus across the visible rows.
function onKeydown(e: KeyboardEvent) {
  if (e.target !== e.currentTarget) return
  switch (e.key) {
    case 'ArrowRight':
      e.preventDefault()
      if (props.expandable && !props.expanded) emit('toggle')
      break
    case 'ArrowLeft':
      e.preventDefault()
      if (props.expandable && props.expanded) emit('toggle')
      else focusParentRow(e.currentTarget as HTMLElement)
      break
    case 'Enter':
      e.preventDefault()
      emit('activate')
      break
    case ' ':
      e.preventDefault()
      emit('select')
      break
  }
}

function focusParentRow(el: HTMLElement) {
  const parent = el.parentElement?.closest<HTMLElement>('[data-explorer-row]')
  parent?.focus()
}
</script>

<style scoped>
.explorer-node {
  list-style: none;
}

.explorer-node:focus {
  outline: none;
}

.explorer-node:focus-visible > .explorer-row {
  outline: 2px solid var(--fg2-focus);
  outline-offset: -2px;
}

.explorer-row {
  display: flex;
  align-items: center;
  gap: 6px;
  min-height: 28px;
  padding-right: 8px;
  border-radius: var(--fg2-control-radius-sm);
  color: var(--fg2-control-fg);
  font-size: 13px;
  cursor: pointer;
  user-select: none;
}

.explorer-row:hover {
  background: rgba(255, 255, 255, 0.05);
  color: var(--fg2-text);
}

/* selected (single click) and active (Designer-opened Flow) are separate states
   (D0002 §10, 0025 T0006 §5), told apart without any badge or text:
   - selected: neutral highlight only;
   - active:   accent left bar + faint accent tint + weight;
   - both:     the accent tint and bar with the neutral highlight layered on top.
   Hover never overrides either, and keyboard focus is the separate outline above. */
.explorer-row.is-selected {
  background: var(--fg2-line);
  color: var(--fg2-text);
}

.explorer-row.is-active {
  background: var(--fg2-status-info-bg);
  box-shadow: inset 2px 0 0 var(--fg2-blue);
  color: var(--fg2-text);
  font-weight: 650;
}

.explorer-row.is-selected.is-active {
  background: linear-gradient(var(--fg2-line), var(--fg2-line)), var(--fg2-status-info-bg);
}

.explorer-caret {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  width: 14px;
  height: 14px;
  color: var(--fg2-muted);
  transition: transform 0.12s ease;
}

.explorer-caret.is-open {
  transform: rotate(90deg);
}

.explorer-children {
  margin: 0;
  padding: 0;
}
</style>
