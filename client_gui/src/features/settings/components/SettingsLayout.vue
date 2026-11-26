<template>
  <div v-if="!showView" class="settings-layout-loading" data-testid="settings-loading">
    <LoadingState :label="t('gui.settings.loading')" />
  </div>
  <RouterView v-else />
</template>

<script setup lang="ts">
// (D): the parent of every /app/settings route. It owns only
// the loading state and the routed body; each page brings its own frame (SettingsShell:
// navigation, header, body) and its own API calls — nothing here loads page data.
//
// While /auth/me is still out, only a neutral loading state renders, so no settings view (no
// administrator screen and none of its requests) is mounted. When the answer arrives — or the
// signed-in person changes — the current URL is judged again by the router guard's rule
// (../access) and a refused URL is replaced by the overview before its view can render.
import { computed, watch, watchEffect } from 'vue'
import { RouterView, useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import LoadingState from '@/components/ui/LoadingState.vue'
import { useCurrentUser } from '@/core/auth/currentUser'
import { currentSettingsAccess, settingsAccessTarget, settingsHome, settingsRequirementOf } from '../access'
import { loadSettingsAccess } from '../capabilities'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const { status } = useCurrentUser()

// currentSettingsAccess reads the current user's refs and the route, so this follows both.
const access = computed(() => currentSettingsAccess(route))
const showView = computed(() => status.value !== 'loading' && access.value.kind === 'allow')

// `immediate`: a screen that needs the project list's capabilities is let through while that answer is out
// (the guard says `pending`); when this layout mounts the answer may already be in and say no.
watch(access, (next) => {
  if (next.kind === 'home') void router.replace(settingsHome(next))
}, { immediate: true })

// A workspace screen waits for the capabilities of the workspaces (the server's own answer): read them
// now. Nothing of the screen mounts, and no screen request is sent, until then. (A project screen's
// answer is asked for by the shared capability store itself.)
const needsWorkspaces = computed(() => {
  const target = settingsAccessTarget(route)
  return Boolean(settingsRequirementOf(target.path, target.adminOnly)?.workspace)
})
watchEffect(() => {
  if (access.value.kind === 'pending' && status.value === 'ready' && needsWorkspaces.value) void loadSettingsAccess()
})
</script>

<style scoped>
.settings-layout-loading {
  height: 100%;
  display: grid;
  place-items: center;
}
</style>
