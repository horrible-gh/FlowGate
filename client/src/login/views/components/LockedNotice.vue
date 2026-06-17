<template>
  <div class="login-step active" style="text-align:center;">
    <div class="totp-shield" style="background:var(--warning-l); color:var(--warning);">
      <i class="fa-solid fa-lock"></i>
    </div>
    <h2 class="login-title">{{ t('auth.login.locked.title') }}</h2>
    <p class="login-subtitle">{{ t('auth.login.locked.message') }}</p>
    <p class="login-countdown">{{ t('auth.login.locked.countdown', { time: remainingTime }) }}</p>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps<{
  lockedUntil: string | null
}>()

const emit = defineEmits<{
  unlocked: []
}>()

const { t } = useI18n()
const now = ref(Date.now())
let timerId: number | undefined

const remainingTime = computed(() => {
  if (!props.lockedUntil) {
    return '00:00'
  }

  const remainingMs = new Date(props.lockedUntil).getTime() - now.value
  if (remainingMs <= 0) {
    return '00:00'
  }

  const totalSeconds = Math.ceil(remainingMs / 1000)
  const minutes = String(Math.floor(totalSeconds / 60)).padStart(2, '0')
  const seconds = String(totalSeconds % 60).padStart(2, '0')
  return `${minutes}:${seconds}`
})

const tick = () => {
  now.value = Date.now()
  if (props.lockedUntil && new Date(props.lockedUntil).getTime() <= now.value) {
    emit('unlocked')
  }
}

onMounted(() => {
  tick()
  timerId = window.setInterval(tick, 1000)
})

onBeforeUnmount(() => {
  if (timerId !== undefined) {
    clearInterval(timerId)
  }
})
</script>
