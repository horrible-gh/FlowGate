<template>
  <button class="ll-btn" :class="{ active: locale === 'ko' }" @click="setLang('ko')">KO</button>
  <button class="ll-btn" :class="{ active: locale === 'en' }" @click="setLang('en')">EN</button>
  <button class="ll-btn" :class="{ active: locale === 'ja' }" @click="setLang('ja')">JA</button>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'

const { locale } = useI18n()

const setLang = (code: string) => {
  locale.value = code
  localStorage.setItem('preferred_locale', code)
}
</script>
