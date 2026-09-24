import { createApp } from 'vue'
import { createPinia } from 'pinia'
import i18n from '@shared/i18n'
import router from './router'
import '@shared/variables.css'
import '@shared/app.css'
import App from './App.vue'
import { getRequest, ensureValidAccessToken, startTokenAutoRefresh } from '@shared/api'

// Mirrors client/src/main/main.ts's bootstrap exactly (T0005 §5: auth reuse, not a
// second policy): recover the access token for an F5 refresh, rotate it up-front if
// it is near/already expired, and validate it once against the server before the
// router guard makes its first decision.
const getStoredAccessToken = (): string | null =>
  window.__accessToken__ || sessionStorage.getItem('fg_access_token') || null

async function initializeApp() {
  const token = getStoredAccessToken()
  if (token) {
    window.__accessToken__ = token
    sessionStorage.setItem('fg_access_token', token)

    await ensureValidAccessToken()

    try {
      await getRequest('/auth/me')
      startTokenAutoRefresh()
    } catch {
      // Token is invalid/expired — clear it so the router guard sends us to /login.
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
  await router.isReady()

  app.mount('#app')
}

initializeApp()
