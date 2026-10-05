<template>
  <div class="settings-shell-view">
    <nav class="crumbs" :aria-label="t('gui.settings.breadcrumb_aria')">
      <template v-for="(crumb, index) in crumbs" :key="index">
        <span v-if="index > 0" class="crumb-sep" aria-hidden="true">›</span>
        <RouterLink v-if="crumb.to" :to="crumb.to" class="crumb-link">{{ crumb.label }}</RouterLink>
        <span v-else class="crumb-current">{{ crumb.label }}</span>
      </template>
    </nav>

    <div class="scope-switch" role="tablist" :aria-label="t('gui.settings.scope_aria')">
      <component
        :is="tab.to && !tab.disabled ? RouterLinkComp : 'button'"
        v-for="tab in tabs"
        :key="tab.key"
        :to="tab.to && !tab.disabled ? tab.to : undefined"
        :type="tab.to && !tab.disabled ? undefined : 'button'"
        class="scope-tab"
        :class="[tab.key, { on: tab.key === scope, disabled: tab.disabled }]"
        role="tab"
        :aria-selected="tab.key === scope"
        :aria-disabled="tab.disabled || undefined"
        :title="tab.disabled ? tab.reason : undefined"
        :data-scope="tab.key"
      >
        <SettingsIcon :name="tab.icon" size="lg" />
        <b>{{ tab.label }}</b>
      </component>
    </div>

    <div class="shell">
      <aside class="subnav">
        <slot name="nav" />
      </aside>
      <div class="body">
        <slot />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
// The frame every Environment Settings screen renders in (시안 11~14): breadcrumb, the four
// scope tabs (사용자 / 워크스페이스 / 시스템 / 프로젝트) and a sub-navigation + body split.
// 사용자 and 프로젝트 settings are not part of this step, so their tabs are shown but inert
// ("준비 중"); 시스템 is for system administrators only.
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useCurrentUser } from '@/core/auth/currentUser'
import SettingsIcon from '../SettingsIcon.vue'
import type { SettingsIconName } from '../icons'
import '../settings.css'

export type SettingsScope = 'user' | 'workspace' | 'system' | 'project'
export interface SettingsCrumb {
  label: string
  to?: string
}

defineProps<{ crumbs: SettingsCrumb[]; scope: SettingsScope }>()

const { t } = useI18n()
const { user } = useCurrentUser()
const RouterLinkComp = RouterLink

interface ScopeTab {
  key: SettingsScope
  icon: SettingsIconName
  label: string
  to?: string
  disabled: boolean
  reason?: string
}

const tabs = computed<ScopeTab[]>(() => [
  { key: 'user', icon: 'user', label: t('gui.settings.scope.user'), disabled: true, reason: t('gui.settings.not_ready') },
  { key: 'workspace', icon: 'grid', label: t('gui.settings.scope.workspace'), to: '/settings/workspaces', disabled: false },
  {
    key: 'system',
    icon: 'globe',
    label: t('gui.settings.scope.system'),
    to: '/settings/system/users',
    disabled: !user.value?.isAdmin,
    reason: t('gui.settings.admin_only'),
  },
  { key: 'project', icon: 'folder', label: t('gui.settings.scope.project'), disabled: true, reason: t('gui.settings.not_ready') },
])
</script>

<style scoped>
.settings-shell-view {
  height: 100%;
  min-height: 0;
  overflow-y: auto;
  padding: 18px 24px 28px;
}

.crumbs {
  height: 28px;
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 0 10px 2px;
  font-size: 11px;
  color: #6f87a0;
}

.crumb-link {
  color: #83a5c8;
}

.crumb-link:hover {
  color: #cbe0f6;
  text-decoration: underline;
  text-underline-offset: 3px;
}

.crumb-sep {
  color: #506984;
}

.crumb-current {
  color: #9fb3c9;
}

.scope-switch {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
  margin-bottom: 16px;
}

.scope-tab {
  height: 50px;
  border: 1px solid var(--fg2-line);
  border-radius: 9px;
  background: #0d1d30;
  color: #8fb1d3;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 9px;
  padding: 0 15px;
  font: inherit;
  cursor: pointer;
}

.scope-tab b {
  color: #cdd9e7;
  font-size: 14px;
}

.scope-tab.on {
  border-color: #367fd5;
  background: #112c4d;
  color: #fff;
  box-shadow: inset 0 0 20px rgba(44, 124, 255, 0.08);
}

.scope-tab.disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.shell {
  display: grid;
  grid-template-columns: 260px minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}

.subnav {
  border: 1px solid var(--fg2-line);
  border-radius: 12px;
  background: #0b1929;
  overflow: hidden;
  position: sticky;
  top: 0;
}

.body {
  min-width: 0;
}

@media (max-width: 1120px) {
  .shell {
    grid-template-columns: 1fr;
  }

  .subnav {
    position: static;
  }
}
</style>
