<template>
  <!-- flowgate.v02.0072 T#1 — every open work plan edit window of this tab (mounted once, by DocumentWindows). -->
  <WorkPlanEditWindow v-for="win in windows" :key="`work-plan:${win.docId}`" :win="win" />
  <WorkPlanStepEditWindow v-for="win in steps" :key="win.id" :win="win" />
</template>

<script setup lang="ts">
import { onBeforeUnmount } from 'vue'
import WorkPlanStepEditWindow from './WorkPlanStepEditWindow.vue'
import { installWorkPlanStepEditor, closeWorkPlanStepWindows, workPlanStepWindowList } from './workPlanStepEditWindows'
import WorkPlanEditWindow from './WorkPlanEditWindow.vue'
import { workPlanEditWindowList } from './workPlanEditWindows'

const windows = workPlanEditWindowList()
const steps = workPlanStepWindowList()
const unregister = installWorkPlanStepEditor()
onBeforeUnmount(() => { closeWorkPlanStepWindows(); unregister() })
</script>
