<template>
  <div class="fg-login-step">
    <StepIcon name="key" />
    <h1 id="fg-login-title" class="fg-login-title">{{ t('auth.totp.backup.title') }}</h1>
    <p class="fg-login-subtitle">{{ t('auth.totp.backup.instruction') }}</p>

    <form class="fg-login-form" novalidate @submit.prevent="submitCode">
      <div class="fg-login-field-group">
        <label class="fg-login-label" for="fg-login-backup-code">
          {{ t('auth.totp.backup.code_label') }}<span class="fg-login-required">*</span>
        </label>
        <div class="fg-login-field">
          <input
            id="fg-login-backup-code"
            v-model.trim="backupCode"
            class="fg-login-input--plain"
            autocomplete="one-time-code"
            spellcheck="false"
            :disabled="loading"
          />
        </div>
      </div>
      <button class="fg-login-primary" type="submit" :disabled="loading || backupCode.length === 0">
        <span>{{ t('auth.totp.backup.submit') }}</span>
      </button>
    </form>

    <button class="fg-login-secondary" type="button" :disabled="loading" @click="emit('backToOtp')">
      <span>{{ t('auth.totp.backup.back_to_otp') }}</span>
    </button>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import StepIcon from './StepIcon.vue'

const props = defineProps<{
  loading: boolean
}>()

const emit = defineEmits<{
  submit: [code: string]
  backToOtp: []
}>()

const { t } = useI18n()
const backupCode = ref('')

const submitCode = () => {
  if (!props.loading && backupCode.value) emit('submit', backupCode.value)
}
</script>
