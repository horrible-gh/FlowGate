<template>
  <div class="user-menu" ref="rootRef">
    <button class="user-menu__btn" :aria-label="t('main.nav.user_menu')" @click="open = !open">
      👤 {{ username }}
    </button>
    <div v-if="open" class="user-menu__dropdown">
      <div class="user-menu__lang">
        <span class="user-menu__label">🌐</span>
        <button
          v-for="loc in ['ko', 'en', 'ja']"
          :key="loc"
          class="user-menu__lang-btn"
          :class="{ active: locale === loc }"
          @click="setLocale(loc)"
        >
          {{ loc.toUpperCase() }}
        </button>
      </div>
      <hr class="user-menu__divider" />
      <button v-if="isAdmin" class="user-menu__item" @click="goToSettings">
        {{ t('common.settings') }}
      </button>
      <button class="user-menu__item" @click="logout">
        {{ t('common.logout') }}
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { serverLogout } from '@shared/api'

interface AccessTokenPayload {
  username?: string
  roles?: string[]
  is_admin?: boolean
}

const { t, locale } = useI18n()
const open = ref(false)
const rootRef = ref<HTMLElement | null>(null)

function decodeAccessTokenPayload(token?: string): AccessTokenPayload | null {
  if (!token) return null

  try {
    return JSON.parse(atob(token.split('.')[1])) as AccessTokenPayload
  } catch {
    return null
  }
}

const tokenPayload = decodeAccessTokenPayload(window.__accessToken__)
const username = ref(tokenPayload?.username ?? (window.__accessToken__ ? 'user' : 'guest'))
const isAdmin = tokenPayload?.is_admin === true || tokenPayload?.roles?.includes('role_admin') === true

function setLocale(lang: string) {
  locale.value = lang
  localStorage.setItem('preferred_locale', lang)
  open.value = false
}

function goToSettings() {
  open.value = false
  window.location.href = '/settings'
}

async function logout() {
  open.value = false
  const userId = username.value
  try {
    await serverLogout()
  } finally {
    Object.keys(localStorage).forEach((key) => {
      if (key.startsWith(`flowgate.user.${userId}.`)) localStorage.removeItem(key)
    })
    localStorage.removeItem('flowgate.token')
    localStorage.removeItem('fg_refresh_token')
    sessionStorage.removeItem('fg_access_token')
    sessionStorage.removeItem('fg_refresh_token')
    delete window.__accessToken__
    window.location.href = '/'
  }
}

function handleOutsideClick(e: MouseEvent) {
  if (!rootRef.value?.contains(e.target as Node)) open.value = false
}

onMounted(() => document.addEventListener('mousedown', handleOutsideClick))
onBeforeUnmount(() => document.removeEventListener('mousedown', handleOutsideClick))
</script>

<style scoped>
.user-menu {
  position: relative;
}

.user-menu__btn {
  background: transparent;
  border: none;
  cursor: pointer;
  color: inherit;
  font-size: 0.875rem;
  padding: 4px 8px;
  border-radius: 4px;
}

.user-menu__btn:hover {
  background: var(--color-hover, rgba(255, 255, 255, 0.1));
}

.user-menu__dropdown {
  position: absolute;
  top: calc(100% + 4px);
  right: 0;
  min-width: 160px;
  background: var(--color-surface, #313244);
  border: 1px solid var(--color-border, #45475a);
  border-radius: 4px;
  z-index: 50;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
  padding: 4px 0;
}

.user-menu__lang {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 12px;
}

.user-menu__label {
  font-size: 0.875rem;
}

.user-menu__lang-btn {
  background: transparent;
  border: 1px solid var(--color-border, #45475a);
  border-radius: 3px;
  color: inherit;
  cursor: pointer;
  font-size: 0.75rem;
  padding: 2px 6px;
}

.user-menu__lang-btn.active {
  background: var(--color-accent, #cba6f7);
  color: #1e1e2e;
  border-color: transparent;
}

.user-menu__divider {
  border: none;
  border-top: 1px solid var(--color-border, #45475a);
  margin: 4px 0;
}

.user-menu__item {
  display: block;
  width: 100%;
  padding: 8px 12px;
  background: transparent;
  color: inherit;
  border: none;
  text-align: left;
  cursor: pointer;
  font-size: 0.875rem;
}

.user-menu__item:hover {
  background: var(--color-hover, rgba(255, 255, 255, 0.1));
}
</style>
