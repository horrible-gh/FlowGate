<template>
  <div class="fg-login-step">
    <StepIcon name="shield" />
    <h1 id="fg-login-title" class="fg-login-title">{{ t('auth.totp.title') }}</h1>
    <p class="fg-login-subtitle">{{ t('auth.totp.instruction') }}</p>

    <div class="fg-login-totp" role="group" :aria-label="t('auth.totp.code_label')">
      <input
        v-for="(_, i) in DIGITS"
        :key="i"
        :ref="(el) => setDigitRef(el, i)"
        v-model="digits[i]"
        type="text"
        class="fg-login-totp-digit"
        maxlength="1"
        inputmode="numeric"
        autocomplete="one-time-code"
        :aria-label="`${t('auth.totp.code_label')} ${i + 1}`"
        :disabled="loading"
        @input="onDigitInput(i)"
        @keydown="onDigitKeydown($event, i)"
        @paste.prevent="onPaste($event)"
      />
    </div>

    <button class="fg-login-primary" type="button" :disabled="loading || code.length !== DIGITS" @click="submitCode">
      <span>{{ t('auth.totp.submit') }}</span>
    </button>
    <button class="fg-login-link" type="button" data-testid="totp-use-backup" :disabled="loading" @click="emit('useBackup')">
      {{ t('auth.totp.use_backup') }}
    </button>
    <button class="fg-login-secondary" type="button" :disabled="loading" @click="emit('back')">
      <span>{{ t('auth.totp.back') }}</span>
    </button>
    <p class="fg-login-help">{{ t('auth.totp.lost_hint') }}</p>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import StepIcon from './StepIcon.vue'

const DIGITS = 6

const props = defineProps<{
  loading: boolean
}>()

const emit = defineEmits<{
  submit: [code: string]
  useBackup: []
  back: []
}>()

const { t } = useI18n()
const digits = ref<string[]>(Array.from({ length: DIGITS }, () => ''))
const digitRefs: (HTMLInputElement | null)[] = Array.from({ length: DIGITS }, () => null)

const setDigitRef = (el: unknown, i: number) => {
  digitRefs[i] = (el as HTMLInputElement | null) ?? null
}

const code = computed(() => digits.value.join(''))

const focusDigit = (i: number) => {
  if (i >= 0 && i < DIGITS) digitRefs[i]?.focus()
}

const submitIfComplete = () => {
  if (code.value.length === DIGITS && !props.loading) emit('submit', code.value)
}

const onDigitInput = (i: number) => {
  const value = (digits.value[i] ?? '').replace(/\D/g, '').slice(-1)
  digits.value[i] = value
  if (value && i < DIGITS - 1) focusDigit(i + 1)
  submitIfComplete()
}

const onDigitKeydown = (event: KeyboardEvent, i: number) => {
  if (event.key === 'Backspace' && !digits.value[i] && i > 0) focusDigit(i - 1)
  if (event.key === 'ArrowLeft' && i > 0) {
    event.preventDefault()
    focusDigit(i - 1)
  }
  if (event.key === 'ArrowRight' && i < DIGITS - 1) {
    event.preventDefault()
    focusDigit(i + 1)
  }
}

const onPaste = (event: ClipboardEvent) => {
  const clean = (event.clipboardData?.getData('text') ?? '').replace(/\D/g, '').slice(0, DIGITS)
  clean.split('').forEach((ch, i) => {
    digits.value[i] = ch
  })
  focusDigit(Math.min(clean.length, DIGITS - 1))
  submitIfComplete()
}

const submitCode = () => submitIfComplete()

onMounted(async () => {
  await nextTick()
  focusDigit(0)
})
</script>
