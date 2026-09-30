<template>
  <div ref="rootRef" class="ui-resizablepane" :class="`ui-resizablepane--${edge}`" :style="paneStyle">
    <div class="ui-resizablepane-body"><slot /></div>
    <div
      v-if="resizable"
      class="ui-resizablepane-handle"
      :class="`ui-resizablepane-handle--${edge}`"
      role="separator"
      :aria-orientation="ariaOrientation"
      tabindex="0"
      :aria-valuenow="Math.round(size)"
      :aria-valuemin="minSize"
      :aria-valuemax="Math.round(effectiveMaxSize)"
      @mousedown="onMouseDown"
      @keydown="onKeyDown"
    />
  </div>
</template>

<script setup lang="ts">
// Domain-free single-panel resize primitive (T0010 §6): one panel with one draggable
// edge, independent of any sibling ("상대 영역의 의미를 모름", T0010 §7). It never knows
// whether it wraps a Designer Inspector or a Git Workspace panel (T0010 §4/§6.4), and
// never persists its own size (T0010 §16: no localStorage/API/store).
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { createAxisResize } from '../../core/interaction/axisResize'

export type ResizeEdge = 'left' | 'right' | 'top' | 'bottom'

const props = withDefaults(
  defineProps<{
    /** Which edge the drag handle sits on and which dimension it resizes: `left`/`right`
     *  resize width, `top`/`bottom` resize height. */
    edge?: ResizeEdge
    initialSize?: number
    minSize?: number
    maxSize?: number
    /** When false, the resize handle is not rendered at all -- resize is impossible, not
     *  merely visually disabled (T0010 §6.3 "disabled 상태에서는 resize 금지"). */
    resizable?: boolean
    step?: number
  }>(),
  {
    edge: 'right',
    initialSize: 240,
    minSize: 80,
    maxSize: Number.POSITIVE_INFINITY,
    resizable: true,
    step: 16,
  },
)

const emit = defineEmits<{
  resize: [size: number]
  'resize-end': [size: number]
  'update:size': [size: number]
}>()

const rootRef = ref<HTMLElement | null>(null)
const isHorizontalEdge = computed(() => props.edge === 'left' || props.edge === 'right')
// Dragging a `right`/`bottom` handle away from the panel increases size; dragging a
// `left`/`top` handle does the opposite, since the handle sits on the panel's near edge
// in the direction the pointer coordinate decreases when growing. Folding the sign into
// the pointer reader (rather than into the shared helper) keeps createAxisResize generic.
const sign = computed(() => (props.edge === 'right' || props.edge === 'bottom' ? 1 : -1))
const ariaOrientation = computed<'horizontal' | 'vertical'>(() => (isHorizontalEdge.value ? 'vertical' : 'horizontal'))

// Bumped by the window `resize` listener, a ResizeObserver on the parent element, and
// once on mount, purely so the `effectiveMaxSize` computed below has a reactive
// dependency to invalidate its cache on -- `getBoundingClientRect()` reads plain DOM
// state that Vue cannot track on its own (same technique as SplitPane's
// `containerResizeTick`).
const resizeTick = ref(0)

// How far the pane's resizing edge could move before it would reach the *viewport*
// boundary on that side (T0010 §6.3 "viewport/parent 경계 고려"). This must account for
// the pane's own on-screen position, not just the raw viewport length: a `right`-edge
// pane whose left edge already sits at x=300 in a 500px-wide viewport only has 200px of
// room to its right, not the full 500. Falls back to the full viewport length before
// mount, when `rootRef` is not yet measurable.
function viewportExtent(): number {
  void resizeTick.value
  const el = rootRef.value
  const fallback = isHorizontalEdge.value ? window.innerWidth : window.innerHeight
  if (!el) return fallback
  const elRect = el.getBoundingClientRect()
  let extent: number
  switch (props.edge) {
    case 'right':
      extent = window.innerWidth - elRect.left
      break
    case 'left':
      extent = elRect.right
      break
    case 'bottom':
      extent = window.innerHeight - elRect.top
      break
    default:
      extent = elRect.bottom
  }
  // A non-positive extent means the element has not actually been laid out in this
  // environment (e.g. jsdom without an explicit rect stub, where every rect reads as all
  // zeroes) rather than a genuine zero-width viewport -- fall back to the un-positioned
  // viewport length, the same "not measurable, don't apply this bound" convention
  // `parentGrowthExtent()` already uses.
  return extent > 0 ? extent : fallback
}

// How far the pane's resizing edge could move before it would push the *other*,
// anchored edge past the parent's boundary (T0010 §6.3 "viewport/parent 경계 고려").
// Returns 0 (= "not measurable, ignore this bound") when there is no parent or the
// environment does not lay elements out (e.g. jsdom without an explicit rect stub).
function parentGrowthExtent(): number {
  void resizeTick.value
  const el = rootRef.value
  const parent = el?.parentElement ?? null
  if (!el || !parent) return 0
  const parentRect = parent.getBoundingClientRect()
  const elRect = el.getBoundingClientRect()
  if (isHorizontalEdge.value) {
    return props.edge === 'right' ? parentRect.right - elRect.left : elRect.right - parentRect.left
  }
  return props.edge === 'bottom' ? parentRect.bottom - elRect.top : elRect.bottom - parentRect.top
}

// The single source of truth for "how big can this pane get right now" -- used by drag
// clamp, the End key, and the `aria-valuemax` the template exposes, so the three can
// never disagree the way `aria-valuemax` and the actual reachable size did before this
// fix (rejection: viewport-only aria-valuemax vs. viewport+parent-aware clamp/End).
function currentMaxSize(): number {
  let max = Math.min(props.maxSize, viewportExtent())
  const parentBound = parentGrowthExtent()
  if (parentBound > 0) max = Math.min(max, parentBound)
  return max
}

// `currentMaxSize()` can legitimately come out below `minSize` (a viewport/parent that's
// simply too small to fit the configured minimum). Conflict policy: `minSize` always
// wins, so clamp/End/aria-valuemax never disagree with aria-valuemin and never report an
// inverted `[min, max]` range -- the pane may still overflow its cramped container in
// that case, but its own advertised size contract stays internally consistent.
function effectiveMax(): number {
  const max = currentMaxSize()
  const bounded = Number.isFinite(max) ? max : props.minSize
  return Math.max(props.minSize, bounded)
}

const effectiveMaxSize = computed(() => effectiveMax())

function clampSize(value: number): number {
  return Math.min(effectiveMax(), Math.max(props.minSize, value))
}

const size = ref(clampSize(props.initialSize))

const paneStyle = computed(() =>
  isHorizontalEdge.value ? { width: `${size.value}px` } : { height: `${size.value}px` },
)

const axisResize = createAxisResize({
  getValue: () => size.value,
  getPointerPos: (event) => sign.value * (isHorizontalEdge.value ? event.clientX : event.clientY),
  clamp: clampSize,
  onChange: (value) => {
    size.value = value
    emit('resize', value)
    emit('update:size', value)
  },
  onEnd: (value) => emit('resize-end', value),
})

function onMouseDown(event: MouseEvent): void {
  if (!props.resizable) return
  axisResize.onMouseDown(event)
}

function onKeyDown(event: KeyboardEvent): void {
  if (!props.resizable) return
  // Arrow key that visually points "toward growing the panel" always increases size,
  // regardless of which edge the handle is on.
  const growKey = props.edge === 'right' ? 'ArrowRight' : props.edge === 'left' ? 'ArrowLeft' : props.edge === 'bottom' ? 'ArrowDown' : 'ArrowUp'
  const shrinkKey = props.edge === 'right' ? 'ArrowLeft' : props.edge === 'left' ? 'ArrowRight' : props.edge === 'bottom' ? 'ArrowUp' : 'ArrowDown'
  axisResize.onKeyDown(event, props.step, shrinkKey, growKey, props.minSize, effectiveMaxSize.value)
}

function reclamp(): void {
  const clamped = clampSize(size.value)
  if (clamped !== size.value) size.value = clamped
}

// Shared by the window `resize` listener, the parent ResizeObserver, and the initial
// mount measurement: all three mean "the reachable bound may have changed", so all three
// bump the tick (so `effectiveMaxSize` re-evaluates against fresh
// `getBoundingClientRect()` reads) and reclamp the same way.
function onExternalResize(): void {
  resizeTick.value += 1
  reclamp()
}

let resizeObserver: ResizeObserver | null = null

onMounted(() => {
  // `rootRef`/its parent are only measurable once mounted; routing the first
  // measurement through `onExternalResize()` (not a bare `reclamp()`) bumps the tick so
  // `effectiveMaxSize` reflects the real layout immediately, without waiting for a
  // `window resize`.
  onExternalResize()
  window.addEventListener('resize', onExternalResize)
  // `window resize` alone misses a parent-only resize that never touches the viewport
  // (a flex/grid sibling growing, a sidebar collapsing) -- the same gap SplitPane's
  // container ResizeObserver closes. Observing the parent (not `rootRef` itself) matches
  // what `parentGrowthExtent()`/`viewportExtent()` actually measure. Not every
  // environment implements ResizeObserver, so this is additive to the `window resize`
  // listener above, not a replacement.
  const parent = rootRef.value?.parentElement
  if (typeof ResizeObserver !== 'undefined' && parent) {
    resizeObserver = new ResizeObserver(onExternalResize)
    resizeObserver.observe(parent)
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onExternalResize)
  resizeObserver?.disconnect()
  resizeObserver = null
  axisResize.dispose()
})

defineExpose({ size })
</script>

<style scoped>
.ui-resizablepane {
  position: relative;
  min-width: 0;
  min-height: 0;
  flex: none;
}

.ui-resizablepane-body {
  width: 100%;
  height: 100%;
  overflow: auto;
}

.ui-resizablepane-handle {
  position: absolute;
  background: transparent;
}

.ui-resizablepane-handle--left,
.ui-resizablepane-handle--right {
  top: 0;
  bottom: 0;
  width: var(--fg2-splitter-size);
  cursor: col-resize;
}

.ui-resizablepane-handle--left {
  left: 0;
  transform: translateX(-50%);
}

.ui-resizablepane-handle--right {
  right: 0;
  transform: translateX(50%);
}

.ui-resizablepane-handle--top,
.ui-resizablepane-handle--bottom {
  left: 0;
  right: 0;
  height: var(--fg2-splitter-size);
  cursor: row-resize;
}

.ui-resizablepane-handle--top {
  top: 0;
  transform: translateY(-50%);
}

.ui-resizablepane-handle--bottom {
  bottom: 0;
  transform: translateY(50%);
}

.ui-resizablepane-handle:hover,
.ui-resizablepane-handle:focus-visible {
  background: var(--fg2-splitter-hover-bg);
}

.ui-resizablepane-handle:active {
  background: var(--fg2-splitter-active-bg);
}
</style>
