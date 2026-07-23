<template>
  <div class="login-step active">
    <h2 class="login-title">{{ t('auth.login.title') }}</h2>
    <p class="login-subtitle">{{ t('auth.login.subtitle') }}</p>

    <!-- Error message -->
    <div v-if="error" class="login-alert login-alert-danger">
      <AppIcon name="warning-circle" />
      <div>
        <p>{{ error }}</p>
        <p v-if="attemptText">{{ attemptText }}</p>
        <p v-if="warningText">{{ warningText }}</p>
      </div>
    </div>

    <div class="form-group">
      <label class="form-label req" for="username">{{ t('auth.login.username') }}</label>
      <div class="form-inline" style="position:relative;">
        <AppIcon name="user" style="color:var(--text-m); position:absolute; left:12px; z-index:1;" />
        <input
          id="username"
          ref="usernameInput"
          v-model.trim="username"
          type="text"
          class="form-ctrl"
          style="padding-left:34px;"
          autocomplete="username"
          :placeholder="t('auth.login.username_placeholder')"
          :disabled="loading"
          @keydown.enter="handleSubmit"
        />
      </div>
    </div>

    <div class="form-group">
      <label class="form-label req" for="password">{{ t('auth.login.password') }}</label>
      <div class="form-inline" style="position:relative;">
        <AppIcon name="lock" style="color:var(--text-m); position:absolute; left:12px; z-index:1;" />
        <input
          id="password"
          v-model="password"
          :type="showPassword ? 'text' : 'password'"
          class="form-ctrl"
          style="padding-left:34px; padding-right:36px;"
          autocomplete="current-password"
          placeholder="••••••••"
          :disabled="loading"
          @keydown.enter="handleSubmit"
        />
        <button
          type="button"
          @click="showPassword = !showPassword"
          style="position:absolute; right:10px; color:var(--text-m); font-size:.85rem;"
        >
          <AppIcon :name="showPassword ? 'eye-slash' : 'eye'" />
        </button>
      </div>
    </div>

    <button
      class="btn btn-primary w-full btn-lg"
      style="margin-top:8px;"
      :disabled="loading"
      @click="handleSubmit"
    >
      <AppIcon name="sign-in" />
      <span>{{ t('auth.login.submit') }}</span>
    </button>

    <div class="demo-hint">
      <AppIcon name="info" />
      <span>{{ t('auth.login.forgot_password_hint') }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import AppIcon from '@shared/AppIcon.vue'

interface LoginPayload {
  username: string
  password: string
  remember_me: boolean
}

const props = defineProps<{
  loading: boolean
  error: string | null
  attemptText?: string | null
  warningText?: string | null
}>()

const emit = defineEmits<{
  submit: [payload: LoginPayload]
}>()

const { t } = useI18n()
const username = ref('')
const password = ref('')
const rememberMe = ref(false)
const showPassword = ref(false)
const usernameInput = ref<HTMLInputElement | null>(null)

const focusUsername = async () => {
  await nextTick()
  usernameInput.value?.focus()
}

const handleSubmit = () => {
  emit('submit', {
    username: username.value,
    password: password.value,
    remember_me: rememberMe.value,
  })
}

void props
onMounted(focusUsername)
</script>
