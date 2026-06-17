import { defineStore } from 'pinia'
import { ref } from 'vue'

export interface AppError {
  id: string
  code: number
  message: string
  retry?: (() => void) | null
}

export const useErrorStore = defineStore('error', () => {
  const errors = ref<AppError[]>([])

  function addError(err: Omit<AppError, 'id'>) {
    errors.value.push({ ...err, id: String(Date.now()) })
  }

  function removeError(id: string) {
    errors.value = errors.value.filter((e) => e.id !== id)
  }

  function clearErrors() {
    errors.value = []
  }

  return { errors, addError, removeError, clearErrors }
})
