// v02test.0090 (F90-12): long settings lists (사용자 · 워크스페이스 멤버 · 프로젝트 멤버) show 20 rows
// at a time. The list is split on screen — the server still returns the whole list. The footer reads
// "1–20 / 34" (the range shown / the rows that pass the current search and filters) with only a
// previous / next button next to it; both are off when everything fits on one page.
// Any change of the search or filters (`resetOn`) goes back to the first page, and a list that shrinks
// under the current page (a member removed, a re-read) is clamped to its last page.
import { computed, ref, watch, type WatchSource } from 'vue'

export const SETTINGS_PAGE_SIZE = 20

export function usePagedList<T>(items: { readonly value: readonly T[] }, resetOn: WatchSource[] = [], size = SETTINGS_PAGE_SIZE) {
  const page = ref(0)
  const total = computed(() => items.value.length)
  const pageCount = computed(() => Math.max(1, Math.ceil(total.value / size)))

  watch(pageCount, (count) => {
    if (page.value > count - 1) page.value = count - 1
  })
  if (resetOn.length) {
    watch(resetOn, () => {
      page.value = 0
    })
  }

  const pageItems = computed(() => items.value.slice(page.value * size, page.value * size + size))
  const hasPrev = computed(() => page.value > 0)
  const hasNext = computed(() => page.value < pageCount.value - 1)
  const rangeLabel = computed(() => {
    if (!total.value) return '0 / 0'
    const from = page.value * size + 1
    const to = Math.min(total.value, from + size - 1)
    return `${from}–${to} / ${total.value}`
  })

  function prev() {
    if (hasPrev.value) page.value -= 1
  }
  function next() {
    if (hasNext.value) page.value += 1
  }

  return { page, pageItems, hasPrev, hasNext, rangeLabel, prev, next }
}
