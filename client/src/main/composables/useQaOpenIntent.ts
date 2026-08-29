import { readonly, ref } from 'vue'

export interface QaOpenIntent {
  docId: string
  sequence: number
}

const intent = ref<QaOpenIntent | null>(null)
let sequence = 0

export function useQaOpenIntent() {
  function requestQaOpen(docId: string): QaOpenIntent {
    const next = { docId, sequence: ++sequence }
    intent.value = next
    return next
  }

  function consumeQaOpen(docId: string, expectedSequence: number): boolean {
    const current = intent.value
    if (!current || current.docId !== docId || current.sequence !== expectedSequence) return false
    intent.value = null
    return true
  }

  return { intent: readonly(intent), requestQaOpen, consumeQaOpen }
}