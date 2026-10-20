<template>
  <SettingsShell :crumbs="crumbs" scope="me">
    <template #nav>
      <SettingsSubnav :title="t('gui.settings.scope.me')" :items="meNav" active="security" head-icon="user" />
    </template>

    <section class="fgs-panel" data-panel="me-security">
      <div class="fgs-phead">
        <span class="fgs-tico"><SettingsIcon name="shield" size="lg" /></span>
        <h2>{{ t('gui.settings.me_nav.security') }}</h2>
      </div>

      <div class="fgs-panel-body">
        <!-- 로그인 세션: GET /auth/sessions, DELETE /auth/sessions/{sid}, POST revoke-others, 현재 세션은 logout -->
        <div class="fgs-card" data-card="sessions">
          <div class="fgs-card-h">
            <span class="fgs-tico sm"><SettingsIcon name="key" /></span>
            <b>{{ t('gui.settings.self.security.sessions') }}</b>
            <div class="fgs-push">
              <button
                v-if="sessions && others.length"
                type="button"
                class="fgs-btn sm danger"
                :disabled="busy"
                data-testid="revoke-others"
                @click="confirmOthers = true"
              >
                <SettingsIcon name="logout" />{{ t('gui.settings.self.security.revoke_others') }}
              </button>
            </div>
          </div>

          <div v-if="loading && !sessions" class="fgs-line"><LoadingState inline size="sm" :label="t('gui.common.loading')" /></div>
          <div v-else-if="loadError" class="fgs-line">
            <ErrorState compact :title="t('gui.settings.self.errors.sessions_failed')" :message="loadError" data-testid="sessions-load-error">
              <template #action>
                <Button variant="secondary" @click="loadSessions">{{ t('gui.common.retry') }}</Button>
              </template>
            </ErrorState>
          </div>
          <div v-else-if="sessions && !sessions.length" class="fgs-line">
            <EmptyState compact :title="t('gui.settings.self.security.sessions_empty')" data-testid="sessions-empty" />
          </div>
          <template v-else-if="sessions">
            <div v-for="session in sessions" :key="session.session_id" class="fgs-line" :data-session="session.session_id">
              <div class="k">
                <b>{{ session.device_label || t('gui.settings.self.security.unknown_device') }}</b>
                <span v-if="session.is_current" class="fgs-pill ok" data-testid="session-current">{{ t('gui.settings.self.security.current') }}</span>
              </div>
              <div class="v">
                <span class="fgs-muted">{{ session.ip_display || '—' }} · {{ t('gui.settings.self.security.last_used') }} {{ formatDate(session.last_used_at) }}</span>
              </div>
              <div class="v end">
                <button
                  v-if="session.is_current"
                  type="button"
                  class="fgs-btn sm"
                  :disabled="busy"
                  data-testid="logout-current"
                  @click="signOut"
                >
                  <SettingsIcon name="logout" />{{ t('gui.settings.self.security.sign_out_here') }}
                </button>
                <button
                  v-else
                  type="button"
                  class="fgs-btn sm"
                  :disabled="busy"
                  data-testid="revoke-session"
                  @click="revoke(session.session_id)"
                >
                  {{ t('gui.settings.self.security.sign_out') }}
                </button>
              </div>
            </div>
          </template>

          <div v-if="actionError" class="fgs-line">
            <span class="fgs-error" role="alert" data-testid="sessions-error">{{ actionError }}</span>
          </div>
          <div v-else-if="actionNotice" class="fgs-line">
            <span class="fgs-muted" data-testid="sessions-notice">{{ actionNotice }}</span>
          </div>
        </div>

        <!-- 비밀번호 변경: POST /auth/password/change -->
        <form class="fgs-card" data-card="password" novalidate @submit.prevent="submitPassword">
          <div class="fgs-card-h">
            <span class="fgs-tico sm"><SettingsIcon name="pw" /></span>
            <b>{{ t('gui.settings.self.security.password') }}</b>
          </div>
          <div class="fgs-line password-fields">
            <div class="fgs-formfield">
              <label for="me-current-password">{{ t('gui.settings.self.security.current_password') }}</label>
              <input id="me-current-password" v-model="password.current" class="fgs-input" type="password" autocomplete="current-password" :disabled="passwordBusy" data-testid="current-password" />
            </div>
            <div class="fgs-formfield">
              <label for="me-new-password">{{ t('gui.settings.self.security.new_password') }}</label>
              <input id="me-new-password" v-model="password.next" class="fgs-input" type="password" autocomplete="new-password" :disabled="passwordBusy" data-testid="new-password" />
            </div>
            <div class="fgs-formfield">
              <label for="me-confirm-password">{{ t('gui.settings.self.security.confirm_password') }}</label>
              <input id="me-confirm-password" v-model="password.confirm" class="fgs-input" type="password" autocomplete="new-password" :disabled="passwordBusy" data-testid="confirm-password" />
            </div>
          </div>
          <div v-if="passwordError" class="fgs-line">
            <div class="fgs-error" role="alert" data-testid="password-error">
              {{ passwordError }}
              <ul v-if="passwordViolations.length" class="violations">
                <li v-for="violation in passwordViolations" :key="violation">{{ violation }}</li>
              </ul>
            </div>
          </div>
          <div v-else-if="passwordDone" class="fgs-line">
            <span class="fgs-muted" data-testid="password-done">{{ t('gui.settings.self.security.password_changed') }}</span>
          </div>
          <div class="fgs-line">
            <span class="fgs-muted">{{ t('gui.settings.self.security.password_hint') }}</span>
            <div class="fgs-push">
              <button type="submit" class="fgs-btn pri" :disabled="!passwordReady" data-testid="password-submit">
                {{ passwordBusy ? t('gui.settings.saving') : t('gui.settings.self.security.password_submit') }}
              </button>
            </div>
          </div>
        </form>
      </div>
    </section>

    <!-- 다른 세션 모두 로그아웃 확인: 취소하면 요청 0건 -->
    <ModalShell :open="confirmOthers" size="sm" :aria-label="t('gui.settings.self.security.revoke_others_title')" @close="confirmOthers = false">
      <div class="confirm">
        <div class="conf-ico danger"><SettingsIcon name="logout" size="xl" /></div>
        <div class="conf-who">{{ t('gui.settings.self.security.revoke_others_title') }}</div>
        <div class="effects">
          <span class="effect bad"><SettingsIcon name="logout" size="sm" />{{ t('gui.settings.self.security.effect_others_end', { count: others.length }) }}</span>
          <span class="effect good"><SettingsIcon name="check" size="sm" />{{ t('gui.settings.self.security.effect_current_kept') }}</span>
        </div>
      </div>
      <template #footer>
        <div class="fgs-modal-actions">
          <button type="button" class="fgs-btn" data-testid="confirm-cancel" @click="confirmOthers = false">{{ t('gui.common.cancel') }}</button>
          <button type="button" class="fgs-btn danger" :disabled="busy" data-testid="confirm-ok" @click="revokeOthers">{{ t('gui.settings.self.security.revoke_others') }}</button>
        </div>
      </template>
    </ModalShell>
  </SettingsShell>
</template>

<script setup lang="ts">
// 내 설정 › 보안 · 세션 (flowgate.v02.0076 T#2, D §2.2), carried over from v0.1's
// SecuritySessionsView on the same /auth contract:
//
// - GET /auth/sessions lists the active sessions. Another session is ended with
//   DELETE /auth/sessions/{sid} (a 404 means it was already gone); the current one is never
//   DELETEd (the server answers 409) — it is ended by the existing POST /auth/logout.
//   "다른 세션 모두 로그아웃" is POST /auth/sessions/revoke-others after an explicit confirmation.
//   Every action re-reads the list, so what is shown is what the server holds.
// - POST /auth/password/change with the current password; the server checks it and, on success,
//   ends every other session, which the re-read list then shows.
// - 2단계 인증 is not on this screen: no self-service API reports the signed-in person's TOTP
//   state, and POST /auth/totp/setup would replace an existing secret. The administrator routes
//   (/users/{uid}, /system/users/{id}/tfa-reset) are never used here.
// A failure, a cancellation or a 401 is never shown as a success.
import { computed, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '@/components/ui/Button.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import LoadingState from '@/components/ui/LoadingState.vue'
import ModalShell from '@/components/ui/ModalShell.vue'
import { logout } from '@/core/auth/logout'
import SettingsIcon from '../SettingsIcon.vue'
import SettingsShell from '../components/SettingsShell.vue'
import SettingsSubnav from '../components/SettingsSubnav.vue'
import { useMeNav } from '../components/meNav'
import { changePassword, listSessions, MeRequestError, revokeOtherSessions, revokeSession, type AuthSession } from '../meApi'

const { t, locale } = useI18n()
const meNav = useMeNav()

const crumbs = computed(() => [
  { label: t('gui.settings.title'), to: '/settings' },
  { label: t('gui.settings.scope.me') },
  { label: t('gui.settings.me_nav.security') },
])

const sessions = ref<AuthSession[] | null>(null)
const others = computed(() => (sessions.value ?? []).filter((session) => !session.is_current))
const loading = ref(true)
const loadError = ref('')
const busy = ref(false)
const actionError = ref('')
const actionNotice = ref('')
const confirmOthers = ref(false)

function formatDate(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : new Intl.DateTimeFormat(locale.value, { dateStyle: 'medium', timeStyle: 'short' }).format(date)
}

async function loadSessions() {
  loading.value = true
  loadError.value = ''
  try {
    sessions.value = await listSessions()
  } catch (err) {
    sessions.value = null
    loadError.value = err instanceof Error ? err.message : String(err)
  } finally {
    loading.value = false
  }
}

async function revoke(sessionId: string) {
  if (busy.value) return
  busy.value = true
  actionError.value = ''
  actionNotice.value = ''
  try {
    await revokeSession(sessionId)
    actionNotice.value = t('gui.settings.self.security.revoked_one')
  } catch (err) {
    if (err instanceof MeRequestError && err.status === 404) actionNotice.value = t('gui.settings.self.security.session_gone')
    else actionError.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = false
    await loadSessions()
  }
}

async function revokeOthers() {
  if (busy.value) return
  busy.value = true
  actionError.value = ''
  actionNotice.value = ''
  try {
    const result = await revokeOtherSessions()
    const count = typeof result?.revoked_count === 'number' ? result.revoked_count : null
    actionNotice.value = count === null ? t('gui.settings.self.security.revoked_one') : t('gui.settings.self.security.revoked_others', { count })
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = false
    confirmOthers.value = false
    await loadSessions()
  }
}

async function signOut() {
  if (busy.value) return
  busy.value = true
  await logout()
}

const password = reactive({ current: '', next: '', confirm: '' })
const passwordBusy = ref(false)
const passwordError = ref('')
const passwordViolations = ref<string[]>([])
const passwordDone = ref(false)
const passwordReady = computed(() => !passwordBusy.value && Boolean(password.current && password.next && password.confirm))

async function submitPassword() {
  if (!passwordReady.value) return
  passwordError.value = ''
  passwordViolations.value = []
  passwordDone.value = false
  if (password.next !== password.confirm) {
    passwordError.value = t('gui.settings.self.security.mismatch')
    return
  }
  passwordBusy.value = true
  try {
    await changePassword(password.current, password.next)
    password.current = ''
    password.next = ''
    password.confirm = ''
    passwordDone.value = true
    // The server has ended every other session; show the list it now holds.
    await loadSessions()
  } catch (err) {
    passwordError.value = err instanceof Error ? err.message : String(err)
    passwordViolations.value = err instanceof MeRequestError ? err.violations : []
  } finally {
    passwordBusy.value = false
  }
}

onMounted(loadSessions)
</script>

<style scoped>
.fgs-line .k {
  min-width: 220px;
}

.password-fields {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 12px;
  align-items: end;
}

.password-fields .fgs-formfield {
  margin-bottom: 0;
}

.violations {
  margin: 6px 0 0;
  padding-left: 18px;
}

.confirm {
  display: grid;
  justify-items: center;
  gap: 12px;
  padding: 10px 0 4px;
  text-align: center;
}

.conf-ico {
  width: 64px;
  height: 64px;
  border-radius: 18px;
  display: grid;
  place-items: center;
}

.conf-ico.danger { background: #2a1419; color: #ff8c94; }

.conf-who {
  font-size: 15px;
  font-weight: 700;
}

.effects {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: center;
}

.effect {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: 999px;
  padding: 5px 10px;
  font-size: 12px;
  color: #b9c9da;
}

.effect.bad { border-color: #74313a; color: #ff8c94; background: #2a1419; }
.effect.good { border-color: #246c53; color: #71ddb2; background: #0c2a21; }
</style>
