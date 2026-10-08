<template>
  <!-- flowgate.v02.0046 — the drag handle on a Dock boundary. A thin strip the Body /
       CenterColumn place over the line between a Dock and the Center; it reports where the
       pointer wants that Dock's size to be and owns no size itself. -->
  <div
    class="designer-resize-handle"
    :class="`is-${axis}`"
    role="separator"
    :aria-orientation="axis === 'x' ? 'vertical' : 'horizontal'"
    @mousedown="drag.onMouseDown"
  />
</template>

<script setup lang="ts">
// The drag lifecycle (primary button only, document listeners, text-selection guard, cleanup)
// is the shared single-axis resize interaction SplitPane / ResizablePane use. The value is
// the Dock size the parent passed in; the parent clamps what this handle reports against its
// own measured size and the layout bounds (designerLayout.ts), so nothing here is clamped.
import { onBeforeUnmount } from 'vue'
import { createAxisResize } from '../../../core/interaction/axisResize'

const props = defineProps<{
  /** `x` resizes a width (vertical line), `y` a height (horizontal line). */
  axis: 'x' | 'y'
  /** The current size of the Dock this handle resizes. */
  size: number
  /** 1 when moving the pointer toward +x / +y grows the Dock, -1 when it shrinks it. */
  grow: 1 | -1
}>()

const emit = defineEmits<{ resize: [size: number] }>()

const drag = createAxisResize({
  getValue: () => props.size,
  getPointerPos: (event) => props.grow * (props.axis === 'x' ? event.clientX : event.clientY),
  clamp: (value) => value,
  onChange: (value) => emit('resize', value),
  onEnd: () => {},
})

onBeforeUnmount(() => drag.dispose())
</script>

<style scoped>
.designer-resize-handle {
  position: relative;
  z-index: 3;
  background: transparent;
}

.designer-resize-handle.is-x {
  width: var(--fg2-splitter-size);
  cursor: col-resize;
}

.designer-resize-handle.is-y {
  height: var(--fg2-splitter-size);
  cursor: row-resize;
}

.designer-resize-handle:hover {
  background: var(--fg2-splitter-hover-bg);
}

.designer-resize-handle:active {
  background: var(--fg2-splitter-active-bg);
}
</style>
