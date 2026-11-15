<template>
  <div class="fg-login-step">
    <StepIcon name="shield" />
    <h1 id="fg-login-title" class="fg-login-title">{{ t('auth.totp_setup.title') }}</h1>
    <p class="fg-login-subtitle">{{ t('auth.totp_setup.instruction') }}</p>

    <button
      v-if="!setup"
      class="fg-login-primary"
      type="button"
      data-testid="totp-setup-start"
      :disabled="loading"
      @click="emit('start')"
    >
      <span>{{ t('auth.totp_setup.start') }}</span>
    </button>

    <template v-else>
      <div class="fg-login-field-group">
        <span class="fg-login-label">{{ t('auth.totp_setup.secret_label') }}</span>
        <code class="fg-login-code" data-testid="totp-setup-secret">{{ groupedSecret }}</code>
      </div>
      <p class="fg-login-help fg-login-help--left">
        <a :href="setup.qr_uri" class="fg-login-anchor">{{ t('auth.totp_setup.uri_label') }}</a>
      </p>
      <div v-if="setup.backup_codes.length" class="fg-login-field-group" data-testid="totp-setup-backup">
        <span class="fg-login-label">{{ t('auth.totp_setup.backup_title') }}</span>
        <code class="fg-login-code fg-login-code--multiline">{{ setup.backup_codes.join('\n') }}</code>
      </div>
      <p class="fg-login-subtitle fg-login-subtitle--left" data-testid="totp-setup-pending">
        {{ t('auth.totp_setup.pending_hint') }}
      </p>
      <form class="fg-login-form" novalidate @submit.prevent="submit">
        <div class="fg-login-field-group">
          <label class="fg-login-label" for="fg-login-totp-setup-code">
            {{ t('auth.totp_setup.code_label') }}<span class="fg-login-required">*</span>
          </label>
          <div class="fg-login-field">
            <input
              id="fg-login-totp-setup-code"
              v-model="code"
              class="fg-login-input--plain"
              type="text"
              inputmode="numeric"
              autocomplete="one-time-code"
              maxlength="6"
              :disabled="loading"
            />
          </div>
        </div>
        <button class="fg-login-primary" type="submit" :disabled="loading || code.length !== 6">
          <span>{{ t('auth.totp_setup.submit') }}</span>
        </button>
      </form>
    </template>

    <button class="fg-login-secondary" type="button" :disabled="loading" @click="emit('back')">
      <span>{{ t('auth.totp.back') }}</span>
    </button>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { TotpSetupInfo } from '../stores/auth'
import StepIcon from './StepIcon.vue'

// flowgate.v02.0020 (시스템 › 보안 › 2단계 인증): enrolment the policy requires at sign-in.
// The page has no QR renderer, so the key is shown in full for manual entry and the
// otpauth:// link opens an authenticator on devices that handle it.
const props = defineProps<{
  loading: boolean
  setup: TotpSetupInfo | null
}>()

const emit = defineEmits<{
  start: []
  submit: [code: string]
  back: []
}>()

const { t } = useI18n()
const code = ref('')

const groupedSecret = computed(() => (props.setup?.secret ?? '').replace(/(.{4})/g, '$1 ').trim())

watch(code, (value) => {
  const clean = value.replace(/\D/g, '').slice(0, 6)
  if (clean !== value) code.value = clean
})

const submit = () => {
  if (code.value.length === 6 && !props.loading) emit('submit', code.value)
}
</script>
