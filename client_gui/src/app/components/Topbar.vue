<template>
  <header class="app-header">
    <div class="topbar-brand">
      <div class="brand-icon">FG</div>
      <span class="brand-name">FlowGate</span>
      <span class="brand-ver">v0.2</span>
    </div>

    <div class="header-spacer"></div>

    <!-- GUI generation switch (T0009 §2/§8): this Topbar is the v0.2 App Shell, not a
         Dashboard-owned control, so every future /app/* route rendered under App.vue
         gets the same switch without a per-screen implementation. -->
    <nav class="header-nav">
      <GuiGenerationSwitch target="legacy" />
    </nav>
  </header>
</template>

<script setup lang="ts">
import GuiGenerationSwitch from '@shared/GuiGenerationSwitch.vue'
</script>

<style scoped>
.topbar-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 18px;
  height: 100%;
}
</style>
