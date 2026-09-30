<template>
  <div class="gui-app-shell">
    <Topbar />
    <div class="gui-app-body">
      <Sidebar />
      <main class="gui-route-content">
        <RouterView />
      </main>
    </div>
  </div>
  <ProjectDialog />
</template>

<script setup lang="ts">
// The v0.2 App Shell (T0011 §5, extended by T0010/T#3 §2): layout and route assembly only
// — Topbar, Sidebar, the routed screen, and (T#3) the single Project Dialog mount. It owns
// no feature state or API calls of its own; each screen under RouterView brings its own,
// and ProjectDialog is reached only through features/project's public surface (D0004 §4.1)
// — this file never touches ProjectList/ProjectRow/ProjectCreateDialog or Dialog-local
// state. The GUI-generation switch stays in the Topbar (T0009 §8).
import { RouterView } from 'vue-router'
import { ProjectDialog } from '@/features/project'
import Sidebar from './components/Sidebar.vue'
import Topbar from './components/Topbar.vue'
</script>

<style scoped>
/* Outer shell is one column: Topbar row over the body row (T0009 rev1: never reuse v0.1's
   shared two-column `.app-shell` grid, which trapped routed content in a sidebar-width
   cell). The sidebar/content split is the body's own grid below. */
.gui-app-shell {
  display: grid;
  grid-template-rows: var(--fg2-topbar-h) minmax(0, 1fr);
  grid-template-columns: minmax(0, 1fr);
  height: 100vh;
  overflow: hidden;
}

.gui-app-body {
  display: grid;
  grid-template-columns: var(--fg2-sidebar-w) minmax(0, 1fr);
  min-width: 0;
  min-height: 0;
}

.gui-route-content {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}

/* Mockup ≤1120px: the page scrolls as a whole instead of each pane scrolling. */
@media (max-width: 1120px) {
  .gui-app-shell {
    height: auto;
    min-height: 100vh;
    overflow: visible;
  }

  .gui-route-content {
    overflow: visible;
  }
}

@media (max-width: 760px) {
  .gui-app-body {
    grid-template-columns: minmax(0, 1fr);
    grid-template-rows: auto 1fr;
  }
}
</style>
