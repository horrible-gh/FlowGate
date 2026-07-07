<template>
  <header class="app-header">
    <button
      v-if="showSidebarToggle"
      class="sidebar-toggle"
      type="button"
      :aria-label="t(sidebarOpen ? 'main.nav.close_explorer' : 'main.nav.open_explorer')"
      :aria-expanded="sidebarOpen"
      @click="emit('toggle-sidebar')"
    >
      <i class="fa-solid fa-bars"></i>
    </button>

    <!-- Brand -->
    <RouterLink to="/" class="header-brand" @click="goToOverview">
      <div class="brand-icon">FG</div>
      <span class="brand-name">FlowGate</span>
      <span class="brand-ver">v0.1</span>
    </RouterLink>

    <!-- Project Selector -->
    <ProjectSelector @projectChanged="onProjectChanged" />

    <!-- Spacer -->
    <div class="header-spacer"></div>

    <!-- Right Nav -->
    <nav class="header-nav">
      <!-- Language Switcher -->
      <div class="lang-sw">
        <button
          v-for="lang in ['ko', 'en', 'ja']"
          :key="lang"
          class="lang-btn"
          :class="{ active: locale === lang }"
          @click="setLocale(lang)"
        >{{ lang.toUpperCase() }}</button>
      </div>

      <div class="hdr-div"></div>

      <!-- ⑂ Git action menu (flowgate.default.0162 §3.3 "안전망") — self-hides unless
           the current project is git-integrated; carries the finalize-backlog badge. -->
      <GitActionMenu />

      <div class="hdr-div"></div>

      <!-- 🔔 Notification center (R0001 group 0045 / NR0003 option A) -->
      <NotificationCenter />

      <div class="hdr-div"></div>

      <!-- Settings (admin only) -->
      <button v-if="isAdmin" class="hdr-btn" @click="goToSettings">
        <i class="fa-solid fa-gear"></i><span>{{ t('nav.settings') }}</span>
      </button>

      <div class="hdr-div"></div>

      <!-- User display -->
      <div class="hdr-user">
        <div class="user-av">{{ usernameInitial }}</div>
        {{ username }}
      </div>

      <!-- Logout -->
      <button class="hdr-btn" @click="logout">
        <i class="fa-solid fa-right-from-bracket"></i><span>{{ t('common.logout') }}</span>
      </button>
    </nav>
  </header>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { RouterLink } from 'vue-router'
import ProjectSelector from './ProjectSelector.vue'
import NotificationCenter from './NotificationCenter.vue'
import GitActionMenu from './GitActionMenu.vue'
import { useProjectStore } from '../stores/project'
import { useTabsStore } from '../stores/tabs'
import { serverLogout } from '@shared/api'

withDefaults(defineProps<{
  sidebarOpen?: boolean
  showSidebarToggle?: boolean
}>(), {
  sidebarOpen: false,
  showSidebarToggle: false,
})
const emit = defineEmits<{ 'toggle-sidebar': [] }>()
const { t, locale } = useI18n()
const projectStore = useProjectStore()
const tabsStore = useTabsStore()

interface AccessTokenPayload {
  username?: string
  roles?: string[]
  is_admin?: boolean
  sub?: string
  user_id?: string
}

function decodeToken(token?: string): AccessTokenPayload | null {
  if (!token) return null
  try {
    return JSON.parse(atob(token.split('.')[1])) as AccessTokenPayload
  } catch {
    return null
  }
}

const tokenPayload = decodeToken(window.__accessToken__)
const isAdmin =
  tokenPayload?.is_admin === true ||
  tokenPayload?.roles?.includes('role_admin') === true

const username = tokenPayload?.username ?? (window.__accessToken__ ? 'user' : 'guest')
const usernameInitial = username.charAt(0).toUpperCase()

function onProjectChanged(projectId: string) {
  projectStore.setCurrentProject(projectId)
}

// Clicking the brand/logo should land on the Overview tab. The router
// link to "/" is a no-op once you're already on the dashboard route, so on its
// own it produced no visible change when a document tab was active. Deselecting
// the active tab reveals the overview panel — the overview tab's own behaviour.
function goToOverview() {
  tabsStore.activeTabId = null
}

function setLocale(lang: string) {
  locale.value = lang
  localStorage.setItem('preferred_locale', lang)
}

function goToSettings() {
  window.location.href = '/settings'
}

async function logout() {
  const userId = username
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
</script>

