<template>
  <nav class="subnav" :aria-label="t('gui.settings.title')">
    <RouterLink to="/settings" class="nav-home" :class="{ active: !current }" :aria-current="current ? undefined : 'page'">
      <SettingsIcon name="home" size="sm" />{{ t('gui.settings.nav_home') }}
    </RouterLink>
    <div v-if="scopes.length" class="nav-sep" />
    <div v-for="scope in scopes" :key="scope.key" class="scope" :class="{ open: scope.key === current }">
      <RouterLink :to="scope.to" class="scope-btn" :data-scope="scope.key">
        <span class="si" :class="`t-${scope.key}`"><SettingsIcon :name="scope.icon" size="sm" /></span>
        <span class="txt">
          <strong>{{ scope.label }}</strong>
          <small>{{ scope.sub }}</small>
        </span>
      </RouterLink>
      <div v-if="scope.key === current" class="scope-pages"><slot /></div>
    </div>
    <!-- A scope no menu offers (the retired Workspace views) still shows its own pages. -->
    <div v-if="current && !scopes.some((scope) => scope.key === current)" class="scope-pages"><slot /></div>
  </nav>
</template>

<script setup lang="ts">
// The one navigation of 환경 설정 (mockup ⑩, flowgate.v02.0077): 환경 설정 개요, then each settings
// scope the shared menu offers (../settingsMenu — 내 설정 for everyone, 프로젝트 / 시스템 for system
// administrators) as a heading with its reach, and under the scope being shown the page's own
// sub-navigation (the `default` slot: SettingsSubnav, the project picker …). A scope heading opens
// the first page of that scope. Nothing here loads data.
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import SettingsIcon from '../SettingsIcon.vue'
import type { SettingsIconName } from '../icons'
import { useSettingsMenu, type SettingsScopeKey } from '../settingsMenu'

defineProps<{
  /** The scope of the screen shown; null on the overview. */
  current: SettingsScopeKey | 'workspace' | null
}>()

const { t } = useI18n()
const menu = useSettingsMenu()

const SCOPES: { key: SettingsScopeKey; icon: SettingsIconName }[] = [
  { key: 'me', icon: 'user' },
  { key: 'project', icon: 'folder' },
  { key: 'system', icon: 'server' },
]

const scopes = computed(() =>
  SCOPES.flatMap((scope) => {
    const first = menu.value.find((page) => page.scope === scope.key)
    return first
      ? [{ ...scope, label: t(`gui.settings.scope.${scope.key}`), sub: t(`gui.settings.scope_sub.${scope.key}`), to: first.path }]
      : []
  }),
)
</script>

<style scoped>
.subnav {
  border: 1px solid #263d57;
  border-radius: 14px;
  background: #0a1a2c;
  padding: 10px;
  position: sticky;
  top: 0;
}

.nav-home {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 11px 12px;
  border-radius: 9px;
  border: 1px solid transparent;
  color: #a9bdd2;
  font-weight: 700;
  font-size: 13px;
}

.nav-home:hover {
  background: #132b44;
  color: #e7f2ff;
}

.nav-home.active {
  background: #173a61;
  border-color: #265e9d;
  color: #e6f4ff;
}

.nav-sep {
  height: 1px;
  background: #1f3550;
  margin: 8px 4px;
}

.scope {
  margin-top: 2px;
}

.scope-btn {
  display: flex;
  align-items: center;
  gap: 11px;
  width: 100%;
  padding: 10px 11px;
  border-radius: 10px;
  border: 1px solid transparent;
  color: #a9bdd2;
  text-align: left;
}

.scope-btn:hover {
  background: #112840;
  color: #e7f2ff;
}

.scope.open .scope-btn {
  background: #0f2440;
}

.scope-btn .si {
  width: 32px;
  height: 32px;
  border-radius: 9px;
  display: grid;
  place-items: center;
  border: 1px solid;
  flex: none;
}

.scope-btn .txt {
  min-width: 0;
}

.scope-btn strong {
  display: block;
  font-size: 13.5px;
  color: #dbe9fa;
}

.scope-btn small {
  display: block;
  font-size: 11px;
  color: #70869f;
  margin-top: 2px;
}

.t-me { color: #54a2ff; border-color: #296fb0; background: #112f50; }
.t-project { color: #ad85ff; border-color: #6650a9; background: #282047; }
.t-system { color: #9cbbdd; border-color: #526e8a; background: #1a2b41; }

.scope-pages {
  padding: 4px 0 8px 14px;
  margin-left: 16px;
  border-left: 1px solid #22405f;
}

@media (max-width: 780px) {
  .subnav {
    position: static;
  }

  .scope-pages {
    margin-left: 0;
    padding-left: 0;
    border-left: 0;
  }
}
</style>
