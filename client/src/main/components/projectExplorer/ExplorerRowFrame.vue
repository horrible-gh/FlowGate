<template>
  <!-- Thin shared row primitive (D0002 §7.1/§7.2): one treeitem with its caret, focus and
       selected/active styling. It knows nothing about Project/Group/Flow — each Row decides
       what select/activate/toggle mean and fills the default slot with its own icon/label. -->
  <li
    role="treeitem"
    class="tree-node pe-node"
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
      class="tree-row pe-row"
      :class="{ active: selected, 'pe-row--active': active }"
      :title="label"
      @click.stop="onRowClick"
      @dblclick.stop="onRowDblclick"
    >
      <span
        class="tree-caret"
        :class="{ open: expandable && expanded, leaf: !expandable }"
        data-test="project-explorer-caret"
        @click.stop="onCaretClick"
        @dblclick.stop
      >
        <AppIcon v-if="expandable" name="caret-right" />
      </span>
      <slot />
    </div>
    <ul v-if="expandable && expanded" role="group" class="tree-ul tree-children">
      <slot name="children" />
    </ul>
  </li>
</template>

<script setup lang="ts">
import AppIcon from '@shared/AppIcon.vue'

const props = defineProps<{
  rowId: string
  /** aria-level: 1 = Project, 2 = Group, 3 = Flow. */
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

// A real double click fires click(detail=1), click(detail=2), dblclick. Single click =
// select, double click = select + activate (0023 TR0004 Q answer): the first click selects at
// once, the second click of the same double click (detail>1) adds nothing, and dblclick
// adds the activate intent on top of the selection that is already in place. Nothing waits
// on a timer, so the contract does not depend on the OS double-click speed.
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

// Keys are only handled when this row itself has focus: a nested treeitem's keydown
// bubbles through every ancestor row, which must not react to it. Up/Down/Home/End are
// left to bubble so ProjectTree can move focus across the visible rows.
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
.pe-node:focus {
  outline: none;
}
.pe-node:focus-visible > .pe-row {
  outline: 2px solid rgba(96, 165, 250, 0.8);
  outline-offset: -2px;
}
/* selected (single click) and active (Designer-opened Flow) are separate states
   (D0002 §10): selected keeps the shared .tree-row.active background, active adds an
   accent bar and weight so a selected-but-not-active Flow never looks opened. */
.pe-row--active {
  box-shadow: inset 2px 0 0 #60a5fa;
  color: rgba(255, 255, 255, 0.95);
  font-weight: 600;
}
</style>
