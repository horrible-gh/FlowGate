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
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useAuthStore } from '@login/stores/auth'
import BackupCodeInput from './components/BackupCodeInput.vue'
import LanguageSwitch from './components/LanguageSwitch.vue'
import LockedNotice from './components/LockedNotice.vue'
import LoginCard from './components/LoginCard.vue'
import PasswordChangeRequired from './components/PasswordChangeRequired.vue'
import TotpInput from './components/TotpInput.vue'

const auth = useAuthStore()
const { t } = useI18n()

const showLogin = computed(() => ['IDLE', 'SUBMITTING', 'ERROR'].includes(auth.state))
const showTotp = computed(() => ['TOTP_INPUT', 'TOTP_SUBMITTING'].includes(auth.state))
const showBackup = computed(() => ['BACKUP_CODE_INPUT', 'BACKUP_CODE_SUBMITTING'].includes(auth.state))

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
