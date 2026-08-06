import { ref } from 'vue'

export type ToastType = 'success' | 'info' | 'warning' | 'danger' | 'error'

export interface ToastItem {
  id: number
  message: string
  type: ToastType
}

const toasts = ref<ToastItem[]>([])
let _nextId = 0

/** Default life of a toast: enough for a short confirmation ("복사했습니다"). Callers
 *  with an ACTIONABLE message — e.g. a server rejection that explains how to fix the
 *  request (0391 T0005 §7-6) — pass their own, longer duration instead: 3s is not
 *  enough to read ~150 characters of instructions. */
const TOAST_DEFAULT_MS = 3000

export function useToast() {
  function dismissToast(id: number) {
    toasts.value = toasts.value.filter((t) => t.id !== id)
  }

  function showToast(message: string, type: ToastType = 'info', durationMs = TOAST_DEFAULT_MS) {
    const id = ++_nextId
    toasts.value.push({ id, message, type })
    setTimeout(() => dismissToast(id), durationMs)
  }

  return { toasts, showToast, dismissToast }
}
