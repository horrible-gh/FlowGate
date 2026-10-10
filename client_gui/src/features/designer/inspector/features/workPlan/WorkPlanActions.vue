<template>
  <!-- flowgate.v02.0051 stage 2 — the 작업계획 card's header actions (0035 v7 plan.m.*: ⇉ 전개 on a plan
       not deployed yet, ⇉ 재전개 ▾ on a plan revised after a deployment). Deploying a plan onto the
       Canvas has no Inspector API (0048 D#1 §8.4: WP 전개), so both are shown disabled with that reason
       (0051-R §2.3) — never a working-looking button. A thin container over workPlanModel; nothing is
       sent. -->
  <div class="inspector-work-plan-actions" role="group" :aria-label="t(k('deploy.label'))" data-test="inspector-work-plan-actions">
    <Button
      v-for="operation in WORK_PLAN_DEPLOY_OPERATIONS"
      :key="operation"
      size="sm"
      variant="primary"
      :disabled="!actions[operation].enabled"
      :title="actions[operation].reason ? t(k(actions[operation].reason!)) : undefined"
      :data-test="`work-plan-${operation}`"
    >{{ t(k(`deploy.${operation}`)) }}</Button>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '../../../../../components/ui/Button.vue'
import type { InspectorTarget } from '../../inspectorTarget'
import { WORK_PLAN_DEPLOY_OPERATIONS, workPlanDeployActions } from './workPlanModel'

const props = defineProps<{
  target: InspectorTarget
}>()

const { t } = useI18n()

const k = (key: string) => `gui.inspector.workPlan.${key}`

const actions = computed(() => workPlanDeployActions({ hasDocument: props.target.docId !== null }))
</script>

<style scoped>
.inspector-work-plan-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
</style>
