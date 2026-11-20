import { onScopeDispose, ref, watch } from 'vue'

const RETRY_EVENT = 'fg:get_retry'
export interface RetryNotice { path: string; projectId?: string; groupId?: string; typeCode?: string }

// Only routing fields leave the transport; never publish headers, tokens or arbitrary query values.
export function notifyGetRetry(url: string, params?: Record<string, unknown>) {
  if (typeof window === 'undefined') return
  const path = new URL(url, window.location.href).pathname
  window.dispatchEvent(new CustomEvent<RetryNotice>(RETRY_EVENT, { detail: {
    path, projectId: typeof params?.project_id === 'string' ? params.project_id : undefined,
    groupId: typeof params?.group_id === 'string' ? params.group_id : undefined,
    typeCode: typeof params?.type_code === 'string' ? params.type_code : undefined,
  } }))
}

export function useRequestRecovery(scope: () => string, loading: () => boolean, matches: (notice: RetryNotice) => boolean) {
  const reconnecting = ref(false)
  const retry = (event: Event) => {
    if (loading() && matches((event as CustomEvent<RetryNotice>).detail)) reconnecting.value = true
  }
  watch([scope, loading], () => { reconnecting.value = false }, { flush: 'sync' })
  if (typeof window !== 'undefined') window.addEventListener(RETRY_EVENT, retry)
  onScopeDispose(() => {
    if (typeof window !== 'undefined') window.removeEventListener(RETRY_EVENT, retry)
  })
  return reconnecting
}
