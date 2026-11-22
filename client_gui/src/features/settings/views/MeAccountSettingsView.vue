<template>
  <component :is="embedded ? 'div' : SettingsShell" :scope="embedded ? undefined : 'me'" :class="{ 'me-account-embedded': embedded }">
    <template #nav>
      <SettingsSubnav :items="meNav" active="account" />
    </template>

    <section class="fgs-panel" data-panel="me-account">
      <div class="fgs-phead">
        <span class="fgs-tico"><SettingsIcon name="user" size="lg" /></span>
        <div class="fgs-ptitle">
          <h2>{{ t('gui.settings.me_nav.account') }}</h2>
          <p>{{ t('gui.settings.page_sub.me_account') }}</p>
        </div>
      </div>

      <div class="fgs-panel-body">
        <!-- Sign-in information: read-only values returned by GET /auth/me -->
        <div class="fgs-sec">
          <div class="fgs-sec-head">
            <h3>{{ t('gui.settings.self.account.profile') }}</h3>
            <div class="meta">{{ t('gui.settings.self.account.readonly_hint') }}</div>
          </div>
          <div class="fgs-rows" data-card="profile">
            <div class="fgs-row">
              <div class="fgs-row-label"><strong>{{ t('gui.settings.self.account.username') }}</strong></div>
              <div class="fgs-row-ctrl" data-testid="me-username">{{ user?.username ?? '—' }}</div>
            </div>
            <div class="fgs-row">
              <div class="fgs-row-label"><strong>{{ t('gui.settings.self.account.email') }}</strong></div>
              <div class="fgs-row-ctrl fgs-muted" data-testid="me-email">{{ user?.email || t('gui.settings.self.account.no_email') }}</div>
            </div>
          </div>
        </div>

        <div class="fgs-sec">
          <div class="fgs-sec-head"><h3>{{ t('gui.settings.hub.tags.display') }}</h3></div>
          <div class="fgs-rows">
            <!-- Display language: shared i18n ko/ja/en, preferred_locale -->
            <div class="fgs-row" data-card="language">
              <div class="fgs-row-label">
                <strong>{{ t('gui.settings.self.account.language') }}</strong>
                <small>{{ t('gui.settings.self.account.language_hint') }}</small>
              </div>
              <div class="fgs-row-ctrl">
                <div class="fgs-segment" role="group" :aria-label="t('gui.settings.self.account.language')">
                  <button
                    v-for="code in LOCALES"
                    :key="code"
                    type="button"
                    :class="{ on: locale === code }"
                    :aria-pressed="locale === code ? 'true' : 'false'"
                    :data-locale="code"
                    @click="setLocale(code)"
                  >
                    {{ LOCALE_NAMES[code] }}
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  </component>
</template>

<script setup lang="ts">
// My settings > Account / display (flowgate.v02.0076 T#2, D 2.1).
//
// - Sign-in information: the username/email of GET /auth/me, which bootstrap already asked
//   (core/auth/currentUser). Read-only: there is no input for them and no profile PATCH exists.
// - Display language: the shared i18n's ko/ja/en, stored as `preferred_locale` the way the v0.1
//   UserMenu and the Designer header do. @shared/api sends the rendered locale as X-Locale, so the
//   next request follows the switch. No server locale field and no document/message language setting.
// - The finished-card retention row was removed in v02test.0055: no v0.2 screen reads that value.
//   This screen therefore sends no request of its own.
// - `embedded` draws the same panel without the settings page frame, for the Designer window.
import { useI18n } from 'vue-i18n'
import { useCurrentUser } from '@/core/auth/currentUser'
import SettingsIcon from '../SettingsIcon.vue'
import SettingsShell from '../components/SettingsShell.vue'
import SettingsSubnav from '../components/SettingsSubnav.vue'
import { useMeNav } from '../components/meNav'

defineProps<{ embedded?: boolean }>()

const LOCALES = ['ko', 'ja', 'en'] as const
type Locale = (typeof LOCALES)[number]
// Each language is named in itself, so it can be found whatever the current one is.
const LOCALE_NAMES: Record<Locale, string> = { ko: '한국어', ja: '日本語', en: 'English' }

const { t, locale } = useI18n()
const { user } = useCurrentUser()
const meNav = useMeNav()

function setLocale(code: Locale) {
  locale.value = code
  localStorage.setItem('preferred_locale', code)
}
</script>
