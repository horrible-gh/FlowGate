<template>
  <div class="login-step active">
    <h2 class="login-title">{{ t('auth.password.change.title') }}</h2>
    <p class="login-subtitle">{{ message }}</p>

    <div v-if="displayError" class="login-alert login-alert-danger" role="alert">
      <AppIcon name="warning-circle" />
      <span>{{ displayError }}</span>
    </div>

    <form @submit.prevent="handleSubmit">
      <div v-if="!isInitialChange" class="form-group">
        <label class="form-label req" for="current-password">{{ t('auth.password.current_label') }}</label>
        <input
          id="current-password"
          v-model="currentPassword"
          type="password"
          class="form-ctrl"
          :disabled="loading"
          required
        />
      </div>

      <div class="form-group">
        <label class="form-label req" for="new-password">{{ t('auth.password.new_label') }}</label>
        <input
          id="new-password"
          v-model="newPassword"
          type="password"
          class="form-ctrl"
          :disabled="loading"
          required
        />
      </div>

      <div class="form-group">
        <label class="form-label req" for="confirm-password">{{ t('auth.password.confirm_label') }}</label>
        <input
          id="confirm-password"
          v-model="confirmPassword"
          type="password"
          class="form-ctrl"
          :disabled="loading"
          required
        />
      </div>

      <p :class="policyValid ? 'login-policy valid' : 'login-policy'">{{ t('auth.password.policy_hint') }}</p>

      <div style="display:flex; gap:8px;">
        <button type="submit" class="btn btn-primary w-full btn-lg" :disabled="loading || !formValid">
          {{ t('auth.password.submit') }}
        </button>
        <button type="button" class="btn btn-ghost btn-lg" style="flex-shrink:0;" disabled>
          {{ t('common.cancel') }}
        </button>
      </div>
    </form>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import AppIcon from '@shared/AppIcon.vue'

const props = defineProps<{
  loading: boolean
  error: string | null
  isInitialChange: boolean
}>()

const emit = defineEmits<{
  submit: [payload: { current_password?: string; new_password: string }]
}>()

const { t } = useI18n()
const currentPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const localError = ref<string | null>(null)

const message = computed(() =>
  props.isInitialChange ? t('auth.password.change.initial_message') : t('auth.password.change.expired_message'),
)

const policyValid = computed(() => {
  const value = newPassword.value
  if (value.length < 12) {
    return false
  }

  let categories = 0
  if (/[A-Z]/.test(value)) categories += 1
  if (/[a-z]/.test(value)) categories += 1
  if (/[0-9]/.test(value)) categories += 1
  if (/[^A-Za-z0-9]/.test(value)) categories += 1
  return categories >= 2
})

const formValid = computed(() => {
  const hasCurrent = props.isInitialChange || currentPassword.value.length > 0
  return hasCurrent && policyValid.value && confirmPassword.value === newPassword.value
})

const displayError = computed(() => localError.value ?? props.error)

const handleSubmit = () => {
  if (!policyValid.value) {
    localError.value = t('auth.password.error.policy_violation', {
      policy: t('auth.password.policy_hint'),
    })
    return
  }

  if (newPassword.value !== confirmPassword.value) {
    localError.value = t('auth.password.error.mismatch')
    return
  }

  localError.value = null
  emit('submit', {
    current_password: props.isInitialChange ? undefined : currentPassword.value,
    new_password: newPassword.value,
  })
}
</script>
