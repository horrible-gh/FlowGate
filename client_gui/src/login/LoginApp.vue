<template>
  <nav class="fg-login-languages" aria-label="Language">
    <LanguageToggle />
  </nav>
  <main class="fg-login-viewport">
    <section class="fg-login-card" aria-labelledby="fg-login-title">
      <header class="fg-login-brand" aria-label="FlowGate">
        <span class="fg-login-logo">FG</span>
        <span class="fg-login-wordmark">FlowGate</span>
      </header>

      <LoginForm
        v-if="showLogin"
        :loading="auth.state === 'SUBMITTING'"
        @submit="onLogin"
        @invalid="setLocalError"
      />
      <TotpStep
        v-else-if="showTotp"
        :loading="auth.state === 'TOTP_SUBMITTING'"
        @submit="auth.verifyTotp"
        @use-backup="auth.goToBackup"
        @back="auth.backToIdle"
      />
      <BackupCodeStep
        v-else-if="showBackup"
        :loading="auth.state === 'BACKUP_CODE_SUBMITTING'"
        @submit="auth.verifyBackupCode"
        @back-to-otp="auth.backToTotp"
      />
      <TotpSetupStep
        v-else-if="showTotpSetup"
        :loading="auth.state === 'TOTP_SETUP_SUBMITTING'"
        :setup="auth.totpSetup"
        @start="auth.startTotpSetup"
        @submit="auth.confirmTotpSetup"
        @back="auth.backToIdle"
      />
      <InviteStep
        v-else-if="showInvite"
        :loading="auth.state === 'INVITE_SUBMITTING'"
        :invite="auth.invite"
        @submit="onAcceptInvite"
        @invalid="setLocalError"
        @back="leaveInvite"
      />
      <PasswordChangeStep
        v-else-if="auth.state === 'PW_CHANGE_REQUIRED'"
        :loading="auth.passwordChangeLoading"
        :is-initial-change="auth.isInitialChange"
        @submit="onChangePassword"
        @invalid="setLocalError"
      />
      <LockedStep
        v-else-if="auth.state === 'LOCKED'"
        :locked-until="auth.lockedUntil"
        @unlocked="auth.backToIdle"
      />
      <div v-else class="fg-login-step">
        <h1 id="fg-login-title" class="fg-login-title">{{ t('auth.login.redirecting') }}</h1>
      </div>

      <!-- R0001 §2: status and error messages sit at the bottom of the card, as in the mockup. -->
      <div
        v-if="status"
        class="fg-login-status"
        :data-tone="status.tone"
        :role="status.tone === 'error' ? 'alert' : 'status'"
        aria-live="polite"
        data-testid="login-status"
      >
        <p v-for="line in status.lines" :key="line">{{ line }}</p>
      </div>

      <footer class="fg-login-footer">FlowGate v0.2</footer>
    </section>
  </main>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { MAX_LOGIN_ATTEMPTS, useAuthStore, type LoginCredentials } from './stores/auth'
import BackupCodeStep from './components/BackupCodeStep.vue'
import InviteStep from './components/InviteStep.vue'
import LanguageToggle from './components/LanguageToggle.vue'
import LockedStep from './components/LockedStep.vue'
import LoginForm from './components/LoginForm.vue'
import PasswordChangeStep from './components/PasswordChangeStep.vue'
import TotpSetupStep from './components/TotpSetupStep.vue'
import TotpStep from './components/TotpStep.vue'

interface StatusMessage {
  tone: 'error' | 'success'
  lines: string[]
}

const auth = useAuthStore()
const { t, locale } = useI18n()

/** A check made on this page before anything is sent (empty fields, mismatched passwords). */
const localError = ref<string | null>(null)

const showLogin = computed(() => ['IDLE', 'SUBMITTING', 'ERROR'].includes(auth.state))
const showTotp = computed(() => ['TOTP_INPUT', 'TOTP_SUBMITTING'].includes(auth.state))
const showBackup = computed(() => ['BACKUP_CODE_INPUT', 'BACKUP_CODE_SUBMITTING'].includes(auth.state))
const showTotpSetup = computed(() => ['TOTP_SETUP', 'TOTP_SETUP_SUBMITTING'].includes(auth.state))
const showInvite = computed(() => ['INVITE', 'INVITE_SUBMITTING'].includes(auth.state))

const setLocalError = (message: string) => {
  localError.value = message
}

// A new step (or a new answer from the server) replaces the previous local check.
watch(
  () => auth.state,
  () => {
    localError.value = null
  },
)

const syncDocumentLanguage = () => {
  document.documentElement.lang = String(locale.value)
  document.title = t('auth.login.title')
}
watch(locale, syncDocumentLanguage)

// flowgate.v02.0020: an invitation link from 시스템 › 사용자 (/login?invite=<token>) opens the
// password step.
onMounted(() => {
  syncDocumentLanguage()
  const token = new URLSearchParams(window.location.search).get('invite')
  if (token) void auth.openInvite(token)
})

const onLogin = (credentials: LoginCredentials) => {
  localError.value = null
  void auth.login(credentials)
}

const onAcceptInvite = (password: string) => {
  localError.value = null
  void auth.acceptInvite(password)
}

const onChangePassword = (payload: { current_password?: string; new_password: string }) => {
  localError.value = null
  void auth.changePassword(payload)
}

const leaveInvite = () => {
  const url = new URL(window.location.href)
  url.searchParams.delete('invite')
  window.history.replaceState(null, '', url.pathname + url.search)
  auth.backToIdle()
}

const mapNetworkError = (value: string | null): string | null => {
  switch (value) {
    case 'network':
      return t('error.network')
    case 'unauthorized':
      return t('error.unauthorized')
    case 'forbidden':
      return t('error.forbidden')
    case 'not_found':
      return t('error.not_found')
    case 'server':
      return t('error.server')
    default:
      return null
  }
}

const policyViolation = () => t('auth.password.error.policy_violation', { policy: t('auth.password.policy_hint') })

const loginErrorLines = (): string[] => {
  const network = mapNetworkError(auth.error)
  if (network) return [network]
  if (auth.error === 'code_expired') return [t('auth.totp.error.code_expired')]
  if (auth.error !== 'invalid_credentials') return auth.error ? [t('error.server')] : []
  const lines = [t('auth.login.error.invalid_credentials')]
  if (auth.failedCount >= 1) lines.push(t('auth.login.error.attempt_count', { count: auth.failedCount }))
  if (auth.failedCount >= MAX_LOGIN_ATTEMPTS - 1) lines.push(t('auth.login.error.attempt_count_warning'))
  return lines
}

const totpError = (): string | null => {
  switch (auth.error) {
    case 'code_expired':
      return t('auth.totp.error.code_expired')
    case 'invalid_code':
      return t('auth.totp.error.invalid_code')
    default:
      return mapNetworkError(auth.error) ?? (auth.error ? t('auth.totp.error.invalid_code') : null)
  }
}

const totpSetupError = (): string | null => {
  switch (auth.error) {
    case 'invalid_code':
      return t('auth.totp.error.invalid_code')
    case 'code_expired':
      return t('auth.totp.error.code_expired')
    default:
      return mapNetworkError(auth.error) ?? (auth.error ? t('auth.totp_setup.failed') : null)
  }
}

const backupError = (): string | null => {
  switch (auth.error) {
    case 'used_backup_code':
      return t('auth.totp.backup.error.used')
    case 'invalid_backup_code':
    case 'invalid':
      return t('auth.totp.backup.error.invalid')
    default:
      return mapNetworkError(auth.error) ?? (auth.error ? t('auth.totp.backup.error.invalid') : null)
  }
}

const inviteError = (): string | null => {
  switch (auth.error) {
    case 'invite_invalid':
      return t('auth.invite.invalid')
    case 'policy_violation':
      return policyViolation()
    default:
      return mapNetworkError(auth.error) ?? (auth.error ? t('error.server') : null)
  }
}

const passwordError = (): string | null => {
  switch (auth.error) {
    case 'mismatch':
      return t('auth.password.error.mismatch')
    case 'current_incorrect':
      return t('auth.password.error.current_incorrect')
    case 'policy_violation':
      return policyViolation()
    default:
      return mapNetworkError(auth.error) ?? (auth.error ? t('error.server') : null)
  }
}

const serverErrorLines = (): string[] => {
  let message: string | null = null
  if (showLogin.value) return loginErrorLines()
  if (showTotp.value) message = totpError()
  else if (showBackup.value) message = backupError()
  else if (showTotpSetup.value) message = totpSetupError()
  else if (showInvite.value) message = inviteError()
  else if (auth.state === 'PW_CHANGE_REQUIRED') message = passwordError()
  return message ? [message] : []
}

const status = computed<StatusMessage | null>(() => {
  if (localError.value) return { tone: 'error', lines: [localError.value] }
  const lines = serverErrorLines()
  if (lines.length) return { tone: 'error', lines }
  if (showLogin.value && auth.notice === 'invite_accepted') {
    return { tone: 'success', lines: [t('auth.invite.accepted')] }
  }
  return null
})
</script>
