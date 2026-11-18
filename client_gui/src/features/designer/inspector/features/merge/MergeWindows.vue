<template>
  <MergeWindow v-for="win in mergeWindows" :key="win.key" :win="win" :target="win.target()" />
</template>
<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import messages from './messages'
import MergeWindow from './MergeWindow.vue'
import { mergeWindows } from './mergeWindows'
const { mergeLocaleMessage } = useI18n({ useScope: 'global' })
for (const locale of ['ko', 'en', 'ja'] as const) mergeLocaleMessage(locale, { gui: { inspector: { merge: messages[locale] } } })
</script>
