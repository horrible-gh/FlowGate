<template>
  <!-- flowgate.v02.0072 T#1 — every open work plan edit window of this tab (mounted once, by DocumentWindows). -->
  <WorkPlanEditWindow v-for="win in windows" :key="`work-plan:${win.docId}`" :win="win" />
</template>

<script setup lang="ts">
import WorkPlanEditWindow from './WorkPlanEditWindow.vue'
import { workPlanEditWindowList } from './workPlanEditWindows'

const windows = workPlanEditWindowList()
</script>
