<template>
  <!-- flowgate.v02.0072 T#1 — every open work plan edit window of this tab (mounted once, by DocumentWindows). -->
  <WorkPlanEditWindow v-for="win in windows" :key="`work-plan:${win.docId}`" :win="win" />
  <WorkPlanStepEditWindow v-for="win in steps" :key="win.id" :win="win" />
</template>

<script setup lang="ts">
// flowgate.v02.0072 T#3 — opened from the WP card's 「작업계획」 line (the Inspector's openDocument.ts). Signing out
// (the signed-in user gone) closes every edit window and its Step windows without asking: their edits belong to the
// user who left (the v0.2 sign-out is a full page move to /login, which ends this module state as well).
import { onBeforeUnmount, watch } from 'vue'
import { useCurrentUser } from '@/core/auth/currentUser'
import WorkPlanStepEditWindow from './WorkPlanStepEditWindow.vue'
import { installWorkPlanStepEditor, closeWorkPlanStepWindows, workPlanStepWindowList } from './workPlanStepEditWindows'
import WorkPlanEditWindow from './WorkPlanEditWindow.vue'
import { closeAllWorkPlanEditWindows, workPlanEditWindowList } from './workPlanEditWindows'

const windows = workPlanEditWindowList()
const steps = workPlanStepWindowList()
const unregister = installWorkPlanStepEditor()
const { user } = useCurrentUser()
watch(
  () => user.value,
  (now, before) => {
    if (before && !now) closeAllWorkPlanEditWindows()
  },
)
onBeforeUnmount(() => { closeWorkPlanStepWindows(); unregister() })
</script>
