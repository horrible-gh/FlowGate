<template>
  <header class="app-header gui-topbar">
    <RouterLink to="/" class="gui-brand" :aria-label="t('gui.brand.home_aria')">
      <span class="gui-brandmark" aria-hidden="true"><i></i><i></i><i></i></span>
      <span class="gui-brand-text">
        <span class="gui-brand-title">FlowGate</span>
        <span class="gui-brand-sub">{{ t('gui.brand.tagline') }}</span>
      </span>
    </RouterLink>

    <!-- Search is a visual shell only (T0011 §7): there is no search feature yet, so the
         field is disabled and says so instead of pretending to search. -->
    <div class="gui-search-wrap">
      <div class="gui-search" role="search" :title="t('gui.topbar.search_unavailable')">
        <NavGlyph name="search" class="gui-search-icon" />
        <input
          type="search"
          class="gui-search-input"
          :placeholder="t('gui.topbar.search_placeholder')"
          :aria-label="t('gui.topbar.search_aria')"
          disabled
        />
        <span class="gui-kbd" aria-hidden="true">Ctrl + K</span>
      </div>
    </div>

    <div class="gui-user-area">
      <!-- GUI generation switch (T0009 §2/§8): owned by the App Shell Topbar, not by any
           route view, so every /app/* screen keeps the way back to v0.1. -->
      <nav class="header-nav" :aria-label="t('gui.topbar.switch_aria')">
        <GuiGenerationSwitch target="legacy" />
      </nav>
      <button
        type="button"
        class="gui-bell"
        disabled
        :aria-label="t('gui.topbar.notifications_aria')"
        :title="t('gui.topbar.notifications_aria')"
      >
        <NavGlyph name="bell" :stroke-width="1.6" />
      </button>
      <div v-if="user" class="gui-user">
        <span class="gui-avatar" aria-hidden="true">{{ initial }}</span>
        <span class="gui-user-meta">
          <span class="gui-user-name" :title="user.username">{{ user.username }}</span>
          <span class="gui-user-sub">{{ roleLabel }}</span>
        </span>
      </div>
    </div>
  </header>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { RouterLink } from 'vue-router'
import GuiGenerationSwitch from '@shared/GuiGenerationSwitch.vue'
import { useCurrentUser } from '@/core/auth/currentUser'
import NavGlyph from './NavGlyph.vue'

const { t } = useI18n()
const { user } = useCurrentUser()

const initial = computed(() => user.value?.username.charAt(0).toUpperCase() ?? '')
const roleLabel = computed(() =>
  user.value?.isAdmin ? t('gui.topbar.user_role_admin') : t('gui.topbar.user_role_member'),
)
</script>

<style scoped>
.gui-topbar {
  display: grid;
  grid-template-columns: var(--fg2-sidebar-w) minmax(0, 1fr) auto;
  align-items: center;
  min-width: 0;
  border-bottom: 1px solid var(--fg2-line);
  background: rgba(5, 12, 20, 0.72);
  backdrop-filter: blur(16px);
}

.gui-brand {
  display: flex;
  gap: 14px;
  align-items: center;
  min-width: 0;
  padding-left: 24px;
}

.gui-brandmark {
  width: 28px;
  height: 38px;
  flex: 0 0 28px;
  display: flex;
  gap: 4px;
  align-items: flex-end;
}

.gui-brandmark i {
  display: block;
  width: 6px;
  border-radius: 2px 2px 0 0;
  background: linear-gradient(180deg, #5eb4ff, #3d8cff);
  box-shadow: 0 0 16px rgba(75, 163, 255, 0.25);
}

.gui-brandmark i:nth-child(1) {
  height: 16px;
}

.gui-brandmark i:nth-child(2) {
  height: 30px;
}

.gui-brandmark i:nth-child(3) {
  height: 38px;
}

.gui-brand-text {
  display: block;
  min-width: 0;
}

.gui-brand-title {
  display: block;
  font-weight: 800;
  font-size: 24px;
  line-height: 1.1;
}

.gui-brand-sub {
  display: block;
  margin-top: 5px;
  font-size: 12px;
  color: var(--fg2-muted);
  letter-spacing: 0;
}

.gui-search-wrap {
  display: flex;
  justify-content: center;
  min-width: 0;
  padding: 0 22px;
}

.gui-search {
  width: min(660px, 100%);
  height: 42px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 14px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: 8px;
  background: linear-gradient(180deg, rgba(18, 31, 47, 0.92), rgba(12, 24, 38, 0.92));
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.02);
  color: var(--fg2-muted);
  cursor: not-allowed;
}

.gui-search-icon {
  flex: 0 0 17px;
  width: 17px;
  height: 17px;
  opacity: 0.78;
}

.gui-search-input {
  flex: 1;
  min-width: 0;
  padding: 0;
  border: 0;
  background: transparent;
  color: inherit;
  font-size: 13px;
  cursor: not-allowed;
}

.gui-search-input::placeholder {
  color: var(--fg2-muted);
  opacity: 1;
}

/* The mockup's `.search span { flex: 1 }` also matches its kbd span, so the shortcut
   box shares the bar's width with the placeholder — reproduced as drawn. */
.gui-kbd {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  color: #c4d1e0;
  background: #0b1624;
  border: 1px solid var(--fg2-line);
  padding: 4px 7px;
  border-radius: 5px;
  white-space: nowrap;
}

.gui-user-area {
  height: 100%;
  min-width: 300px;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 18px;
  padding: 0 28px 0 8px;
}

.header-nav {
  display: flex;
  align-items: center;
}

/* The shared switch button carries v0.1's `.hdr-btn` class, whose rules live in
   client/shared/app.css — deliberately not loaded here (T0011 §11) — so the v0.2 look is
   defined locally. */
.header-nav :deep(.gui-switch-btn) {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  padding: 0 12px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: 16px;
  background: rgba(12, 24, 38, 0.78);
  color: #c7d5e6;
  font-size: 12px;
  white-space: nowrap;
  cursor: pointer;
  transition: 0.16s ease;
}

.header-nav :deep(.gui-switch-btn:hover) {
  border-color: rgba(130, 170, 220, 0.45);
  color: var(--fg2-text);
}

.gui-bell {
  position: relative;
  width: 24px;
  height: 24px;
  flex: 0 0 24px;
  padding: 0;
  border: 0;
  background: none;
  color: #b8c6d8;
  cursor: not-allowed;
}

.gui-bell svg {
  width: 24px;
  height: 24px;
}

.gui-user {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.gui-avatar {
  width: 42px;
  height: 42px;
  flex: 0 0 42px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  border: 1px solid rgba(255, 255, 255, 0.06);
  background: linear-gradient(145deg, #52647b, #2f3d50);
  font-size: 20px;
}

.gui-user-meta {
  display: block;
  min-width: 96px;
  max-width: 160px;
}

.gui-user-name,
.gui-user-sub {
  display: block;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.gui-user-name {
  font-size: 14px;
}

.gui-user-sub {
  margin-top: 3px;
  font-size: 12px;
  color: var(--fg2-muted);
}

@media (max-width: 1120px) {
  .gui-topbar {
    position: sticky;
    top: 0;
    z-index: 20;
  }

  .gui-user-area {
    min-width: 0;
    gap: 14px;
    padding-right: 20px;
  }
}

@media (max-width: 760px) {
  .gui-topbar {
    grid-template-columns: minmax(0, 1fr) auto;
  }

  .gui-brand {
    padding-left: 16px;
  }

  .gui-search-wrap,
  .gui-user-meta {
    display: none;
  }

  .gui-user-area {
    padding-right: 16px;
  }
}
</style>
