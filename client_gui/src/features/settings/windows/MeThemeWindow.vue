<template>
  <ModelessShell ref="shell" kind-color="#94a3b8" :open="open" :minimized="minimized" :title="t('gui.taskbar.theme_window')"
    :initial-x="24" :initial-y="initialY" :initial-width="initialWidth" :initial-height="initialHeight" :min-width="260" :min-height="150"
    @update:minimized="emit('update:minimized', $event)" @close="emit('close')">
    <div class="me-menu-window" data-test="me-theme-window">
      <MeThemePanel />
    </div>
  </ModelessShell>
</template>
<script setup lang="ts">
// "내 설정 · 테마" as a Designer window, opened from the taskbar menu card. Only the frame is
// here; the setting is MeThemePanel, the same component the settings page shows. The window title
// already names the setting, so the window holds only the style cards.
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import ModelessShell from '@/components/ui/ModelessShell.vue'
import MeThemePanel from '../components/MeThemePanel.vue'
defineProps<{ open: boolean; minimized: boolean }>()
const emit = defineEmits<{ close: []; 'update:minimized': [value: boolean] }>()
const { t } = useI18n()
const shell = ref<InstanceType<typeof ModelessShell> | null>(null)
// Four cards in one row fit 500px; a narrower screen puts them two by two in a taller window.
const initialWidth = Math.min(500, Math.max(260, window.innerWidth - 48))
const initialHeight = initialWidth >= 480 ? 172 : 286
// Opens just above the menu board's place, clear of the taskbar.
const initialY = Math.max(70, window.innerHeight - 120 - initialHeight)
defineExpose({ bringToFront: () => shell.value?.bringToFront() })
</script>
