import { serverLogout } from '@shared/api'
import { announceAuthSessionEnded } from '@shared/authSessionSignal'
import { setCurrentUser, useCurrentUser } from './currentUser'

// Sign out of this browser: the existing POST /auth/logout
// through the shared serverLogout(), then the same local clean-up v0.1's UserMenu does. The
// current session is ended this way, never by DELETE /auth/sessions/{sid} (the server answers
// that with 409 current_session_use_logout).
//
// serverLogout() settles even when the server call fails (it only logs). The tokens are dropped
// either way, so this browser is signed out; nothing here reports the server call as a success.

const LOGIN_PATH = '/login'

// the user's keys of this browser tab's sessionStorage go too (the Designer's F5 snapshot).
export function clearStoredAuth(username: string | null): void {
  if (username) {
    for (const storage of [localStorage, sessionStorage]) {
      Object.keys(storage).forEach((key) => {
        if (key.startsWith(`flowgate.user.${username}.`)) storage.removeItem(key)
      })
    }
  }
  localStorage.removeItem('flowgate.token')
  localStorage.removeItem('fg_refresh_token')
  localStorage.removeItem('fg_refresh_result')
  sessionStorage.removeItem('fg_access_token')
  sessionStorage.removeItem('fg_refresh_token')
  delete window.__accessToken__
}

export async function logout(): Promise<void> {
  const username = useCurrentUser().user.value?.username ?? null
  // stop the session's work BEFORE the server call, so nothing new (a Flow attachment
  // delete whose five seconds run out) is sent while the sign-out is on the way.
  announceAuthSessionEnded()
  try {
    await serverLogout()
  } finally {
    clearStoredAuth(username)
    setCurrentUser(null)
    window.location.href = LOGIN_PATH
  }
}
