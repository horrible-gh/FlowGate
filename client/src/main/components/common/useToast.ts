import { ref } from 'vue'

export type ToastType = 'success' | 'info' | 'warning' | 'danger' | 'error'

export interface ToastItem {
  id: number
  message: string
  type: ToastType
}

const toasts = ref<ToastItem[]>([])
let _nextId = 0

export function useToast() {
  function showToast(message: string, type: ToastType = 'info') {
    const id = ++_nextId
    toasts.value.push({ id, message, type })
    setTimeout(() => {
      toasts.value = toasts.value.filter((t) => t.id !== id)
    }, 3000)
  }

  return { toasts, showToast }
}
