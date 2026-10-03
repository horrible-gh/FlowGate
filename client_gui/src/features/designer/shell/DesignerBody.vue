<template>
  <!-- Horizontal axis of the Designer: Left ↔ Center ↔ Right (0003-D §3.7.2). Left / Right
       get fixed widths from the layout state; the Center takes everything left over, down to
       its minimum. Narrower than that, only the Body scrolls sideways — columns never
       overlap. -->
  <div
    class="designer-body"
    data-test="designer-body"
    :data-left-width="leftWidth"
    :data-right-width="rightWidth"
    :style="columns"
  >
    <slot />
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { DESIGNER_CENTER_MIN_WIDTH } from './designerLayout'

const props = defineProps<{
  /** Current left column width (expanded width or rail). */
  leftWidth: number
  /** Current right column width (expanded width or rail). */
  rightWidth: number
}>()

const columns = computed(() => ({
  gridTemplateColumns: `${props.leftWidth}px minmax(${DESIGNER_CENTER_MIN_WIDTH}px, 1fr) ${props.rightWidth}px`,
}))
</script>

<style scoped>
.designer-body {
  display: grid;
  grid-template-rows: minmax(0, 1fr);
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  overflow-x: auto;
  overflow-y: hidden;
}
</style>
