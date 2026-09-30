<template>
  <span ref="wrapRef" class="ui-tooltip-wrap" @mouseenter="show" @mouseleave="hide" @focusin="show" @focusout="hide" @keydown.esc="hide">
    <slot />
  </span>
  <Teleport to="body">
    <span v-if="visible && text" :id="tooltipId" class="ui-tooltip" role="tooltip" :style="bubbleStyle">{{ text }}</span>
  </Teleport>
</template>

<script setup lang="ts">
// Domain-free hover/focus tooltip (T0004 §5.8). The bubble is teleported to <body> and
// positioned from the trigger's real measured rect (not a container-relative guess), so it
// is never clipped by an ancestor's overflow:hidden/auto and never fights App Shell
// z-index. `display: contents` on the wrapper keeps it invisible to the trigger's own
// layout (e.g. a flex row), so wrapping never changes sizing/flex-item behaviour. Vue keeps
// this component's scope attribute on teleported nodes, so `<style scoped>` still applies
// after the bubble moves under <body>.
//
// Accessibility: role="tooltip" alone does not connect the bubble to whatever control the
// caller put in the default slot — a screen reader on the focused trigger never learns the
// description exists. This component owns that connection itself: it stamps its own stable
// id onto the bubble and sets/clears `aria-describedby` on the trigger's first element in
// lockstep with show()/hide(), so the caller never has to wire it by hand.
import { ref } from 'vue'

let uidCounter = 0
const tooltipId = `ui-tooltip-${++uidCounter}`

const props = defineProps<{ text?: string; placement?: 'top' | 'bottom' }>()

const wrapRef = ref<HTMLElement | null>(null)
const visible = ref(false)
const bubbleStyle = ref<Record<string, string>>({})

function updatePosition() {
  const target = wrapRef.value?.firstElementChild as HTMLElement | null
  if (!target) return
  const rect = target.getBoundingClientRect()
  const placement = props.placement ?? 'top'
  const top = placement === 'top' ? rect.top - 8 : rect.bottom + 8
  bubbleStyle.value = {
    position: 'fixed',
    left: `${rect.left + rect.width / 2}px`,
    top: `${top}px`,
    transform: placement === 'top' ? 'translate(-50%, -100%)' : 'translate(-50%, 0)',
  }
}

function triggerEl(): HTMLElement | null {
  return wrapRef.value?.firstElementChild as HTMLElement | null
}

function show() {
  if (!props.text) return
  updatePosition()
  visible.value = true
  const el = triggerEl()
  if (!el) return
  const tokens = (el.getAttribute('aria-describedby') ?? '').split(/\s+/).filter(Boolean)
  if (!tokens.includes(tooltipId)) {
    el.setAttribute('aria-describedby', [...tokens, tooltipId].join(' '))
  }
}

function hide() {
  visible.value = false
  const el = triggerEl()
  if (!el) return
  const tokens = (el.getAttribute('aria-describedby') ?? '')
    .split(/\s+/)
    .filter((token) => token && token !== tooltipId)
  if (tokens.length) {
    el.setAttribute('aria-describedby', tokens.join(' '))
  } else {
    el.removeAttribute('aria-describedby')
  }
}

defineExpose({ show, hide })
</script>

<style scoped>
.ui-tooltip-wrap {
  display: contents;
}

.ui-tooltip {
  z-index: var(--fg2-overlay-z);
  max-width: 240px;
  padding: 6px 10px;
  border-radius: 6px;
  border: 1px solid var(--fg2-line-strong);
  background: #0b1624;
  color: var(--fg2-text);
  font-size: 12px;
  line-height: 1.5;
  white-space: normal;
  pointer-events: none;
}
</style>
