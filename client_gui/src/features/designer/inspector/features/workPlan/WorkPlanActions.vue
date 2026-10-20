<template>
  <!-- flowgate.v02.0051 stage 2 — the 작업계획 card's header actions (0035 v7 plan.m.*: ⇉ 전개 on a plan
       not deployed yet, ⇉ 재전개 ▾ on a deployed plan).
       flowgate.v02.0074 T#5 (D#1 §6.1) — they deploy the plan onto the Flow's Graph draft (T#4 API). Two icon buttons
       of the ⋯ button's kind (IconButton sm, secondary) with no text label: [전개], and [재전개 ▾] opening its two items
       「추가분 전개」 · 「정리 포함 재전개」 (each with its own state and tooltip). The tooltips name the operation and, when
       it is off, why. Only 「정리 포함 재전개」 asks once (ModalShell sm: how many cards go, how many stay) and then runs
       the preview the dialog shows. A refusal, a blocker or how the deploy went is one InspectorReason line. A thin
       container over useWorkPlanDeploy / workPlanModel. -->
  <div
    class="inspector-work-plan-actions"
    role="group"
    :aria-label="t(k('deploy.label'))"
    data-test="inspector-work-plan-actions"
    :data-busy="busy ? 'true' : ''"
  >
    <IconButton
      size="sm"
      variant="secondary"
      class="inspector-work-plan-button"
      :label="t(k('deploy.deploy'))"
      :title="tip('deploy')"
      :disabled="!actions.deploy.enabled"
      :aria-busy="busy ? 'true' : undefined"
      data-test="work-plan-deploy"
      @click="run('deploy')"
    ><span aria-hidden="true">⇉</span></IconButton>
    <Dropdown>
      <template #trigger="{ toggle, triggerAttrs }">
        <IconButton
          v-bind="triggerAttrs"
          size="sm"
          variant="secondary"
          class="inspector-work-plan-button"
          :label="t(k('deploy.redeploy'))"
          :title="tip('redeploy')"
          :disabled="!actions.redeploy.enabled"
          :aria-busy="busy ? 'true' : undefined"
          data-test="work-plan-redeploy"
          @click="toggle"
        ><span aria-hidden="true">⇉</span><span class="inspector-work-plan-caret" aria-hidden="true">▾</span></IconButton>
      </template>
      <template #default="{ close }">
        <button
          v-for="operation in WORK_PLAN_REDEPLOY_ITEMS"
          :key="operation"
          type="button"
          role="menuitem"
          class="inspector-header-menu-item"
          :disabled="!actions[operation].enabled"
          :title="tip(operation)"
          :data-test="`work-plan-redeploy-${operation}`"
          @click="pick(operation, close)"
        >{{ t(k(`deploy.${operation}`)) }}</button>
      </template>
    </Dropdown>
    <span v-if="shownLine" class="inspector-work-plan-actions-line" :title="shownLine.text" data-test="work-plan-deploy-line" :data-kind="shownLine.kind">
      <InspectorReason :text="shownLine.text" :tone="shownLine.tone" />
    </span>

    <ModalShell :open="pendingCleanup !== null" size="sm" :aria-label="t(k('cleanup_dialog.title'))" @close="cancelCleanup">
      <template #header>
        <h3 class="inspector-work-plan-dialog-title">{{ t(k('cleanup_dialog.title')) }}</h3>
      </template>
      <p class="inspector-work-plan-dialog-text" data-test="work-plan-cleanup-dialog">
        {{ t(k('cleanup_dialog.counts'), { remove: cleanupCounts?.remove ?? 0, keep: cleanupCounts?.keep ?? 0 }) }}
      </p>
      <template #footer>
        <div class="inspector-work-plan-dialog-footer">
          <Button size="sm" variant="ghost" :disabled="busy" data-test="work-plan-cleanup-cancel" @click="cancelCleanup">{{ t(k('cleanup_dialog.cancel')) }}</Button>
          <Button size="sm" variant="primary" :loading="busy" data-test="work-plan-cleanup-confirm" @click="confirmCleanup">{{ t(k('cleanup_dialog.submit')) }}</Button>
        </div>
      </template>
    </ModalShell>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '../../../../../components/ui/Button.vue'
import Dropdown from '../../../../../components/ui/Dropdown.vue'
import IconButton from '../../../../../components/ui/IconButton.vue'
import ModalShell from '../../../../../components/ui/ModalShell.vue'
import InspectorReason from '../../parts/InspectorReason.vue'
import type { InspectorTarget } from '../../inspectorTarget'
import { useWorkPlanDeploy, type WorkPlanDeployRunnable } from './useWorkPlanDeploy'
import { WORK_PLAN_REDEPLOY_ITEMS, type WorkPlanDeployOperation } from './workPlanModel'

const props = defineProps<{
  target: InspectorTarget
}>()

const { t } = useI18n()

const k = (key: string) => `gui.inspector.workPlan.${key}`

const { actions, busy, line, pendingCleanup, cleanupCounts, run, confirmCleanup, cancelCleanup } = useWorkPlanDeploy(() => props.target)

/** The operation's name, and why it is off (the busy state is the buttons' aria-busy). */
function tip(operation: WorkPlanDeployOperation): string {
  const name = t(k(`deploy.${operation}`))
  const reason = actions.value[operation].reason
  return reason && reason !== 'reason.busy' ? `${name} — ${t(k(reason))}` : name
}

function pick(operation: 'add' | 'cleanup', close: () => void): void {
  close()
  void run(operation as WorkPlanDeployRunnable)
}

const shownLine = computed(() => {
  const current = line.value
  return current ? { kind: current.kind, tone: current.tone, text: t(k(current.key), { ...(current.params ?? {}) }) } : null
})
</script>

<style scoped>
/* The same row as the ⋯ button: 28px squares, the header actions' 6px gap. */
.inspector-work-plan-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.inspector-work-plan-button {
  font-size: 14px;
  line-height: 1;
}

.inspector-work-plan-caret {
  margin-left: 1px;
  font-size: 9px;
}

.inspector-work-plan-actions-line {
  display: block;
  max-width: 320px;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
}

.inspector-work-plan-actions-line :deep(.inspector-reason) {
  margin: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* The ⋯ menu's items (InspectorHeader .inspector-header-menu-item; teleported with the Dropdown, this scope applies). */
.inspector-header-menu-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  height: 30px;
  padding: 0 10px;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: #c3d3e3;
  font-size: 12px;
  text-align: left;
  cursor: pointer;
}

.inspector-header-menu-item:hover:not(:disabled) {
  background: #15345a;
}

.inspector-header-menu-item:disabled {
  color: #5d738b;
  cursor: default;
}

.inspector-work-plan-dialog-title {
  margin: 0;
  padding: 16px 20px 0;
  font-size: 14px;
  font-weight: 700;
}

.inspector-work-plan-dialog-text {
  margin: 0;
  font-size: 13px;
}

.inspector-work-plan-dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 0 20px 16px;
}
</style>
