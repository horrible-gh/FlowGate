// "this browser's signed-in session is ending" — one window event every auth clean-up path
// sends (sign-out before the server call, the shared token clean-up, a sign-out in another tab, a rejected refresh).
// Work that belongs to the session (the Flow attachment work manager) listens and drops what it has not sent yet.
// An event keeps shared/ and core/ free of feature imports.
export const AUTH_SESSION_ENDED_EVENT = 'fg:auth_session_ended'

export function announceAuthSessionEnded(): void {
  if (typeof window === 'undefined') return
  window.dispatchEvent(new CustomEvent(AUTH_SESSION_ENDED_EVENT))
}
