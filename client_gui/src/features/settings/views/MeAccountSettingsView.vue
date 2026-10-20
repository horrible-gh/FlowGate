<template>
  <SettingsShell :crumbs="crumbs" scope="me">
    <template #nav>
      <SettingsSubnav :title="t('gui.settings.scope.me')" :items="meNav" active="account" head-icon="user" />
    </template>

    <section class="fgs-panel" data-panel="me-account">
      <div class="fgs-phead">
        <span class="fgs-tico"><SettingsIcon name="user" size="lg" /></span>
        <h2>{{ t('gui.settings.me_nav.account') }}</h2>
      </div>

      <div class="fgs-panel-body">
        <!-- 로그인 정보: GET /auth/me 그대로, 읽기 전용 -->
        <div class="fgs-card" data-card="profile">
          <div class="fgs-card-h">
            <span class="fgs-tico sm"><SettingsIcon name="user" /></span>
            <b>{{ t('gui.settings.self.account.profile') }}</b>
          </div>
          <div class="fgs-line">
            <div class="k">{{ t('gui.settings.self.account.username') }}</div>
            <div class="v" data-testid="me-username">{{ user?.username ?? '—' }}</div>
          </div>
          <div class="fgs-line">
            <div class="k">{{ t('gui.settings.self.account.email') }}</div>
            <div class="v" data-testid="me-email">{{ user?.email || t('gui.settings.self.account.no_email') }}</div>
          </div>
          <div class="fgs-line">
            <span class="fgs-muted">{{ t('gui.settings.self.account.readonly_hint') }}</span>
          </div>
        </div>

        <!-- 표시 언어: 공유 i18n의 ko/ja/en, preferred_locale -->
        <div class="fgs-card" data-card="language">
          <div class="fgs-card-h">
            <span class="fgs-tico sm"><SettingsIcon name="globe" /></span>
            <b>{{ t('gui.settings.self.account.language') }}</b>
          </div>
          <div class="fgs-line">
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
          <div class="fgs-line">
            <span class="fgs-muted">{{ t('gui.settings.self.account.language_hint') }}</span>
          </div>
        </div>

        <!-- 완료 카드 유지 시간: GET/PATCH /me/ui-settings -->
        <div class="fgs-card" data-card="retention">
          <div class="fgs-card-h">
            <span class="fgs-tico sm"><SettingsIcon name="timer" /></span>
            <b>{{ t('gui.settings.self.account.retention') }}</b>
          </div>
          <div v-if="loading" class="fgs-line"><LoadingState inline size="sm" :label="t('gui.common.loading')" /></div>
          <div v-else-if="loadError" class="fgs-line">
            <ErrorState compact :title="t('gui.settings.self.errors.load_failed')" :message="loadError" data-testid="me-retention-load-error">
              <template #action>
                <Button variant="secondary" @click="load">{{ t('gui.common.retry') }}</Button>
              </template>
            </ErrorState>
          </div>
          <template v-else>
            <div class="fgs-line">
              <label class="k" for="me-retention">{{ t('gui.settings.self.account.retention_label') }}</label>
              <div class="v">
                <select id="me-retention" v-model.number="selected" class="fgs-input" :disabled="saving" data-testid="me-retention" @change="save">
                  <option v-for="minutes in choices" :key="minutes" :value="minutes">{{ optionLabel(minutes) }}</option>
                </select>
                <span v-if="saving" class="fgs-muted">{{ t('gui.settings.saving') }}</span>
                <span v-else-if="saved" class="fgs-muted" data-testid="me-retention-saved">{{ t('gui.settings.saved') }}</span>
              </div>
            </div>
            <div v-if="saveError" class="fgs-line">
              <span class="fgs-error" role="alert" data-testid="me-retention-error">{{ saveError }}</span>
            </div>
            <div class="fgs-line">
              <span class="fgs-muted">{{ t('gui.settings.self.account.retention_hint') }}</span>
            </div>
          </template>
        </div>
      </div>
    </section>
  </SettingsShell>
</template>

<script setup lang="ts">
// 내 설정 › 계정 · 화면 (flowgate.v02.0076 T#2, D §2.1).
//
// - 로그인 정보: the username/email of GET /auth/me, which bootstrap already asked (core/auth/
//   currentUser). Read-only: there is no input for them and no profile PATCH exists.
// - 표시 언어: the shared i18n's ko/ja/en, stored as `preferred_locale` the way the v0.1 UserMenu
//   and the Designer header do. @shared/api sends the rendered locale as X-Locale, so the next
//   request follows the switch. No server locale field and no document/message language setting.
// - 완료 카드 유지 시간: AiRunMonitorSettingsView's flow on GET/PATCH /me/ui-settings — the server
//   envelope's choices (kept to the shared domain -1, 0, 30 … 1440), save on change, the value
//   the server answered becomes the confirmed one, a failed save puts the last confirmed value
//   back and says so. A failed GET is an error with a retry, not a default value.
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '@/components/ui/Button.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import LoadingState from '@/components/ui/LoadingState.vue'
import { useCurrentUser } from '@/core/auth/currentUser'
import { RETENTION_IMMEDIATE, RETENTION_NEVER, writeRetentionMirror } from '@shared/aiFinishedCardRetention'
import SettingsIcon from '../SettingsIcon.vue'
import SettingsShell from '../components/SettingsShell.vue'
import SettingsSubnav from '../components/SettingsSubnav.vue'
import { useMeNav } from '../components/meNav'
import { getUiSettings, retentionChoices, retentionIn, saveRetention } from '../meApi'

const LOCALES = ['ko', 'ja', 'en'] as const
type Locale = (typeof LOCALES)[number]
// Each language is named in itself, so it can be found whatever the current one is.
const LOCALE_NAMES: Record<Locale, string> = { ko: '한국어', ja: '日本語', en: 'English' }

const { t, locale } = useI18n()
const { user } = useCurrentUser()
const meNav = useMeNav()

const crumbs = computed(() => [
  { label: t('gui.settings.title'), to: '/settings' },
  { label: t('gui.settings.scope.me') },
  { label: t('gui.settings.me_nav.account') },
])

function setLocale(code: Locale) {
  locale.value = code
  localStorage.setItem('preferred_locale', code)
}

const choices = ref<number[]>([])
const selected = ref<number | null>(null)
// The last value the server confirmed (GET or a successful PATCH); a failed save returns here.
const confirmed = ref<number | null>(null)
const loading = ref(true)
const loadError = ref('')
const saving = ref(false)
const saveError = ref('')
const saved = ref(false)

function optionLabel(minutes: number): string {
  if (minutes === RETENTION_NEVER) return t('gui.settings.self.account.never')
  if (minutes === RETENTION_IMMEDIATE) return t('gui.settings.self.account.immediate')
  if (minutes % 60 !== 0) return t('gui.settings.self.account.minutes', { minutes })
  const hours = minutes / 60
  return hours === 1 ? t('gui.settings.self.account.hour', { hours }) : t('gui.settings.self.account.hours', { hours })
}

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    const body = await getUiSettings()
    const value = retentionIn(body)
    if (value === null) throw new Error(t('gui.settings.self.errors.load_failed'))
    choices.value = retentionChoices(body)
    confirmed.value = value
    selected.value = value
  } catch (err) {
    loadError.value = err instanceof Error ? err.message : String(err)
  } finally {
    loading.value = false
  }
}

/** Re-read after a save whose answer could not be read; the stored value is what is shown. */
async function reread() {
  try {
    const body = await getUiSettings()
    const value = retentionIn(body)
    if (value !== null) {
      confirmed.value = value
      selected.value = value
    }
  } catch {
    // The save error is already on screen; the last confirmed value stays selected.
  }
}

async function save() {
  const requested = selected.value
  if (requested === null || requested === confirmed.value || saving.value) return
  saving.value = true
  saveError.value = ''
  saved.value = false
  try {
    const body = await saveRetention(requested)
    const value = retentionIn(body)
    if (value === null) {
      selected.value = confirmed.value
      saveError.value = t('gui.settings.self.errors.save_unconfirmed')
      await reread()
      return
    }
    // The value the server answered with, not the one that was sent.
    choices.value = retentionChoices(body)
    confirmed.value = value
    selected.value = value
    writeRetentionMirror(value)
    saved.value = true
  } catch (err) {
    selected.value = confirmed.value
    saveError.value = err instanceof Error ? err.message : String(err)
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.fgs-line .k {
  min-width: 150px;
}

.fgs-line select.fgs-input {
  max-width: 280px;
}
</style>
