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
      <AppIcon name="list" />
    </button>

    <!-- Brand -->
    <RouterLink to="/" class="header-brand" @click="goToOverview">
      <div class="brand-icon">FG</div>
      <span class="brand-name">FlowGate</span>
      <span class="brand-ver">v0.1</span>
    </RouterLink>

    <!-- Project Selector -->
    <ProjectSelector @projectChanged="onProjectChanged" />

    <!-- Runtime AI provider: user/project-local selection, applied to every in-app run. -->
    <label class="ai-provider-selector" :title="providerTitle">
      <AppIcon name="robot" />
      <select
        :value="aiProviderStore.selectedProviderId"
        :aria-label="t('main.ai_provider.label')"
        :disabled="aiProviderStore.loading || aiProviderStore.providers.length === 0"
        @change="onProviderChanged"
      >
        <option v-if="aiProviderStore.loading" value="">{{ t('main.ai_provider.loading') }}</option>
        <option v-else-if="aiProviderStore.providers.length === 0" value="">{{ t('main.ai_provider.none') }}</option>
        <option v-for="provider in aiProviderStore.providers" :key="provider.id" :value="provider.id">
          {{ provider.name }}
        </option>
      </select>
    </label>

    <!-- 실행 미니플레이어 (0269 NR0011): the run monitor lives here, right beside the
         provider selector — "어느 프로바이더로 돌릴까" next to "지금 뭐가 돌고 있나".
         In the header it can never overlap a screen's bottom-fixed UI (chat composer,
         sticky action bar), which the floating version kept doing. -->
    <AiInvokeMiniplayer />

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
        <AppIcon name="gear" /><span>{{ t('nav.settings') }}</span>
      </button>

      <div class="hdr-div"></div>

      <!-- User display -->
      <div class="hdr-user">
        <div class="user-av">{{ usernameInitial }}</div>
        {{ username }}
      </div>

      <!-- Logout -->
      <button class="hdr-btn" @click="logout">
        <AppIcon name="sign-out" /><span>{{ t('common.logout') }}</span>
      </button>
    </nav>
  </header>
</template>

<script setup lang="ts">
import { computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { RouterLink } from 'vue-router'
import ProjectSelector from './ProjectSelector.vue'
import NotificationCenter from './NotificationCenter.vue'
import GitActionMenu from './GitActionMenu.vue'
import AiInvokeMiniplayer from './AiInvokeMiniplayer.vue'
import AppIcon from '@shared/AppIcon.vue'
import { useProjectStore } from '../stores/project'
import { useAiProviderStore } from '../stores/aiProvider'
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
const aiProviderStore = useAiProviderStore()
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

const providerTitle = computed(() => {
  if (aiProviderStore.error) return t('main.ai_provider.load_failed')
  return t('main.ai_provider.label')
})

watch(
  () => projectStore.currentProjectId,
  (projectId) => {
    if (projectId) void aiProviderStore.loadForProject(projectId)
    else aiProviderStore.clear()
  },
  { immediate: true },
)

function onProjectChanged(projectId: string) {
  projectStore.setCurrentProject(projectId)
}

function onProviderChanged(event: Event) {
  aiProviderStore.selectProvider((event.target as HTMLSelectElement).value)
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

