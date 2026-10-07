<template>
  <!-- flowgate.v02.0038 — Designer Header (0001-R §2): brand, notification and AI entry on the
       left, the search assist in the middle, language / settings / user on the right.
       The side groups never shrink below their content; the search takes what is left
       (up to 680px), and narrow windows switch the side groups to a compact form.
       Notification, AI, settings and the user button are entry points only: they emit a
       command and a later Feature connects them. The language switch is the existing shared
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
          data-test="designer-header-notifications"
          @click="emit('command', 'header.notifications')"
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9" />
            <path d="M10 21h4" />
          </svg>
        </button>
        <button
          type="button"
          class="designer-header-btn is-ai"
          data-test="designer-header-ai"
          @click="emit('command', 'header.ai')"
        >
          <span aria-hidden="true">🤖</span><b>{{ t('gui.designer.top.header.ai') }}</b>
        </button>
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
        @click="emit('command', 'header.settings')"
      >
        <span aria-hidden="true">⚙</span><span class="designer-header-label">{{ t('gui.designer.top.header.settings') }}</span>
      </button>
      <button
        v-if="user"
        type="button"
        class="designer-header-user"
        :aria-label="t('gui.designer.top.header.user', { name: user.username })"
        data-test="designer-header-user"
        @click="emit('command', 'header.user')"
      >
        <span class="designer-header-avatar" aria-hidden="true">{{ initial }}</span>
        <b class="designer-header-label">{{ user.username }}</b>
        <span aria-hidden="true">⌄</span>
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useCurrentUser } from '@/core/auth/currentUser'
import GlobalSearch from './GlobalSearch.vue'
import { DESIGNER_LOCALES, type DesignerLocale, type DesignerSearchRequest, type DesignerTopCommand } from './designerTop'

const emit = defineEmits<{
  command: [command: DesignerTopCommand]
  search: [request: DesignerSearchRequest]
}>()

const { t, locale } = useI18n()
const { user } = useCurrentUser()

const initial = computed(() => user.value?.username.charAt(0).toUpperCase() ?? '')

function setLocale(code: DesignerLocale) {
  locale.value = code
  localStorage.setItem('preferred_locale', code)
}
</script>

<style scoped>
.designer-header {
  display: grid;
  /* Side columns are at least as wide as their content, so the brand / notification / AI
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

.designer-header-btn.is-ai {
  border-color: #356eff;
  background: #10265a;
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
