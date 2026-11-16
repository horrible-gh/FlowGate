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
        <NotificationAiDetailHost :project-id="notificationProjectId" />
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
        <LineIcon name="sliders" class="designer-header-icon" /><span class="designer-header-label">{{ t('gui.designer.top.header.settings') }}</span>
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
        <LineIcon name="chevron-down" class="designer-header-chevron" />
      </button>
      <!-- 0016-CH (시안 basodrr4 B안): who is signed in on a card at the top, then the three items with tinted line
           icons; 로그아웃 sits apart below a separator in red. Items and destinations are unchanged. -->
      <div v-if="accountOpen" id="designer-account-menu" ref="accountMenu" role="menu" class="designer-account-menu" @keydown="menuKey">
        <div class="designer-account-heading">
          <span class="designer-account-avatar" aria-hidden="true">{{ initial }}</span>
          <span class="designer-account-who"><b>{{ user.username }}</b><small v-if="user.email">{{ user.email }}</small></span>
        </div>
        <template v-for="item in accountItems" :key="item.key">
          <div v-if="item.key === 'logout'" class="designer-account-sep" role="separator" />
          <button role="menuitem" type="button" :class="{ 'is-out': item.key === 'logout' }" :data-test="'account-' + item.key" :title="t('gui.accountMenu.' + item.key)" @click="accountAction(item.key)">
            <LineIcon :name="item.icon" />{{ t('gui.accountMenu.' + item.key) }}
          </button>
        </template>
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
import NotificationAiDetailHost from './notifications/NotificationAiDetailHost.vue'
import type { ExplorerProject } from '../projectExplorer/projectExplorerTree'
import { useI18n } from 'vue-i18n'
import { useCurrentUser } from '@/core/auth/currentUser'
import GlobalSearch from './GlobalSearch.vue'
import LineIcon, { type LineIconName } from '@/components/ui/LineIcon.vue'
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
  { key: 'profile', icon: 'user' },
  { key: 'password', icon: 'lock' },
  { key: 'logout', icon: 'logout' },
] as const satisfies readonly { key: string; icon: LineIconName }[]
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
/* 0016-CH (시안 basodrr4 B안): the raised card menu, on theme.css's type scale. */
.designer-account-menu { position: absolute; right: 0; top: calc(100% + 10px); z-index: 100; width: 280px; max-width: calc(100vw - 24px); padding: 8px; border: 1px solid var(--fg2-line-strong); border-radius: var(--fg2-pop-radius); background: var(--fg2-pop-surface); color: var(--fg2-text); box-shadow: var(--fg2-pop-shadow); font-size: var(--fg2-fs-body); }
.designer-account-heading { display: flex; align-items: center; gap: 12px; min-width: 0; margin-bottom: 8px; padding: 12px; border: 1px solid var(--fg2-line); border-radius: 12px; background: var(--fg2-inset-bg); }
.designer-account-avatar { display: grid; flex: none; place-items: center; width: 40px; height: 40px; border-radius: 50%; background: #2563eb; color: #fff; font-size: 14px; font-weight: 700; }
.designer-account-who { display: flex; flex-direction: column; gap: 2px; min-width: 0; overflow-wrap: anywhere; }
.designer-account-who b { font-size: 14px; font-weight: 700; }
.designer-account-who small { color: var(--fg2-text-3); font-size: var(--fg2-fs-sub); }
.designer-account-menu button { display: flex; align-items: center; gap: 12px; width: 100%; height: 40px; padding: 0 10px; border: 0; border-radius: 9px; background: transparent; color: var(--fg2-text-2); font: inherit; font-size: var(--fg2-fs-body); font-weight: 550; text-align: left; cursor: pointer; }
.designer-account-menu button:hover, .designer-account-menu button:focus-visible { background: rgba(124, 192, 255, 0.08); color: var(--fg2-text); }
.designer-account-menu button:focus-visible { outline: 2px solid var(--fg2-focus); outline-offset: -2px; }
.designer-account-menu button :deep(svg) { width: 28px; height: 28px; padding: 6px; border-radius: 8px; background: rgba(124, 192, 255, 0.08); color: #9cc8f5; }
.designer-account-menu button.is-out { color: #ff9aa8; }
.designer-account-menu button.is-out :deep(svg) { background: rgba(255, 100, 120, 0.1); color: #ff9aa8; }
.designer-account-sep { height: 1px; margin: 6px 4px; background: var(--fg2-line); }
.designer-header-btn .designer-header-icon { width: 15px; height: 15px; }
.designer-header-user .designer-header-chevron { width: 14px; height: 14px; color: var(--fg2-text-3); }

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
