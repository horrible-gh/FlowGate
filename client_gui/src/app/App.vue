<template>
  <div class="gui-app-shell" :class="{ 'is-fullscreen': route.meta.fullscreen }">
    <Topbar v-if="!route.meta.fullscreen" />
    <div class="gui-app-body">
      <Sidebar v-if="!route.meta.fullscreen" />
      <main class="gui-route-content">
        <RouterView v-slot="{ Component }">
          <component :is="Component" @close-designer="closeDesigner" />
        </RouterView>
      </main>
    </div>
  </div>
  <ProjectDialog @opened="openDesigner" />
  <DocumentWindows />
</template>

<script setup lang="ts">
// The v0.2 App Shell (T0011 §5, extended by T0010/T#3 §2): layout and route assembly only
// — Topbar, Sidebar, the routed screen, and (T#3) the single Project Dialog mount. It owns
// no feature state or API calls of its own; each screen under RouterView brings its own,
// and ProjectDialog is reached only through features/project's public surface (D0004 §4.1)
// — this file never touches ProjectList/ProjectRow/ProjectCreateDialog or Dialog-local
// state. The GUI-generation switch stays in the Topbar (T0009 §8).
//
// A route with `meta.fullscreen` (the Designer, flowgate.v02.0025 T0006 §1) is a dedicated
// workspace: Topbar and Sidebar are not rendered and the routed screen gets the whole
// viewport. Every other route keeps the App chrome exactly as before.
//
// flowgate.v02.0040: a Project opened with the Dialog's "열기" goes straight to the Designer.
// features/project is router-free, so the move happens here, at the one Dialog mount both
// the Sidebar and the Dashboard open. If the move does not happen, the Dialog's one-shot
// explicit-open signal is dropped so it cannot reach a later, unrelated Designer entry.
//
// flowgate.v02.0043: the Designer's File › Designer 닫기 leaves the Designer for the Dashboard.
// The Designer Shell is router-free as well, so it emits `close-designer` and the move happens
// here, on the routed screen.
//
// flowgate.v02.0053 T#3: the single mount of the document windows (features/documentWindow). They are
// teleported modeless panels over whatever screen is shown; openers reach them through the feature's
// public surface only.
import { RouterView, useRoute, useRouter } from 'vue-router'
import { DocumentWindows } from '@/features/documentWindow'
import { ProjectDialog, useProjectStore } from '@/features/project'
import Sidebar from './components/Sidebar.vue'
import Topbar from './components/Topbar.vue'

const route = useRoute()
const router = useRouter()
const projectStore = useProjectStore()

// A blocked, aborted or redirected move (anything that does not end on the Designer) counts
// as "did not happen".
async function openDesigner(): Promise<void> {
  try {
    await router.push('/designer')
  } catch {
    // Same as any other move that did not end on the Designer, handled below.
  }
  if (router.currentRoute.value.name !== 'designer') projectStore.takeExplicitOpen()
}

function closeDesigner(): void {
  void router.push('/')
}
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

/* Full-screen workspace route: no Topbar row, no Sidebar column, at every width. */
.gui-app-shell.is-fullscreen {
  grid-template-rows: minmax(0, 1fr);
}

.gui-app-shell.is-fullscreen .gui-app-body {
  grid-template-columns: minmax(0, 1fr);
  grid-template-rows: minmax(0, 1fr);
}
</style>
