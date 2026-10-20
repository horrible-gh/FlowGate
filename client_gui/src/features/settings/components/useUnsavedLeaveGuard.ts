import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute } from 'vue-router'
import { currentSettingsAccess } from '../access'

// 미저장 이탈 보호 (flowgate.v02.0076 D §3.2), shared by the settings screens that edit something
// (시스템 › 일반 since T#3, 시스템 Agent since T#4). While `unsaved()` holds:
//
// - leaving by the app (another route) is held and `open` asks first; `answer(false)` keeps the
//   screen, `answer(true)` lets the move go — nothing is saved or re-sent either way;
// - a refresh / tab close gets the browser's own question (beforeunload).
//
// It never holds the screen once the session or the administrator role is gone (sign-out, a lost
// token, the role taken away): those moves go through at once, and an open question is answered
// "leave". `ask()` lets the view put the same question for a switch inside the screen.
export function useUnsavedLeaveGuard(unsaved: () => boolean) {
  const route = useRoute()
  const open = ref(false)
  let pending: ((leave: boolean) => void) | null = null

  // The same token check the auth guard makes (core/router/guard).
  const hasToken = () => !!(window.__accessToken__ || sessionStorage.getItem('fg_access_token'))

  /** Hold a move only while this administrator screen is still theirs and something is unsaved. */
  function holds(at: Parameters<typeof currentSettingsAccess>[0]): boolean {
    return unsaved() && hasToken() && currentSettingsAccess(at).kind === 'allow'
  }

  function answer(leave: boolean) {
    open.value = false
    const resolve = pending
    pending = null
    resolve?.(leave)
  }

  /** Put the question; resolves true for "leave without saving", false for "keep editing". */
  function ask(): Promise<boolean> {
    answer(false) // an older, unanswered question is superseded by this one
    open.value = true
    return new Promise<boolean>((resolve) => {
      pending = resolve
    })
  }

  onBeforeRouteLeave((_to, from) => (holds(from) ? ask() : true))

  // The session or the role went away while the question is open: let the move go at once.
  watch(() => currentSettingsAccess(route).kind, (kind) => {
    if (kind !== 'allow') answer(true)
  })

  function onBeforeUnload(event: BeforeUnloadEvent) {
    if (!holds(route)) return
    event.preventDefault()
    event.returnValue = ''
  }

  onMounted(() => {
    window.addEventListener('beforeunload', onBeforeUnload)
  })

  onBeforeUnmount(() => {
    window.removeEventListener('beforeunload', onBeforeUnload)
    answer(true) // the view is going anyway (e.g. the layout hid it); never leave a move hanging
  })

  return { open, answer, ask }
}
