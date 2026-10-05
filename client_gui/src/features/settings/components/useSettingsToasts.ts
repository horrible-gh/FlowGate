import { ref } from 'vue'
import type { ToastHostItem } from '@/components/ui/ToastHost.vue'

/** Screen-local toast queue for a settings view (rendered with ToastHost). */
export function useSettingsToasts() {
  const toasts = ref<ToastHostItem[]>([])
  let seq = 0

  function pushToast(message: string, tone: ToastHostItem['tone'] = 'success'): void {
    seq += 1
    toasts.value = [...toasts.value, { id: `settings-${seq}`, message, tone, duration: 3200 }]
  }

  function dismissToast(id: string | number): void {
    toasts.value = toasts.value.filter((item) => item.id !== id)
  }

  return { toasts, pushToast, dismissToast }
}

/** Shared state of the "자동 저장" indicator. */
export function useAutosave() {
  const state = ref<'idle' | 'saving' | 'error'>('idle')

  async function run<T>(task: () => Promise<T>): Promise<T> {
    state.value = 'saving'
    try {
      const result = await task()
      state.value = 'idle'
      return result
    } catch (error) {
      state.value = 'error'
      throw error
    }
  }

  return { state, run }
}
