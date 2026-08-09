import { onBeforeUnmount, onMounted, ref, type Ref } from 'vue'

export type ContentLayoutTier = 'wide' | 'mid' | 'narrow'

export const CONTENT_WIDE_MIN = 1040
export const CONTENT_MID_MIN = 760
export const CONTENT_HYSTERESIS = 24
export const CONTENT_DEBOUNCE_MS = 80

/** L0010 §2.9 — classify the measured content width with 24px hysteresis. */
export function classifyContentWidth(width: number, current: ContentLayoutTier): ContentLayoutTier {
  const wideEnter = CONTENT_WIDE_MIN + CONTENT_HYSTERESIS
  const midEnter = CONTENT_MID_MIN + CONTENT_HYSTERESIS
  if (width <= 0) return 'narrow'
  if (current === 'wide') return width >= CONTENT_MID_MIN ? (width >= CONTENT_WIDE_MIN ? 'wide' : 'mid') : 'narrow'
  if (current === 'mid') {
    if (width >= wideEnter) return 'wide'
    return width >= CONTENT_MID_MIN ? 'mid' : 'narrow'
  }
  if (width >= wideEnter) return 'wide'
  if (width >= midEnter) return 'mid'
  return 'narrow'
}

/** Shared ResizeObserver used by the WP editor and its apply-preview overlay. */
export function useContentLayoutTier(root: Ref<HTMLElement | null>) {
  const layoutTier = ref<ContentLayoutTier>('wide')
  let observer: ResizeObserver | null = null
  let timer: ReturnType<typeof setTimeout> | null = null

  onMounted(() => {
    if (!root.value || typeof ResizeObserver === 'undefined') return
    observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect?.width
      if (typeof width !== 'number') return
      if (timer) clearTimeout(timer)
      timer = setTimeout(() => {
        layoutTier.value = classifyContentWidth(width, layoutTier.value)
      }, CONTENT_DEBOUNCE_MS)
    })
    observer.observe(root.value)
  })
  onBeforeUnmount(() => {
    observer?.disconnect()
    if (timer) clearTimeout(timer)
  })
  return { layoutTier }
}
