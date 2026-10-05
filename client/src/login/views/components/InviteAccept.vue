<template>
  <div class="login-step active">
    <h2 class="login-title">{{ t('auth.invite.title') }}</h2>
    <p v-if="invite" class="login-subtitle">{{ t('auth.invite.instruction', { username: invite.username, email: invite.email ?? '' }) }}</p>

    <div v-if="displayError" class="login-alert login-alert-danger" role="alert">
      <AppIcon name="warning-circle" />
      <span>{{ displayError }}</span>
    </div>

    <form v-if="invite" @submit.prevent="handleSubmit">
      <div class="form-group">
        <label class="form-label req" for="invite-password">{{ t('auth.password.new_label') }}</label>
        <input id="invite-password" v-model="password" type="password" class="form-ctrl" :disabled="loading" required />
      </div>
      <div class="form-group">
        <label class="form-label req" for="invite-confirm">{{ t('auth.password.confirm_label') }}</label>
        <input id="invite-confirm" v-model="confirmPassword" type="password" class="form-ctrl" :disabled="loading" required />
      </div>
      <button type="submit" class="btn btn-primary w-full btn-lg" :disabled="loading || !password || !confirmPassword">
        {{ t('auth.invite.submit') }}
      </button>
    </form>

    <button class="btn btn-ghost w-full" style="margin-top:8px;" :disabled="loading" @click="emit('back')">
      <span>{{ t('auth.invite.back') }}</span>
    </button>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import AppIcon from '@shared/AppIcon.vue'

// flowgate.v02.0020 (시스템 › 사용자 › 초대): the invited person sets their own password.
const props = defineProps<{
  loading: boolean
  error: string | null
  invite: { username: string; email: string | null } | null
}>()

const emit = defineEmits<{
  submit: [password: string]
  back: []
}>()

const { t } = useI18n()
const password = ref('')
const confirmPassword = ref('')
const mismatch = ref(false)

const displayError = computed(() => {
  if (mismatch.value) return t('auth.password.error.mismatch')
  return props.error
})

const handleSubmit = () => {
  mismatch.value = password.value !== confirmPassword.value
  if (mismatch.value || props.loading) return
  emit('submit', password.value)
}
</script>
