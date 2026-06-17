import { createApp } from 'vue'
import { createPinia } from 'pinia'
import i18n from '@shared/i18n'
import router from './router'
import '@shared/variables.css'
import '@shared/app.css'
import App from './App.vue'
import { getRequest, ensureValidAccessToken, startTokenAutoRefresh } from '@shared/api'

// Recover token from sessionStorage if present (for F5 refresh)
const getStoredAccessToken = (): string | null =>
  window.__accessToken__ || sessionStorage.getItem('fg_access_token') || null

async function initializeApp() {
  const token = getStoredAccessToken()
  if (token) {
    window.__accessToken__ = token
    sessionStorage.setItem('fg_access_token', token)

    // The stored access token may already be expired (short-lived tokens — group 0028).
    // Rotate up-front using the refresh token so the startup /auth/me never has to eat a
    // 401, then validate. (T0004 req 2: no 401s)
    await ensureValidAccessToken()

    // Validate token with server on app startup
    try {
      await getRequest('/auth/me')
      // Valid session — begin proactive rotation so the token never expires mid-session.
      startTokenAutoRefresh()
    } catch {
      // Token is invalid/expired, clear it
      sessionStorage.removeItem('fg_access_token')
      delete window.__accessToken__
    }
  }

  const app = createApp(App)
  app.use(createPinia())
  app.use(i18n)
  app.use(router)
  
  // Wait for router to be ready before mounting to prevent premature redirects
  await router.isReady()
  
  app.mount('#app')
}

initializeApp()
