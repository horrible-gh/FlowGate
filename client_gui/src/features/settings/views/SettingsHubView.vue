<template>
  <div class="settings-hub">
    <header class="pagehead">
      <span class="pageicon"><SettingsIcon name="gear" size="xl" /></span>
      <h1>{{ t('gui.settings.title') }}</h1>
    </header>

    <Alert
      v-if="notice === 'admin_only'"
      class="hub-notice"
      tone="warning"
      dismissible
      :dismiss-label="t('gui.settings.notice_close')"
      data-testid="settings-access-notice"
      @dismiss="clearNotice"
    >
      {{ t('gui.settings.access_denied') }}
    </Alert>

    <div v-if="cards.length" class="scope-grid">
      <article v-for="card in cards" :key="card.key" class="scope-card" :class="card.key" :data-scope-card="card.key">
        <div class="scope-top">
          <div class="scope-icon"><SettingsIcon :name="card.icon" size="xl" /></div>
          <div class="scope-title">
            <h2>{{ card.title }}</h2>
            <span class="scope-who"><SettingsIcon :name="card.icon" size="sm" />{{ card.who }}</span>
          </div>
        </div>
        <div class="tagrow">
          <span v-for="tag in card.tags" :key="tag" class="tag">{{ tag }}</span>
        </div>
        <RouterLink :to="card.to" class="openbtn">{{ t('gui.settings.hub.open') }} →</RouterLink>
      </article>
    </div>
    <EmptyState v-else :title="t('gui.settings.hub.empty')" data-testid="settings-hub-empty" />
  </div>
</template>

<script setup lang="ts">
// 환경 설정 개요 (시안 11 #hub, flowgate.v02.0076 T#1 D §1.1): one card per settings scope —
// 내 설정 / 프로젝트 / 시스템 — built from the shared settings menu (../settingsMenu). A card
// shows only when at least one of its screens is registered and allowed: 프로젝트 and 시스템 for
// a system administrator once /auth/me has answered. Its tags name exactly those screens and
// "열기" opens the first one; there are no "준비 중" or disabled cards, and Workspace is gone.
// The overview loads no page data itself. A refused settings URL lands here with a notice.
import { computed, onBeforeUnmount } from 'vue'
import { RouterLink } from 'vue-router'
import { useI18n } from 'vue-i18n'
import Alert from '@/components/ui/Alert.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import SettingsIcon from '../SettingsIcon.vue'
import { useSettingsAccessNotice } from '../access'
import type { SettingsIconName } from '../icons'
import { useSettingsMenu, type SettingsScopeKey } from '../settingsMenu'
import '../settings.css'

const { t } = useI18n()
const menu = useSettingsMenu()
const { notice, clearNotice } = useSettingsAccessNotice()

// The notice belongs to the move that was refused; leaving the overview drops it.
onBeforeUnmount(clearNotice)

interface ScopeCard {
  key: SettingsScopeKey
  icon: SettingsIconName
  title: string
  who: string
  tags: string[]
  to: string
}

const SCOPES: { key: SettingsScopeKey; icon: SettingsIconName; titleKey: string; whoKey: string }[] = [
  { key: 'me', icon: 'user', titleKey: 'gui.settings.scope.me', whoKey: 'gui.settings.hub.who_me' },
  { key: 'project', icon: 'folder', titleKey: 'gui.settings.scope.project', whoKey: 'gui.settings.hub.who_project' },
  { key: 'system', icon: 'globe', titleKey: 'gui.settings.scope.system', whoKey: 'gui.settings.hub.who_all' },
]

const cards = computed<ScopeCard[]>(() =>
  SCOPES.flatMap((scope) => {
    const pages = menu.value.filter((page) => page.scope === scope.key)
    if (!pages.length) return []
    return [{
      key: scope.key,
      icon: scope.icon,
      title: t(scope.titleKey),
      who: t(scope.whoKey),
      tags: pages.map((page) => t(page.labelKey)),
      to: pages[0].path,
    }]
  }),
)
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

.hub-notice {
  margin-top: 18px;
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

.scope-card:hover {
  transform: translateY(-2px);
  border-color: var(--accent);
  box-shadow: 0 14px 34px #0006;
}

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

@media (max-width: 1120px) {
  .scope-grid {
    grid-template-columns: 1fr;
  }
}
</style>
