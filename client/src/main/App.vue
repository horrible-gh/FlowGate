<template>
  <RouterView />
  <ToastContainer />
</template>

<script setup lang="ts">
import { watch } from 'vue'
import { RouterView } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ToastContainer } from './components/common'
import { useDocTypeStore } from './stores/docTypeStore'
import { useProjectStore } from './stores/project'

const { locale } = useI18n()
const docTypeStore = useDocTypeStore()
const projectStore = useProjectStore()

// Reload labels when locale changes
watch(locale, (newLocale) => {
  docTypeStore.loadLabels(newLocale)
})

// Reload labels when project changes (covers initial load after login)
watch(
  () => projectStore.currentProjectId,
  (pid) => {
    if (pid) docTypeStore.loadLabels(locale.value)
  },
  { immediate: true },
)
</script>
