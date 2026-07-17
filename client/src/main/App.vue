<template>
  <RouterView />
  <ToastContainer />
  <!-- Manual-copy fallback for failed clipboard writes (B0001 / group 0221): mounted once at
       the app root because copy failures surface from many components. -->
  <ClipboardFallbackModal />
  <!-- 실행 미니플레이어 (group 0252 D0007): mounted once at the app root so the
       running/paused/awaiting-Q cards stay visible on every screen. -->
  <AiInvokeMiniplayer />
</template>

<script setup lang="ts">
import { watch } from 'vue'
import { RouterView } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ToastContainer } from './components/common'
import ClipboardFallbackModal from './components/ClipboardFallbackModal.vue'
import AiInvokeMiniplayer from './components/AiInvokeMiniplayer.vue'
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
