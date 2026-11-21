<template>
  <div class="fg-login-step">
    <h1 id="fg-login-title" class="fg-login-title">{{ t('auth.login.title') }}</h1>
    <form class="fg-login-form" novalidate @submit.prevent="handleSubmit">
      <div class="fg-login-field-group">
        <label class="fg-login-label" for="fg-login-username">
          {{ t('auth.login.username_or_email') }}<span class="fg-login-required">*</span>
        </label>
        <div class="fg-login-field">
          <svg class="fg-login-field-icon" width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" /><circle cx="12" cy="7" r="4" /></svg>
          <input
            id="fg-login-username"
            ref="usernameInput"
            v-model.trim="username"
            name="username"
            type="text"
            autocomplete="username"
            spellcheck="false"
            :placeholder="t('auth.login.username_or_email_placeholder')"
            :disabled="loading"
          />
        </div>
      </div>

      <div class="fg-login-field-group fg-login-field-group--password">
        <label class="fg-login-label" for="fg-login-password">
          {{ t('auth.login.password') }}<span class="fg-login-required">*</span>
        </label>
        <div class="fg-login-field">
          <svg class="fg-login-field-icon" width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="4" y="10" width="16" height="11" rx="2" /><path d="M8 10V7a4 4 0 0 1 8 0v3" /></svg>
          <input
            id="fg-login-password"
            v-model="password"
            name="password"
            :type="showPassword ? 'text' : 'password'"
            autocomplete="current-password"
            :placeholder="t('auth.login.password_placeholder')"
            :disabled="loading"
          />
          <button
            type="button"
            class="fg-login-password-toggle"
            data-testid="password-toggle"
            :aria-label="showPassword ? t('auth.login.hide_password') : t('auth.login.show_password')"
            :aria-pressed="showPassword ? 'true' : 'false'"
            @click="showPassword = !showPassword"
          >
            <svg v-if="showPassword" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 3l18 18M10.6 6.1A11.8 11.8 0 0 1 12 6c6.5 0 10 6 10 6a13.2 13.2 0 0 1-3.2 3.7M6.2 6.3C3.5 8.1 2 12 2 12s3.5 6 10 6a10.7 10.7 0 0 0 4.2-.8" /><path d="M10 10a3 3 0 0 0 4 4" /></svg>
            <svg v-else width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12Z" /><circle cx="12" cy="12" r="3" /></svg>
          </button>
        </div>
      </div>

      <button class="fg-login-primary" type="submit" :disabled="loading" data-testid="login-submit">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 17l5-5-5-5M15 12H3" /><path d="M12 3h6a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-6" /></svg>
        <span>{{ t('auth.login.submit') }}</span>
      </button>
    </form>

    <!-- shown as in the mockup; OS sign-in itself comes later, so it does nothing yet. -->
    <button class="fg-login-secondary" type="button" data-testid="os-login">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="3" width="20" height="15" rx="2" /><path d="M8 22h8m-4-4v4" /></svg>
      <span>{{ t('auth.login.os_login') }}</span>
    </button>

    <p class="fg-login-help">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8h.01" /></svg>
      <span>{{ t('auth.login.forgot_password') }} {{ t('auth.login.contact_admin') }}</span>
    </p>
  </div>
</template>

<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import type { LoginCredentials } from '../stores/auth'

defineProps<{
  loading: boolean
}>()

const emit = defineEmits<{
  submit: [payload: LoginCredentials]
  invalid: [message: string]
}>()

const { t } = useI18n()
const username = ref('')
const password = ref('')
const showPassword = ref(false)
const usernameInput = ref<HTMLInputElement | null>(null)

const handleSubmit = () => {
  if (!username.value || !password.value) {
    emit('invalid', t('auth.login.required'))
    return
  }
  emit('submit', {
    username: username.value,
    password: password.value,
    remember_me: false,
  })
}

onMounted(async () => {
  await nextTick()
  usernameInput.value?.focus({ preventScroll: true })
})
</script>
