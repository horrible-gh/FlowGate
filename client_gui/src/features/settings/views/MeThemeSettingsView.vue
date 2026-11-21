<template>
  <SettingsShell scope="me">
    <template #nav><SettingsSubnav :items="meNav" active="theme" /></template>
    <section class="fgs-panel" data-panel="me-theme">
      <div class="fgs-phead"><span class="fgs-tico"><SettingsIcon name="gear" size="lg" /></span><div class="fgs-ptitle"><h2>{{ t('gui.taskbar.theme') }}</h2><p>{{ t('gui.taskbar.theme_hint') }}</p></div></div>
      <div class="fgs-panel-body"><MeThemePanel /></div>
    </section>
  </SettingsShell>
</template>
<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import SettingsShell from '../components/SettingsShell.vue'
import SettingsSubnav from '../components/SettingsSubnav.vue'
import SettingsIcon from '../SettingsIcon.vue'
import MeThemePanel from '../components/MeThemePanel.vue'
import { useMeNav } from '../components/meNav'
const { t } = useI18n()
const meNav = useMeNav()
</script>
