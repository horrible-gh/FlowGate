<template>
  <div class="login-wrap">
    <div class="login-bg"></div>

    <div class="login-lang">
      <LanguageSwitch />
    </div>

    <div class="login-card">
      <div class="login-logo">
        <div class="ll-icon">FG</div>
        <span class="ll-name">FlowGate</span>
      </div>

      <div v-if="showLogin && auth.notice === 'invite_accepted'" class="login-alert" role="status">
        <AppIcon name="check" />
        <span>{{ t('auth.invite.accepted') }}</span>
      </div>
      <LoginCard
        v-if="showLogin"
        :loading="auth.state === 'SUBMITTING'"
        :error="loginError"
        :attempt-text="attemptText"
        :warning-text="attemptWarning"
        @submit="auth.login"
      />
      <TotpInput
        v-else-if="showTotp"
        :loading="auth.state === 'TOTP_SUBMITTING'"
        :error="totpError"
        @submit="auth.verifyTotp"
        @use-backup="auth.goToBackup"
        @back="auth.backToIdle"
      />
      <BackupCodeInput
        v-else-if="showBackup"
        :loading="auth.state === 'BACKUP_CODE_SUBMITTING'"
        :error="backupError"
        @submit="auth.verifyBackupCode"
        @back-to-otp="auth.backToTotp"
      />
      <TotpSetup
        v-else-if="showTotpSetup"
        :loading="auth.state === 'TOTP_SETUP_SUBMITTING'"
        :error="totpSetupError"
        :setup="auth.totpSetup"
        @start="auth.startTotpSetup"
        @submit="auth.confirmTotpSetup"
        @back="auth.backToIdle"
      />
      <InviteAccept
        v-else-if="showInvite"
        :loading="auth.state === 'INVITE_SUBMITTING'"
        :error="inviteError"
        :invite="auth.invite"
        @submit="auth.acceptInvite"
        @back="leaveInvite"
      />
      <PasswordChangeRequired
        v-else-if="auth.state === 'PW_CHANGE_REQUIRED'"
        :loading="auth.passwordChangeLoading"
        :error="passwordError"
        :is-initial-change="auth.isInitialChange"
        @submit="auth.changePassword"
      />
      <LockedNotice
        v-else-if="auth.state === 'LOCKED'"
        :locked-until="auth.lockedUntil"
        @unlocked="auth.backToIdle"
      />
      <div v-else class="login-step active">
        <p style="text-align:center; color:var(--text-m);">{{ t('auth.login.redirecting') }}</p>
      </div>

      <div class="login-footer">FlowGate v2.0 &nbsp;·&nbsp; © 2026</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useAuthStore } from '@login/stores/auth'
import AppIcon from '@shared/AppIcon.vue'
import BackupCodeInput from './components/BackupCodeInput.vue'
import InviteAccept from './components/InviteAccept.vue'
import LanguageSwitch from './components/LanguageSwitch.vue'
import LockedNotice from './components/LockedNotice.vue'
import LoginCard from './components/LoginCard.vue'
import PasswordChangeRequired from './components/PasswordChangeRequired.vue'
import TotpInput from './components/TotpInput.vue'
import TotpSetup from './components/TotpSetup.vue'

const auth = useAuthStore()
const { t } = useI18n()

const showLogin = computed(() => ['IDLE', 'SUBMITTING', 'ERROR'].includes(auth.state))
const showTotp = computed(() => ['TOTP_INPUT', 'TOTP_SUBMITTING'].includes(auth.state))
const showBackup = computed(() => ['BACKUP_CODE_INPUT', 'BACKUP_CODE_SUBMITTING'].includes(auth.state))
const showTotpSetup = computed(() => ['TOTP_SETUP', 'TOTP_SETUP_SUBMITTING'].includes(auth.state))
const showInvite = computed(() => ['INVITE', 'INVITE_SUBMITTING'].includes(auth.state))

// flowgate.v02.0020: an invitation link from 시스템 › 사용자 opens the password step.
onMounted(() => {
  const token = new URLSearchParams(window.location.search).get('invite')
  if (token) void auth.openInvite(token)
})

const leaveInvite = () => {
  const url = new URL(window.location.href)
  url.searchParams.delete('invite')
  window.history.replaceState(null, '', url.pathname + url.search)
  auth.backToIdle()
}

const mapNetworkError = (value: string | null): string | null => {
  if (!value) {
    return null
  }

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

const loginError = computed(() => {
  const networkError = mapNetworkError(auth.error)
  if (networkError) {
    return networkError
  }
  if (auth.error === 'invalid_credentials') {
    return t('auth.login.error.invalid_credentials')
  }
  if (auth.error === 'code_expired') {
    return t('auth.totp.error.code_expired')
  }
  return auth.error ? t('error.server') : null
})

const attemptText = computed(() => {
  if (auth.error !== 'invalid_credentials' || auth.failedCount < 1) {
    return null
  }
  return t('auth.login.error.attempt_count', { count: auth.failedCount })
})

const attemptWarning = computed(() => {
  if (auth.error !== 'invalid_credentials' || auth.failedCount < 4) {
    return null
  }
  return t('auth.login.error.attempt_count_warning')
})

const totpError = computed(() => {
  switch (auth.error) {
    case 'code_expired':
      return t('auth.totp.error.code_expired')
    case 'invalid_code':
      return t('auth.totp.error.invalid_code')
    default:
      return mapNetworkError(auth.error) ?? (auth.error ? t('auth.totp.error.invalid_code') : null)
  }
})

const totpSetupError = computed(() => {
  switch (auth.error) {
    case 'invalid_code':
      return t('auth.totp.error.invalid_code')
    case 'code_expired':
      return t('auth.totp.error.code_expired')
    default:
      return mapNetworkError(auth.error) ?? (auth.error ? t('auth.totp_setup.failed') : null)
  }
})

const inviteError = computed(() => {
  switch (auth.error) {
    case 'invite_invalid':
      return t('auth.invite.invalid')
    case 'policy_violation':
      return t('auth.password.error.policy_violation', { policy: t('auth.password.policy_hint') })
    default:
      return mapNetworkError(auth.error) ?? auth.error
  }
})

const backupError = computed(() => {
  switch (auth.error) {
    case 'used_backup_code':
      return t('auth.totp.backup.error.used')
    case 'invalid_backup_code':
    case 'invalid':
      return t('auth.totp.backup.error.invalid')
    default:
      return mapNetworkError(auth.error) ?? (auth.error ? t('auth.totp.backup.error.invalid') : null)
  }
})

const passwordError = computed(() => {
  switch (auth.error) {
    case 'mismatch':
      return t('auth.password.error.mismatch')
    case 'current_incorrect':
      return t('auth.password.error.current_incorrect')
    case 'policy_violation':
      return t('auth.password.error.policy_violation', {
        policy: t('auth.password.policy_hint'),
      })
    default:
      return mapNetworkError(auth.error) ?? auth.error
  }
})
</script>
