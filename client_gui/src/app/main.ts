import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { isNavigationFailure, NavigationFailureType } from 'vue-router'
import i18n from '@shared/i18n'
import router from './router'
import { registerAppMessages } from './i18n'
import './styles/theme.css'
import App from './App.vue'
import StartupLoadingView from './views/StartupLoadingView.vue'
import { ensureValidAccessToken, startTokenAutoRefresh } from '@shared/api'
import { loadCurrentUser, setCurrentUser } from '@/core/auth/currentUser'

// Mirrors client/src/main/main.ts's bootstrap exactly (T0005 §5: auth reuse, not a
// second policy): recover the access token for an F5 refresh, rotate it up-front if
// it is near/already expired, and validate it once against the server before the
// router guard makes its first decision.
const getStoredAccessToken = (): string | null =>
  window.__accessToken__ || sessionStorage.getItem('fg_access_token') || null

async function initializeApp() {
  registerAppMessages(i18n)

  // flowgate.v02.0076 T#1 (D §1.2, §5.2 V03): until /auth/me answers, the start screen shows
  // only a neutral loading state. The router is not installed yet, so no route — and no
  // settings view, project picker or system/project request — exists before the answer.
  let startup: ReturnType<typeof createApp> | null = null
  const token = getStoredAccessToken()
  if (token) {
    window.__accessToken__ = token
    sessionStorage.setItem('fg_access_token', token)

    startup = createApp(StartupLoadingView, { label: i18n.global.t('gui.common.loading') })
    startup.mount('#app')

    await ensureValidAccessToken()

    try {
      // The /auth/me validation request also tells the Topbar who is signed in.
      await loadCurrentUser()
      startTokenAutoRefresh()
    } catch {
      // Token is invalid/expired — clear it so the router guard sends us to /login.
      setCurrentUser(null)
      sessionStorage.removeItem('fg_access_token')
      delete window.__accessToken__
    }
  }

  const app = createApp(App)
  app.use(createPinia())
  app.use(i18n)
  app.use(router)

  // Wait for the router (and its auth guard) to resolve before mounting, so an
  // unauthenticated deep-link never flashes the placeholder before redirecting.
  try {
    await router.isReady()
  } catch (error) {
    // Logged out: the auth guard has already handed the browser to /login (full-page
    // redirect in flight) and aborted this initial navigation, so isReady() rejects with
    // an `aborted` NavigationFailure. Nothing to mount — don't let it surface as an
    // "Uncaught (in promise) Error" in the console on every logged-out /app visit.
    // The startup loading state stays up while the browser leaves for /login.
    if (isNavigationFailure(error, NavigationFailureType.aborted)) return
    startup?.unmount()
    throw error
  }

  startup?.unmount()
  app.mount('#app')
}

initializeApp()
