<template>
  <div class="login-step active">
    <div style="text-align:center; margin-bottom:8px;">
      <div style="width:52px; height:52px; background:var(--primary-l); border-radius:50%; display:flex; align-items:center; justify-content:center; margin:0 auto 12px;">
        <i class="fa-solid fa-shield-halved" style="font-size:1.4rem; color:var(--primary);"></i>
      </div>
      <h2 class="login-title">{{ t('auth.totp.title') }}</h2>
      <p class="login-subtitle">{{ t('auth.totp.instruction') }}</p>
    </div>

    <div v-if="error" class="login-alert login-alert-danger" role="alert">
      <i class="fa-solid fa-circle-exclamation"></i>
      <span>{{ error }}</span>
    </div>

    <div class="totp-group">
      <input
        v-for="(_, i) in 6"
        :key="i"
        :ref="(el) => setDigitRef(el, i)"
        v-model="digits[i]"
        type="text"
        class="totp-digit"
        maxlength="1"
        inputmode="numeric"
        autocomplete="one-time-code"
        :disabled="loading"
        @input="onDigitInput($event, i)"
        @keydown="onDigitKeydown($event, i)"
        @paste.prevent="onPaste($event)"
      />
    </div>

    <button class="btn btn-primary w-full btn-lg" :disabled="loading || code.length !== 6" @click="submitCode">
      <i class="fa-solid fa-check"></i>
      <span>{{ t('auth.totp.submit') }}</span>
    </button>
    <button class="btn btn-ghost w-full" style="margin-top:8px;" :disabled="loading" @click="emit('back')">
      <span>{{ t('auth.totp.back') }}</span>
    </button>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps<{
  loading: boolean
  error: string | null
}>()

const emit = defineEmits<{
  submit: [code: string]
  useBackup: []
  back: []
}>()

const { t } = useI18n()
const digits = ref<string[]>(['', '', '', '', '', ''])
const digitRefs = ref<(HTMLInputElement | null)[]>([null, null, null, null, null, null])

const setDigitRef = (el: unknown, i: number) => {
  digitRefs.value[i] = el as HTMLInputElement | null
}

const code = computed(() => digits.value.join(''))

const focusDigit = (i: number) => {
  if (i >= 0 && i < 6) {
    digitRefs.value[i]?.focus()
  }
}

const onDigitInput = (_event: Event, i: number) => {
  const val = digits.value[i].replace(/\D/g, '').slice(-1)
  digits.value[i] = val
  if (val && i < 5) {
    focusDigit(i + 1)
  }
  if (code.value.length === 6 && !props.loading) {
    emit('submit', code.value)
  }
}

const onDigitKeydown = (event: KeyboardEvent, i: number) => {
  if (event.key === 'Backspace' && !digits.value[i] && i > 0) {
    focusDigit(i - 1)
  }
  if (event.key === 'ArrowLeft' && i > 0) {
    event.preventDefault()
    focusDigit(i - 1)
  }
  if (event.key === 'ArrowRight' && i < 5) {
    event.preventDefault()
    focusDigit(i + 1)
  }
}

const onPaste = (event: ClipboardEvent) => {
  event.preventDefault()
  const text = event.clipboardData?.getData('text') ?? ''
  const clean = text.replace(/\D/g, '').slice(0, 6)
  clean.split('').forEach((ch, i) => {
    digits.value[i] = ch
  })
  const nextFocus = Math.min(clean.length, 5)
  focusDigit(nextFocus)
  if (clean.length === 6 && !props.loading) {
    emit('submit', clean)
  }
}

const submitCode = () => {
  if (code.value.length === 6 && !props.loading) {
    emit('submit', code.value)
  }
}
</script>
