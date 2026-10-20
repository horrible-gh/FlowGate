<template>
  <div class="settings-shell-view">
    <div class="fgs-layout">
      <SettingsNav :current="scope">
        <slot name="nav" />
      </SettingsNav>
      <main class="fgs-content">
        <slot />
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
// The frame every Environment Settings screen renders in (mockup ⑩, flowgate.v02.0077): the
// settings navigation on the left — 환경 설정 개요, the scopes of the shared settings menu
// (../settingsMenu) and, under the scope shown, the page's own sub-navigation from the `nav` slot
// — and the screen's content card on the right. The breadcrumb and the scope tab row of 0076 are
// gone: the navigation already says where the screen is. Loading the page's data stays with the
// page itself.
import SettingsNav from './SettingsNav.vue'
import type { SettingsScopeKey } from '../settingsMenu'
import '../settings.css'

/** 'workspace' is only passed by the Workspace views, which no route opens any more (0076 T#1). */
export type SettingsScope = SettingsScopeKey | 'workspace'

defineProps<{ scope: SettingsScope }>()
</script>

<style scoped>
.settings-shell-view {
  height: 100%;
  min-height: 0;
  overflow-y: auto;
  padding: 22px 28px 48px;
}

.fgs-content {
  min-width: 0;
}

@media (max-width: 1080px) {
  .settings-shell-view {
    padding: 18px 18px 40px;
  }
}
</style>
