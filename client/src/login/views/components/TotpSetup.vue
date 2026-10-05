<template>
  <div class="login-step active">
    <div style="text-align:center; margin-bottom:8px;">
      <div style="width:52px; height:52px; background:var(--primary-l); border-radius:50%; display:flex; align-items:center; justify-content:center; margin:0 auto 12px;">
        <AppIcon name="shield-checkered" style="font-size:1.4rem; color:var(--primary);" />
      </div>
      <h2 class="login-title">{{ t('auth.totp_setup.title') }}</h2>
      <p class="login-subtitle">{{ t('auth.totp_setup.instruction') }}</p>
    </div>

    <div v-if="error" class="login-alert login-alert-danger" role="alert">
      <AppIcon name="warning-circle" />
      <span>{{ error }}</span>
    </div>

    <template v-if="!setup">
      <button class="btn btn-primary w-full btn-lg" :disabled="loading" data-testid="totp-setup-start" @click="emit('start')">
        <AppIcon name="key" />
        <span>{{ t('auth.totp_setup.start') }}</span>
      </button>
    </template>

    <template v-else>
      <div class="form-group">
        <label class="form-label">{{ t('auth.totp_setup.secret_label') }}</label>
        <code class="form-ctrl" data-testid="totp-setup-secret" style="display:block; user-select:all; letter-spacing:.08em;">{{ groupedSecret }}</code>
      </div>
      <div class="form-group">
        <a :href="setup.qr_uri" class="login-policy" style="word-break:break-all;">{{ t('auth.totp_setup.uri_label') }}</a>
      </div>
      <div v-if="setup.backup_codes.length" class="form-group" data-testid="totp-setup-backup">
        <label class="form-label">{{ t('auth.totp_setup.backup_title') }}</label>
        <code class="form-ctrl" style="display:block; white-space:pre-wrap; user-select:all;">{{ setup.backup_codes.join('\n') }}</code>
      </div>
      <p class="login-subtitle" data-testid="totp-setup-pending">{{ t('auth.totp_setup.pending_hint') }}</p>
      <form @submit.prevent="submit">
        <div class="form-group">
          <label class="form-label req" for="totp-setup-code">{{ t('auth.totp_setup.code_label') }}</label>
          <input
            id="totp-setup-code"
            v-model="code"
            type="text"
            class="form-ctrl"
            inputmode="numeric"
            autocomplete="one-time-code"
            maxlength="6"
            :disabled="loading"
          />
        </div>
        <button type="submit" class="btn btn-primary w-full btn-lg" :disabled="loading || code.length !== 6">
          <AppIcon name="check" />
          <span>{{ t('auth.totp_setup.submit') }}</span>
        </button>
      </form>
    </template>

    <button class="btn btn-ghost w-full" style="margin-top:8px;" :disabled="loading" @click="emit('back')">
      <span>{{ t('auth.totp.back') }}</span>
    </button>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import AppIcon from '@shared/AppIcon.vue'

// flowgate.v02.0020 (시스템 › 보안 › 2단계 인증): enrolment the policy requires at sign-in.
// The login page has no QR renderer, so the key is shown in full for manual entry and the
// otpauth:// link opens an authenticator on devices that handle it.
const props = defineProps<{
  loading: boolean
  error: string | null
  setup: { secret: string; qr_uri: string; backup_codes: string[]; temp_token: string } | null
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
