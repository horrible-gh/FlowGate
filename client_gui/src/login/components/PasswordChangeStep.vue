<template>
  <div class="fg-login-step">
    <StepIcon name="lock" />
    <h1 id="fg-login-title" class="fg-login-title">{{ t('auth.password.change.title') }}</h1>
    <p class="fg-login-subtitle">{{ message }}</p>

    <form class="fg-login-form" novalidate @submit.prevent="handleSubmit">
      <div v-if="!isInitialChange" class="fg-login-field-group">
        <label class="fg-login-label" for="fg-login-current-password">
          {{ t('auth.password.current_label') }}<span class="fg-login-required">*</span>
        </label>
        <div class="fg-login-field">
          <input
            id="fg-login-current-password"
            v-model="currentPassword"
            class="fg-login-input--plain"
            type="password"
            autocomplete="current-password"
            :disabled="loading"
          />
        </div>
      </div>
      <div class="fg-login-field-group">
        <label class="fg-login-label" for="fg-login-new-password">
          {{ t('auth.password.new_label') }}<span class="fg-login-required">*</span>
        </label>
        <div class="fg-login-field">
          <input
            id="fg-login-new-password"
            v-model="newPassword"
            class="fg-login-input--plain"
            type="password"
            autocomplete="new-password"
            :disabled="loading"
          />
        </div>
      </div>
      <div class="fg-login-field-group">
        <label class="fg-login-label" for="fg-login-confirm-password">
          {{ t('auth.password.confirm_label') }}<span class="fg-login-required">*</span>
        </label>
        <div class="fg-login-field">
          <input
            id="fg-login-confirm-password"
            v-model="confirmPassword"
            class="fg-login-input--plain"
            type="password"
            autocomplete="new-password"
            :disabled="loading"
          />
        </div>
      </div>
      <p class="fg-login-policy" :data-valid="policyValid ? 'true' : 'false'">{{ t('auth.password.policy_hint') }}</p>
      <button class="fg-login-primary" type="submit" :disabled="loading || !formValid">
        <span>{{ t('auth.password.submit') }}</span>
      </button>
    </form>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import StepIcon from './StepIcon.vue'

const props = defineProps<{
  loading: boolean
  isInitialChange: boolean
}>()

const emit = defineEmits<{
  submit: [payload: { current_password?: string; new_password: string }]
  invalid: [message: string]
}>()

const { t } = useI18n()
const currentPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')

const message = computed(() =>
  props.isInitialChange ? t('auth.password.change.initial_message') : t('auth.password.change.expired_message'),
)

// Same client-side policy hint check as v0.1; the server stays the authority.
const policyValid = computed(() => {
  const value = newPassword.value
  if (value.length < 12) return false
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

const handleSubmit = () => {
  if (!policyValid.value) {
    emit('invalid', t('auth.password.error.policy_violation', { policy: t('auth.password.policy_hint') }))
    return
  }
  if (newPassword.value !== confirmPassword.value) {
    emit('invalid', t('auth.password.error.mismatch'))
    return
  }
  emit('submit', {
    current_password: props.isInitialChange ? undefined : currentPassword.value,
    new_password: newPassword.value,
  })
}
</script>
