<template>
  <button
    v-for="lang in LANGUAGES"
    :key="lang.code"
    type="button"
    class="fg-login-lang"
    :aria-pressed="locale === lang.code ? 'true' : 'false'"
    :data-lang="lang.code"
    @click="setLang(lang.code)"
  >
    {{ lang.label }}
  </button>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'

// Same storage key as the shared i18n instance's initial-locale lookup (`preferred_locale`), so
// the language picked here is the one /app opens in.
const LANGUAGES = [
  { code: 'ko', label: 'KO' },
  { code: 'en', label: 'EN' },
  { code: 'ja', label: 'JA' },
] as const

const { locale } = useI18n()

const setLang = (code: string) => {
  locale.value = code
  localStorage.setItem('preferred_locale', code)
}
</script>
