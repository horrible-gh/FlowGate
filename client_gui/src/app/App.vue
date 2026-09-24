<template>
  <div class="gui-app-shell">
    <Topbar />
    <RouterView />
  </div>
</template>

<script setup lang="ts">
// T0009 §8: the GUI-generation switch lives in the shared App Shell (this file), not
// inside a Dashboard-owned component, so it stays available to every future /app/*
// route without each screen wiring it up separately.
import { RouterView } from 'vue-router'
import Topbar from './components/Topbar.vue'
</script>

<style scoped>
/* v0.2 has no sidebar column yet (T0009 §1 review): the shared `.app-shell` grid
   (client/shared/app.css) reserves a `var(--sdb-w)` first column for v0.1's sidebar,
   which left every /app/* route auto-placed into that 280px cell instead of the full
   width. This shell is single-column, so the Topbar (`.app-header`, `grid-column: 1/-1`
   from the shared CSS) and the routed view both span the full viewport width. */
.gui-app-shell {
  display: grid;
  grid-template-rows: var(--hdr-h) 1fr;
  grid-template-columns: minmax(0, 1fr);
  height: 100vh;
  overflow: hidden;
}
</style>
