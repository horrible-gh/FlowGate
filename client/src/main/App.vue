<template>
  <RouterView />
  <ToastContainer />
  <!-- Manual-copy fallback for failed clipboard writes (B0001 / group 0221): mounted once at
       the app root because copy failures surface from many components. -->
  <ClipboardFallbackModal />
</template>

<script setup lang="ts">
import { onMounted, watch } from 'vue'
import { RouterView } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ToastContainer } from './components/common'
import ClipboardFallbackModal from './components/ClipboardFallbackModal.vue'
import { useDocTypeStore } from './stores/docTypeStore'
import { useProjectStore } from './stores/project'
import { useAiInvokeRunsStore } from './stores/aiInvokeRuns'

const { locale } = useI18n()
const docTypeStore = useDocTypeStore()
const projectStore = useProjectStore()
const aiInvokeRunsStore = useAiInvokeRunsStore()

// P0008 S1: one bootstrap per app mount restores running + paused + awaiting cards.
// It lives here rather than in the miniplayer because that component now renders inside
// AppHeader, which remounts on every route change (0269 NR0011 §4).
onMounted(() => {
  void aiInvokeRunsStore.bootstrap()
  // 0452 L0003 §2-4: the store was built from the browser mirror, which is a cache. Ask the
  // server once per app mount so a value saved in another browser (or never mirrored here)
  // takes effect on this load rather than on the next recovery signal.
  void aiInvokeRunsStore.refreshRetentionSetting()
})

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
