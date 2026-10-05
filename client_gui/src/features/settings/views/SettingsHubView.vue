<template>
  <div class="settings-hub">
    <header class="pagehead">
      <span class="pageicon"><SettingsIcon name="gear" size="xl" /></span>
      <h1>{{ t('gui.settings.title') }}</h1>
    </header>

    <div class="scope-grid">
      <article v-for="card in cards" :key="card.key" class="scope-card" :class="[card.key, { inert: !card.to }]" :data-scope-card="card.key">
        <div class="scope-top">
          <div class="scope-icon"><SettingsIcon :name="card.icon" size="xl" /></div>
          <div class="scope-title">
            <h2>{{ card.title }}</h2>
            <span class="scope-who"><SettingsIcon :name="card.whoIcon" size="sm" />{{ card.who }}</span>
          </div>
        </div>
        <div class="tagrow">
          <span v-for="tag in card.tags" :key="tag" class="tag">{{ tag }}</span>
        </div>
        <RouterLink v-if="card.to" :to="card.to" class="openbtn">{{ t('gui.settings.hub.open') }} →</RouterLink>
        <span v-else class="openbtn disabled" :title="card.reason">{{ card.reason }}</span>
      </article>
    </div>
  </div>
</template>

<script setup lang="ts">
// 환경 설정 hub (시안 11 #hub): one card per settings scope. 워크스페이스 and 시스템 open the
// screens of this step; 사용자 and 프로젝트 settings are later steps and stay inert.
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useCurrentUser } from '@/core/auth/currentUser'
import SettingsIcon from '../SettingsIcon.vue'
import type { SettingsIconName } from '../icons'
import '../settings.css'

const { t } = useI18n()
const { user } = useCurrentUser()

interface ScopeCard {
  key: 'user' | 'workspace' | 'system' | 'project'
  icon: SettingsIconName
  whoIcon: SettingsIconName
  title: string
  who: string
  tags: string[]
  to?: string
  reason?: string
}

const cards = computed<ScopeCard[]>(() => [
  {
    key: 'user', icon: 'user', whoIcon: 'user', title: t('gui.settings.scope.user'), who: t('gui.settings.hub.who_me'),
    tags: [t('gui.settings.hub.tags.language'), t('gui.settings.hub.tags.display'), t('gui.settings.hub.tags.session')],
    reason: t('gui.settings.not_ready'),
  },
  {
    key: 'workspace', icon: 'grid', whoIcon: 'users', title: t('gui.settings.scope.workspace'), who: t('gui.settings.hub.who_team'),
    tags: [t('gui.settings.hub.tags.members'), t('gui.settings.hub.tags.access'), t('gui.settings.hub.tags.projects'), t('gui.settings.hub.tags.policy')],
    to: '/settings/workspaces',
  },
  {
    key: 'system', icon: 'globe', whoIcon: 'globe', title: t('gui.settings.scope.system'), who: t('gui.settings.hub.who_all'),
    tags: [t('gui.settings.hub.tags.users'), t('gui.settings.hub.tags.storage'), t('gui.settings.hub.tags.environment'), t('gui.settings.hub.tags.logs'), t('gui.settings.hub.tags.security')],
    to: user.value?.isAdmin ? '/settings/system/users' : undefined,
    reason: t('gui.settings.admin_only'),
  },
  {
    key: 'project', icon: 'folder', whoIcon: 'folder', title: t('gui.settings.scope.project'), who: t('gui.settings.hub.who_project'),
    tags: [t('gui.settings.hub.tags.paths'), t('gui.settings.hub.tags.source')],
    reason: t('gui.settings.not_ready'),
  },
])
</script>

<style scoped>
.settings-hub {
  height: 100%;
  overflow-y: auto;
  padding: 18px 24px 28px;
}

.pagehead {
  display: flex;
  align-items: center;
  gap: 14px;
}

.pagehead h1 {
  margin: 0;
  font-size: 26px;
}

.pageicon {
  width: 52px;
  height: 52px;
  border-radius: 14px;
  display: grid;
  place-items: center;
  background: #11284a;
  color: #8fb1d3;
}

.scope-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 18px;
  margin-top: 18px;
}

.scope-card {
  --accent: #3f9fff;
  position: relative;
  min-height: 240px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: 16px;
  background: linear-gradient(160deg, rgba(16, 34, 58, 0.94), rgba(10, 24, 41, 0.96));
  padding: 28px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  transition: 0.18s ease;
}

.scope-card:not(.inert):hover {
  transform: translateY(-2px);
  border-color: var(--accent);
  box-shadow: 0 14px 34px #0006;
}

.scope-card.workspace { --accent: #35c7a4; }
.scope-card.system { --accent: #8aa7cc; }
.scope-card.project { --accent: #a16eff; }

.scope-top {
  display: flex;
  align-items: center;
  gap: 15px;
}

.scope-icon {
  width: 58px;
  height: 58px;
  border: 1px solid color-mix(in srgb, var(--accent) 55%, transparent);
  border-radius: 14px;
  background: color-mix(in srgb, var(--accent) 10%, #0c1a2b);
  display: grid;
  place-items: center;
  color: var(--accent);
}

.scope-title h2 {
  margin: 0 0 5px;
  font-size: 22px;
}

.scope-who {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--fg2-muted);
  font-size: 13px;
}

.tagrow {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin: 22px 0;
}

.tag {
  border: 1px solid var(--fg2-line-strong);
  border-radius: 999px;
  padding: 4px 10px;
  font-size: 12px;
  color: #a9bbce;
}

.openbtn {
  margin-top: auto;
  align-self: flex-start;
  height: 40px;
  border: 1px solid color-mix(in srgb, var(--accent) 55%, transparent);
  border-radius: 9px;
  padding: 0 16px;
  display: inline-flex;
  align-items: center;
  color: #e4eef9;
  font-weight: 700;
  font-size: 13px;
}

.openbtn.disabled {
  border-color: var(--fg2-line);
  color: var(--fg2-muted);
  cursor: not-allowed;
}

@media (max-width: 1120px) {
  .scope-grid {
    grid-template-columns: 1fr;
  }
}
</style>
