// flowgate.v02.0047 — open state of one composer popover (model picker, settings, ⋯ menu).
// A press outside `root` or Esc closes it, so opening one popover closes the others.
import { onBeforeUnmount, onMounted, ref, type Ref } from 'vue'

export function usePopover(root: Ref<HTMLElement | null>) {
  const open = ref(false)

  function onDocPointer(event: PointerEvent): void {
    if (open.value && root.value && !root.value.contains(event.target as Node)) open.value = false
  }

  function onDocKey(event: KeyboardEvent): void {
    if (open.value && event.key === 'Escape') open.value = false
  }

  onMounted(() => {
    document.addEventListener('pointerdown', onDocPointer)
    document.addEventListener('keydown', onDocKey)
  })

  onBeforeUnmount(() => {
    document.removeEventListener('pointerdown', onDocPointer)
    document.removeEventListener('keydown', onDocKey)
  })

  return {
    open,
    toggle: () => {
      open.value = !open.value
    },
    close: () => {
      open.value = false
    },
  }
}
