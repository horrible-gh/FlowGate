<template>
  <!-- flowgate.v02.0038 — Designer Header (0001-R §2): brand, notification entry on the
       left, the search assist in the middle, language / settings / user on the right.
       The side groups never shrink below their content; the search takes what is left
       (up to 680px), and narrow windows switch the side groups to a compact form.
       Notifications are connected through the Shell; settings and the user button emit commands. The language switch is the existing shared
       contract (vue-i18n locale + `preferred_locale`, as v0.1's switches do). -->
  <div class="designer-header" data-test="designer-header">
    <div class="designer-header-left">
      <div class="designer-header-brand" data-test="designer-header-brand">
        <span class="designer-header-logo" aria-hidden="true">FG</span>
        <span class="designer-header-product">FlowGate <span>Designer</span></span>
      </div>
      <div class="designer-header-actions">
        <button
          type="button"
          class="designer-header-btn is-icon"
          :aria-label="t('gui.designer.top.header.notifications')"
          :title="t('gui.designer.top.header.notifications')"
          ref="notificationAnchor"
          :aria-expanded="notificationsOpen"
          aria-haspopup="dialog"
          :aria-controls="notificationsOpen ? 'designer-notifications' : undefined"
          data-test="designer-header-notifications"
          @click="emit('command', 'header.notifications')"
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9" />
            <path d="M10 21h4" />
          </svg>
          <span v-if="badge" data-test="notification-bell-count">{{ badge }}</span>
        </button>
        <DesignerNotifications v-if="notificationsOpen" :project-id="notificationProjectId" :project="notificationProject" :anchor="notificationAnchor" @close="emit('close-notifications')" />
      </div>
    </div>

    <div class="designer-header-search">
      <GlobalSearch @search="emit('search', $event)" />
    </div>

    <div class="designer-header-right">
      <div class="designer-header-locale" role="group" :aria-label="t('gui.designer.top.header.locale')" data-test="designer-header-locale">
        <button
          v-for="code in DESIGNER_LOCALES"
          :key="code"
          type="button"
          :class="{ 'is-on': locale === code }"
          :aria-pressed="locale === code ? 'true' : 'false'"
          :data-locale="code"
          @click="setLocale(code)"
        >
          {{ code.toUpperCase() }}
        </button>
      </div>
      <button
        type="button"
        class="designer-header-btn"
        data-test="designer-header-settings"
        :title="t('gui.accountMenu.settings')"
        @click="openSettings()"
      >
        <span aria-hidden="true">⚙</span><span class="designer-header-label">{{ t('gui.designer.top.header.settings') }}</span>
      </button>
      <div v-if="user" ref="accountRoot" class="designer-account">
      <button
        ref="accountTrigger"
        type="button"
        class="designer-header-user"
        :aria-label="t('gui.designer.top.header.user', { name: user.username })"
        data-test="designer-header-user"
        aria-haspopup="menu"
        :aria-expanded="accountOpen"
        :aria-controls="accountOpen ? 'designer-account-menu' : undefined"
        @keydown.down.prevent="showAccount()"
        @click="toggleAccount()"
      >
        <span class="designer-header-avatar" aria-hidden="true">{{ initial }}</span>
        <b class="designer-header-label">{{ user.username }}</b>
        <span aria-hidden="true">⌄</span>
      </button>
      <div v-if="accountOpen" id="designer-account-menu" ref="accountMenu" role="menu" class="designer-account-menu" @keydown="menuKey">
        <div class="designer-account-heading">{{ user.username }}<small v-if="user.email">{{ user.email }}</small></div>
        <button v-for="item in accountItems" :key="item.key" role="menuitem" type="button" :data-test="'account-' + item.key" :title="t('gui.accountMenu.' + item.key)" @click="accountAction(item.key)">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path :d="item.path" /></svg>
          {{ t('gui.accountMenu.' + item.key) }}
        </button>
      </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, inject, nextTick, onMounted, onBeforeUnmount, watch } from 'vue'
import { routerKey } from 'vue-router'
import { logout } from '@/core/auth/logout'
import { useNotificationBadge } from './notifications/useNotificationBadge'
import DesignerNotifications from './notifications/DesignerNotifications.vue'
import type { ExplorerProject } from '../projectExplorer/projectExplorerTree'
import { useI18n } from 'vue-i18n'
import { useCurrentUser } from '@/core/auth/currentUser'
import GlobalSearch from './GlobalSearch.vue'
import { DESIGNER_LOCALES, type DesignerLocale, type DesignerSearchRequest, type DesignerTopCommand } from './designerTop'

const props = withDefaults(defineProps<{ notificationsOpen?: boolean; notificationProjectId?: string | null; notificationProject?: ExplorerProject | null }>(), { notificationsOpen: false, notificationProjectId: null, notificationProject: null })
const badge = useNotificationBadge(() => props.notificationProjectId, () => props.notificationProject)
const notificationAnchor = ref<HTMLElement | null>(null)

const emit = defineEmits<{
  'close-notifications': []
  command: [command: DesignerTopCommand]
  search: [request: DesignerSearchRequest]
}>()

const { t, locale } = useI18n()
const { user } = useCurrentUser()

// Keep routing at the header boundary; the Designer Shell remains router independent.
const router = inject(routerKey, null)
const accountOpen = ref(false)
const accountRoot = ref<HTMLElement | null>(null)
const accountTrigger = ref<HTMLButtonElement | null>(null)
const accountMenu = ref<HTMLElement | null>(null)
const accountItems = [
  { key: 'profile', path: 'M20 21v-2a7 7 0 0 0-14 0v2 M12 3a4 4 0 1 0 0 8a4 4 0 0 0 0-8' },
  { key: 'password', path: 'M6 11h12v10H6z M8 11V7a4 4 0 0 1 8 0v4' },
  { key: 'logout', path: 'M10 3H4v18h6 M10 12h11 M17 8l4 4-4 4' },
] as const
function closeAccount(restoreFocus = false) {
  accountOpen.value = false
  if (restoreFocus) accountTrigger.value?.focus()
}
async function showAccount() {
  accountOpen.value = true
  await nextTick()
  accountMenu.value?.querySelector<HTMLButtonElement>('button')?.focus()
}
function toggleAccount() {
  emit('command', 'header.user')
  if (accountOpen.value) closeAccount(true)
  else void showAccount()
}
function openSettings() {
  closeAccount()
  emit('command', 'header.settings')
  void router?.push({ name: 'settings-me-account' })
}
async function accountAction(key: typeof accountItems[number]['key']) {
  closeAccount(true)
  if (key === 'logout') {
    if (window.confirm(t('gui.accountMenu.confirmLogout'))) await logout()
  } else {
    await router?.push({ name: key === 'profile' ? 'settings-me-account' : 'settings-me-security' })
  }
}
function menuKey(event: KeyboardEvent) {
  if (event.key === 'Tab') { closeAccount(); return }
  const buttons = Array.from(accountMenu.value?.querySelectorAll<HTMLButtonElement>('button') ?? [])
  const index = buttons.indexOf(document.activeElement as HTMLButtonElement)
  let target: number
  if (event.key === 'ArrowDown') target = (index + 1) % buttons.length
  else if (event.key === 'ArrowUp') target = (index - 1 + buttons.length) % buttons.length
  else if (event.key === 'Home') target = 0
  else if (event.key === 'End') target = buttons.length - 1
  else return
  event.preventDefault()
  buttons[target]?.focus()
}
function outside(event: PointerEvent) {
  if (event.target instanceof Node && !accountRoot.value?.contains(event.target)) closeAccount()
}
function escape(event: KeyboardEvent) {
  if (accountOpen.value && event.key === 'Escape') { event.preventDefault(); closeAccount(true) }
}
function focusOutside(event: FocusEvent) {
  if (event.target instanceof Node && !accountRoot.value?.contains(event.target)) closeAccount()
}
onMounted(() => {
  document.addEventListener('pointerdown', outside)
  document.addEventListener('keydown', escape)
  document.addEventListener('focusin', focusOutside)
})
onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', outside)
  document.removeEventListener('keydown', escape)
  document.removeEventListener('focusin', focusOutside)
})
watch(() => user.value?.username, () => closeAccount())
watch(() => props.notificationsOpen, (open) => { if (open) closeAccount() })

const initial = computed(() => user.value?.username.charAt(0).toUpperCase() ?? '')

function setLocale(code: DesignerLocale) {
  locale.value = code
  localStorage.setItem('preferred_locale', code)
}
</script>

<style scoped>
.designer-account { position: relative; }
.designer-account-menu { position: absolute; right: 0; top: calc(100% + 10px); z-index: 100; width: 230px; max-width: calc(100vw - 24px); padding: 6px; border: 1px solid var(--fg2-line-strong); border-radius: 8px; background: #10213a; color: var(--fg2-text); box-shadow: 0 8px 24px #0008; }
.designer-account-heading { padding: 8px; overflow-wrap: anywhere; border-bottom: 1px solid var(--fg2-line); }
.designer-account-heading small { display: block; color: #899db5; }
.designer-account-menu button { display: flex; align-items: center; gap: 8px; width: 100%; padding: 9px; border: 0; border-radius: 4px; background: transparent; color: inherit; font: inherit; text-align: left; cursor: pointer; }
.designer-account-menu button:hover, .designer-account-menu button:focus-visible { background: #203c60; outline: 2px solid var(--fg2-focus); }
.designer-account-menu svg { width: 18px; height: 18px; fill: none; stroke: currentColor; stroke-width: 1.7; }

.designer-header {
  display: grid;
  /* Side columns are at least as wide as their content, so the brand / notification
     group and the language / settings / user group never run under the search. The search
     keeps its 680px cap on wide screens and gives way first on medium ones. */
  grid-template-columns: minmax(max-content, 1fr) minmax(220px, 680px) minmax(max-content, 1fr);
  gap: 14px;
  align-items: center;
  height: 100%;
  min-width: 0;
  padding: 0 16px;
  box-sizing: border-box;
  border-bottom: 1px solid var(--fg2-line);
  background: #0a1729;
}

.designer-header-left,
.designer-header-right,
.designer-header-search {
  display: flex;
  align-items: center;
  min-width: 0;
}

.designer-header-right {
  justify-content: flex-end;
  gap: 9px;
}

.designer-header-search {
  justify-content: center;
}

.designer-header-brand {
  display: flex;
  flex: none;
  align-items: center;
  gap: 10px;
}

.designer-header-logo {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  border-radius: 7px;
  background: linear-gradient(145deg, #347cf5, #225cd0);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
  color: #fff;
  font-size: 13px;
  font-weight: 900;
}

.designer-header-product {
  color: var(--fg2-text);
  font-size: 17px;
  font-weight: 760;
  white-space: nowrap;
}

.designer-header-product span {
  margin-left: 5px;
  color: #65a4ff;
  font-weight: 600;
}

.designer-header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-left: 34px;
  padding-left: 20px;
  border-left: 1px solid var(--fg2-line);
}

.designer-header-btn {
  display: inline-flex;
  flex: none;
  align-items: center;
  gap: 7px;
  height: 34px;
  padding: 0 11px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius-sm);
  background: #10213a;
  color: #d6e3f1;
  font: inherit;
  font-size: 12px;
  white-space: nowrap;
  cursor: pointer;
}

.designer-header-btn:hover {
  background: #142946;
}

.designer-header-btn.is-icon {
  justify-content: center;
  min-width: 36px;
  padding: 0 9px;
}

.designer-header-btn.is-icon svg {
  display: block;
  width: 17px;
  height: 17px;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.8;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.designer-header-locale {
  display: flex;
  flex: none;
  height: 34px;
  overflow: hidden;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius-sm);
}

.designer-header-locale button {
  width: 38px;
  border: 0;
  border-right: 1px solid var(--fg2-line);
  background: #0c1b2f;
  color: #899db5;
  font: inherit;
  font-size: 11px;
  cursor: pointer;
}

.designer-header-locale button:last-child {
  border-right: 0;
}

.designer-header-locale button.is-on {
  background: #142d4c;
  color: #fff;
  font-weight: 700;
}

.designer-header-user {
  display: flex;
  flex: none;
  align-items: center;
  gap: 7px;
  min-width: 0;
  padding: 0 4px;
  border: 0;
  background: transparent;
  color: var(--fg2-text);
  font: inherit;
  cursor: pointer;
}

.designer-header-user b {
  max-width: 120px;
  overflow: hidden;
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.designer-header-avatar {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: #2563eb;
  font-size: 12px;
  font-weight: 800;
}

/* Compact form for medium-narrow windows: tighter separators, and the settings / user
   labels are hidden visually only (they stay the buttons' accessible names). */
@media (max-width: 1199px) {
  .designer-header {
    gap: 10px;
    padding: 0 12px;
  }

  .designer-header-actions {
    margin-left: 14px;
    padding-left: 12px;
  }

  .designer-header-right {
    gap: 6px;
  }

  .designer-header-label {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
    white-space: nowrap;
  }
}

.designer-header-btn:focus-visible,
.designer-header-locale button:focus-visible,
.designer-header-user:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 2px;
}
</style>
