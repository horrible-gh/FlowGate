<template>
  <div class="login-step active">
    <h2 class="login-title">{{ t('auth.totp.backup.title') }}</h2>
    <p class="login-subtitle">{{ t('auth.totp.backup.instruction') }}</p>

    <div v-if="error" class="login-alert login-alert-danger" role="alert">
      <AppIcon name="warning-circle" />
      <span>{{ error }}</span>
    </div>

    <form @submit.prevent="submitCode">
      <div class="form-group">
        <label class="form-label req" for="backup-code">{{ t('auth.totp.backup.code_label') }}</label>
        <input
          id="backup-code"
          v-model.trim="backupCode"
          class="form-ctrl"
          :disabled="loading"
          autocomplete="one-time-code"
          required
        />
      </div>

      <button type="submit" class="btn btn-primary w-full btn-lg" :disabled="loading || backupCode.length === 0">
        <AppIcon name="check" />
        <span>{{ t('auth.totp.backup.submit') }}</span>
      </button>
    </form>

    <div style="margin-top:12px;">
      <button type="button" class="btn btn-ghost w-full" :disabled="loading" @click="emit('backToOtp')">
        {{ t('auth.totp.backup.back_to_otp') }}
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import AppIcon from '@shared/AppIcon.vue'

const props = defineProps<{
  loading: boolean
  error: string | null
}>()

const emit = defineEmits<{
  submit: [code: string]
  backToOtp: []
}>()

const { t } = useI18n()
const backupCode = ref('')

const submitCode = () => {
  if (!props.loading && backupCode.value) {
    emit('submit', backupCode.value)
  }
}
</script>
