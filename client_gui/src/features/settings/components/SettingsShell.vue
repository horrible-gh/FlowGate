<template>
  <div class="settings-shell-view">
    <nav class="crumbs" :aria-label="t('gui.settings.breadcrumb_aria')">
      <template v-for="(crumb, index) in crumbs" :key="index">
        <span v-if="index > 0" class="crumb-sep" aria-hidden="true">›</span>
        <RouterLink v-if="crumb.to" :to="crumb.to" class="crumb-link">{{ crumb.label }}</RouterLink>
        <span v-else class="crumb-current">{{ crumb.label }}</span>
      </template>
    </nav>

    <div v-if="tabs.length" class="scope-switch" role="tablist" :aria-label="t('gui.settings.scope_aria')">
      <RouterLink
        v-for="tab in tabs"
        :key="tab.key"
        :to="tab.to"
        class="scope-tab"
        :class="[tab.key, { on: tab.key === scope }]"
        role="tab"
        :aria-selected="tab.key === scope"
        :data-scope="tab.key"
      >
        <SettingsIcon :name="tab.icon" size="lg" />
        <b>{{ tab.label }}</b>
      </RouterLink>
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
// The frame every Environment Settings screen renders in (시안 11~14): breadcrumb, the scope
// tabs and a sub-navigation + body split. flowgate.v02.0076 T#1: the tabs are the scopes of the
// shared settings menu (../settingsMenu) — 내 설정 / 프로젝트 / 시스템, each shown only when one of
// its screens is registered and allowed (프로젝트/시스템: system administrators) and opening the
// first of them. Workspace and "준비 중" tabs are gone. Loading the page's data stays with the
// page itself.
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import SettingsIcon from '../SettingsIcon.vue'
import type { SettingsIconName } from '../icons'
import { useSettingsMenu, type SettingsScopeKey } from '../settingsMenu'
import '../settings.css'

/** 'workspace' is only passed by the Workspace views, which no route opens any more (0076 T#1). */
export type SettingsScope = SettingsScopeKey | 'workspace'
export interface SettingsCrumb {
  label: string
  to?: string
}

defineProps<{ crumbs: SettingsCrumb[]; scope: SettingsScope }>()

const { t } = useI18n()
const menu = useSettingsMenu()

interface ScopeTab {
  key: SettingsScopeKey
  icon: SettingsIconName
  label: string
  to: string
}

const SCOPES: { key: SettingsScopeKey; icon: SettingsIconName; labelKey: string }[] = [
  { key: 'me', icon: 'user', labelKey: 'gui.settings.scope.me' },
  { key: 'project', icon: 'folder', labelKey: 'gui.settings.scope.project' },
  { key: 'system', icon: 'globe', labelKey: 'gui.settings.scope.system' },
]

const tabs = computed<ScopeTab[]>(() =>
  SCOPES.flatMap((scope) => {
    const first = menu.value.find((page) => page.scope === scope.key)
    return first ? [{ key: scope.key, icon: scope.icon, label: t(scope.labelKey), to: first.path }] : []
  }),
)
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
  grid-auto-flow: column;
  grid-auto-columns: minmax(0, 1fr);
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
