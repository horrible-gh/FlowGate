<template>
  <span ref="triggerWrapRef" class="ui-dropdown-trigger">
    <slot name="trigger" :open="isOpen" :toggle="toggle" :trigger-attrs="triggerAttrs" />
  </span>
  <Teleport to="body">
    <div v-if="isOpen" ref="menuRef" class="ui-dropdown-menu" role="menu" :style="menuStyle" @keydown.esc="close">
      <slot :close="close" />
    </div>
  </Teleport>
</template>

<script setup lang="ts">
// Domain-free menu shell (T0004 §5.9): trigger + open/close + outside click + ESC +
// keyboard focus + a slot for menu item data. It never carries Project/Git/AI menu content
// itself (T0004 §11) — the caller supplies that through the default slot. The menu is
// teleported to <body> and positioned from the trigger's measured rect for the same
// clipping/z-index reasons as Tooltip.vue; Vue keeps this component's scope attribute on
// teleported nodes, so `<style scoped>` still applies after the menu moves under <body>.
//
// Click ownership: the wrapper span never listens for click itself — that would double-fire
// alongside the caller's own `@click="toggle"` on the trigger slot's `toggle` (the click
// bubbles from the trigger element up through the wrapper). `toggle`/`open`/`close` are only
// ever invoked once, by whatever element the caller wires them to via the scoped trigger slot.
import { computed, nextTick, onBeforeUnmount, ref } from 'vue'

const isOpen = ref(false)
const triggerWrapRef = ref<HTMLElement | null>(null)
const menuRef = ref<HTMLElement | null>(null)
const menuStyle = ref<Record<string, string>>({})
const triggerAttrs = computed(() => ({
  'aria-haspopup': 'menu' as const,
  'aria-expanded': isOpen.value ? 'true' : 'false',
}))

function positionMenu() {
  const trigger = triggerWrapRef.value?.firstElementChild as HTMLElement | null
  if (!trigger) return
  const rect = trigger.getBoundingClientRect()
  menuStyle.value = {
    position: 'fixed',
    top: `${rect.bottom + 6}px`,
    left: `${rect.right}px`,
    transform: 'translateX(-100%)',
  }
}

function open() {
  isOpen.value = true
  positionMenu()
  nextTick(() => {
    const first = menuRef.value?.querySelector<HTMLElement>('[role="menuitem"], button, a, [tabindex]')
    first?.focus()
  })
}

function close() {
  if (!isOpen.value) return
  isOpen.value = false
  const trigger = triggerWrapRef.value?.querySelector<HTMLElement>('button, a, [tabindex]')
  trigger?.focus()
}

function toggle() {
  if (isOpen.value) close()
  else open()
}

function onDocMousedown(event: MouseEvent) {
  if (!isOpen.value) return
  const target = event.target as Node
  if (triggerWrapRef.value?.contains(target)) return
  if (menuRef.value?.contains(target)) return
  close()
}

function onDocKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') close()
}

document.addEventListener('mousedown', onDocMousedown)
document.addEventListener('keydown', onDocKeydown)

onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocMousedown)
  document.removeEventListener('keydown', onDocKeydown)
})

defineExpose({ close, isOpen })
</script>

<style scoped>
.ui-dropdown-trigger {
  display: contents;
}

.ui-dropdown-menu {
  z-index: var(--fg2-overlay-z);
  min-width: 180px;
  padding: 6px;
  border-radius: var(--fg2-control-radius);
  border: 1px solid var(--fg2-line-strong);
  background: var(--fg2-control-surface-strong);
  box-shadow: 0 12px 30px rgba(0, 0, 0, 0.4);
}
</style>
