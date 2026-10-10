<template>
  <!-- flowgate.v02.0051 stage 2 — the 작업계획 card's header meta (0035 v7 plan card: "작성 사람 / AI").
       The AI-written plan (Supervisor) does not exist in 0051 (R §3.3), so every plan on the Canvas is a
       person's: the badge says 작성 사람 with that as its hover text. Display only. -->
  <InspectorOriginBadge origin="wp" :label="t(k('author.human'))" :title="t(k('author.human_title'))" data-test="work-plan-author" />
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { InspectorTarget } from '../../inspectorTarget'
import InspectorOriginBadge from '../../parts/InspectorOriginBadge.vue'

defineProps<{
  target: InspectorTarget
}>()

const { t } = useI18n()

const k = (key: string) => `gui.inspector.workPlan.${key}`
</script>
