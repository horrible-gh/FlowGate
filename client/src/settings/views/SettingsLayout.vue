<template>
  <div class="app-shell settings-app-shell" style="grid-template-columns:1fr; grid-template-rows:var(--hdr-h) 1fr;">
    <SettingsHeader />
    <div style="overflow-y:auto; padding:24px 28px;">
      <div class="settings-shell">
        <SettingsNav />
        <div class="settings-content">
          <RouterView />
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted } from 'vue';
import { RouterView } from 'vue-router';
import SettingsHeader from '../components/SettingsHeader.vue';
import SettingsNav from '../components/SettingsNav.vue';
import { useSettingsStore } from '../stores/settings.js';

const settings = useSettingsStore();
onMounted(() => settings.fetchProjects());
</script>
