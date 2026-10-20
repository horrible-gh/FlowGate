<template>
  <div class="gui-startup-loading" data-testid="app-startup-loading">
    <LoadingState :label="label" />
  </div>
</template>

<script setup lang="ts">
// flowgate.v02.0076 T#1 (D §1.2, §5.2 V03): what the start screen shows while the bootstrap's
// /auth/me answer is still out. main.ts mounts it on #app in place of the App Shell and
// replaces it with the real app once the answer is in, so it is a neutral loading state only:
// no router, no Topbar/Sidebar, no settings view or project picker, and no request of its own.
//
// The label comes in as a prop: this short-lived app must not install the shared i18n, because
// vue-i18n disposes its instance when an app it is installed on unmounts.
import LoadingState from '@/components/ui/LoadingState.vue'

defineProps<{ label: string }>()
</script>

<style scoped>
.gui-startup-loading {
  min-height: 100vh;
  display: grid;
  place-items: center;
}
</style>
