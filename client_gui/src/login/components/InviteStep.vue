<template>
  <div class="fg-login-step">
    <StepIcon name="mail" />
    <h1 id="fg-login-title" class="fg-login-title">{{ t('auth.invite.title') }}</h1>
    <p v-if="invite" class="fg-login-subtitle">
      {{ t('auth.invite.instruction', { username: invite.username, email: invite.email ?? '' }) }}
    </p>

    <form v-if="invite" class="fg-login-form" novalidate @submit.prevent="handleSubmit">
      <div class="fg-login-field-group">
        <label class="fg-login-label" for="fg-login-invite-password">
          {{ t('auth.password.new_label') }}<span class="fg-login-required">*</span>
        </label>
        <div class="fg-login-field">
          <input
            id="fg-login-invite-password"
            v-model="password"
            class="fg-login-input--plain"
            type="password"
            autocomplete="new-password"
            :disabled="loading"
          />
        </div>
      </div>
      <div class="fg-login-field-group">
        <label class="fg-login-label" for="fg-login-invite-confirm">
          {{ t('auth.password.confirm_label') }}<span class="fg-login-required">*</span>
        </label>
        <div class="fg-login-field">
          <input
            id="fg-login-invite-confirm"
            v-model="confirmPassword"
            class="fg-login-input--plain"
            type="password"
            autocomplete="new-password"
            :disabled="loading"
          />
        </div>
      </div>
      <p class="fg-login-policy">{{ t('auth.password.policy_hint') }}</p>
      <button class="fg-login-primary" type="submit" :disabled="loading || !password || !confirmPassword">
        <span>{{ t('auth.invite.submit') }}</span>
      </button>
    </form>

    <button class="fg-login-secondary" type="button" :disabled="loading" @click="emit('back')">
      <span>{{ t('auth.invite.back') }}</span>
    </button>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import type { InviteInfo } from '../stores/auth'
import StepIcon from './StepIcon.vue'

// flowgate.v02.0020 ("시스템" › "사용자" › "초대"): the invited person sets their own password.
const props = defineProps<{
  loading: boolean
  invite: InviteInfo | null
}>()

const emit = defineEmits<{
  submit: [password: string]
  invalid: [message: string]
  back: []
}>()

const { t } = useI18n()
const password = ref('')
const confirmPassword = ref('')

const handleSubmit = () => {
  if (props.loading) return
  if (password.value !== confirmPassword.value) {
    emit('invalid', t('auth.password.error.mismatch'))
    return
  }
  emit('submit', password.value)
}
</script>
