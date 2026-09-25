<template>
  <div ref="containerRef" class="ui-splitpane" :class="`ui-splitpane--${direction}`">
    <div class="ui-splitpane-pane ui-splitpane-pane--first" :style="firstPaneStyle">
      <slot name="first" />
    </div>
    <div
      class="ui-splitpane-splitter"
      role="separator"
      :aria-orientation="ariaOrientation"
      tabindex="0"
      :aria-valuenow="Math.round(firstSize)"
      :aria-valuemin="minSize"
      :aria-valuemax="Math.round(effectiveMaxSize)"
      @mousedown="onMouseDown"
      @keydown="onKeyDown"
    />
    <div class="ui-splitpane-pane ui-splitpane-pane--second">
      <slot name="second" />
    </div>
  </div>
</template>

<script setup lang="ts">
// Domain-free layout primitive (T0010 §5): splits its container into two areas along one
// axis and lets the user drag (or arrow-key) the boundary between them. It owns the split
// interaction only -- it never knows whether `#first`/`#second` hold Designer panels, Git
// history, or anything else (T0010 §4), and never persists the result itself (T0010 §5.3,
// §16: no localStorage/API/store).
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { createAxisResize } from '../../core/interaction/axisResize'

export type SplitDirection = 'horizontal' | 'vertical'

const props = withDefaults(
  defineProps<{
    /** `horizontal` = panes side by side with a vertical splitter bar (default);
     *  `vertical` = panes stacked with a horizontal splitter bar. */
    direction?: SplitDirection
    /** Initial size, in px, of the first pane (T0010 §5.1 "initial size"). */
    initialSize?: number
    minSize?: number
    maxSize?: number
    /** Minimum px the second pane must keep, so dragging the splitter can never collapse
     *  it to nothing even when `maxSize` alone would allow it. */
    secondMinSize?: number
    /** px moved per arrow-key press (T0010 §5.2 "keyboard로 splitter 조절 가능"). */
    step?: number
  }>(),
  {
    direction: 'horizontal',
    initialSize: 240,
    minSize: 80,
    maxSize: Number.POSITIVE_INFINITY,
    secondMinSize: 80,
    step: 16,
  },
)

const emit = defineEmits<{
  resize: [size: number]
  'resize-end': [size: number]
  'update:size': [size: number]
}>()

const containerRef = ref<HTMLElement | null>(null)
// Bumped by both the window `resize` listener and (when supported) a ResizeObserver on
// the container, so `effectiveMaxSize` below has an actual reactive dependency to
// invalidate its cache on. `containerExtent()` still reads `clientWidth`/`clientHeight`
// straight off the DOM element on every call -- plain DOM properties are not reactive,
// so without reading this ref from inside that call chain, a Vue `computed` would never
// know a parent resize (flex/grid/sidebar change, not just `window` `resize`) happened
// and would keep serving its first-ever cached value forever. Declared before
// `firstSize` below, since that initializer already calls `clampFirstSize` ->
// `containerExtent` synchronously during setup.
const containerResizeTick = ref(0)
const firstSize = ref(clampFirstSize(props.initialSize))

const ariaOrientation = computed<'horizontal' | 'vertical'>(() =>
  props.direction === 'horizontal' ? 'vertical' : 'horizontal',
)

const firstPaneStyle = computed(() => ({ flex: `0 0 ${firstSize.value}px` }))

const SPLITTER_THICKNESS = 6

function containerExtent(): number {
  void containerResizeTick.value
  const el = containerRef.value
  if (!el) return 0
  return props.direction === 'horizontal' ? el.clientWidth : el.clientHeight
}

function currentMaxSize(): number {
  const extent = containerExtent()
  const configuredMax = Math.max(props.minSize, props.maxSize)
  if (extent <= 0) return configuredMax
  const containerBound = Math.max(props.minSize, extent - props.secondMinSize - SPLITTER_THICKNESS)
  return Math.max(props.minSize, Math.min(configuredMax, containerBound))
}

const effectiveMaxSize = computed(() => {
  const max = currentMaxSize()
  return Number.isFinite(max) ? max : props.minSize
})

function clampFirstSize(value: number): number {
  return Math.min(currentMaxSize(), Math.max(props.minSize, value))
}

const axisResize = createAxisResize({
  getValue: () => firstSize.value,
  getPointerPos: (event) => (props.direction === 'horizontal' ? event.clientX : event.clientY),
  clamp: clampFirstSize,
  onChange: (value) => {
    firstSize.value = value
    emit('resize', value)
    emit('update:size', value)
  },
  onEnd: (value) => emit('resize-end', value),
})

function onMouseDown(event: MouseEvent): void {
  axisResize.onMouseDown(event)
}

function onKeyDown(event: KeyboardEvent): void {
  const decreaseKey = props.direction === 'horizontal' ? 'ArrowLeft' : 'ArrowUp'
  const increaseKey = props.direction === 'horizontal' ? 'ArrowRight' : 'ArrowDown'
  axisResize.onKeyDown(event, props.step, decreaseKey, increaseKey, props.minSize, effectiveMaxSize.value)
}

function reclamp(): void {
  const clamped = clampFirstSize(firstSize.value)
  if (clamped !== firstSize.value) firstSize.value = clamped
}

// Shared by the window `resize` listener and the ResizeObserver callback: both mean
// "the container's available space may have changed", so both re-measure (via the
// tick) and reclamp the same way.
function onExternalResize(): void {
  containerResizeTick.value += 1
  reclamp()
}

let resizeObserver: ResizeObserver | null = null

onMounted(() => {
  // `containerRef` is only populated once mounted -- during the initial (pre-mount)
  // evaluation `effectiveMaxSize` measured an unattached container (extent 0) and, with
  // the default `maxSize=Infinity`, fell back to `minSize`. That fallback is a Vue
  // `computed` cached on `containerResizeTick`, so it would otherwise stay wrong forever
  // until the first `window resize`/ResizeObserver callback fired. Routing the initial
  // measurement through `onExternalResize()` (not a bare `reclamp()`) bumps the tick so
  // the computed actually re-evaluates against the now-real `containerRef` immediately.
  onExternalResize()
  window.addEventListener('resize', onExternalResize)
  // Not every environment implements ResizeObserver (e.g. older embedders); the
  // `window resize` listener above still covers the viewport-driven case when it is
  // absent. Where it exists, it also catches parent-driven resizes that never fire a
  // `window` `resize` event at all (flex/grid siblings changing, a sidebar collapsing).
  if (typeof ResizeObserver !== 'undefined' && containerRef.value) {
    resizeObserver = new ResizeObserver(onExternalResize)
    resizeObserver.observe(containerRef.value)
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onExternalResize)
  resizeObserver?.disconnect()
  resizeObserver = null
  axisResize.dispose()
})

defineExpose({ firstSize })
</script>

<style scoped>
.ui-splitpane {
  display: flex;
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
}

.ui-splitpane--horizontal {
  flex-direction: row;
}

.ui-splitpane--vertical {
  flex-direction: column;
}

.ui-splitpane-pane {
  min-width: 0;
  min-height: 0;
  overflow: auto;
}

.ui-splitpane-pane--first {
  flex: none;
}

.ui-splitpane-pane--second {
  flex: 1 1 auto;
}

.ui-splitpane-splitter {
  flex: none;
  position: relative;
  background: var(--fg2-line);
}

.ui-splitpane--horizontal > .ui-splitpane-splitter {
  width: var(--fg2-splitter-size);
  cursor: col-resize;
}

.ui-splitpane--vertical > .ui-splitpane-splitter {
  height: var(--fg2-splitter-size);
  cursor: row-resize;
}

.ui-splitpane-splitter:hover,
.ui-splitpane-splitter:focus-visible {
  background: var(--fg2-splitter-hover-bg);
}

.ui-splitpane-splitter:active {
  background: var(--fg2-splitter-active-bg);
}
</style>
