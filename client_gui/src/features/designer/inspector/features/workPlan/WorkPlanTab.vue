<template>
  <!-- flowgate.v02.0048 T#13 — the WP summary of the documents tab (D#1 §10.2 T#13 row): the plan's revision,
       totals, progress (cards the workflow started), provider assignment and one row per card, with ↗ 열기 to
       edit it in the v0.1 WorkPlanEditor. A thin container: useWorkPlan reads, workPlanModel summarizes.
       flowgate.v02.0051 stage 2 — the v7 작업계획 card (0035 plan.m.*) on top: the 작업계획 (WP) slot line
       (state pill · rev n · Step n · ↗) and the 전개 result line (empty "전개 전": no deploy API), built from
       the common slot list part; the 0048 Step summary stays below as reused content. A Node without a
       document shows the two lines empty and reads nothing. -->
  <section class="inspector-work-plan" data-test="inspector-work-plan" :data-status="status" :data-has-document="hasDocument ? 'true' : 'false'">
    <InspectorSlotList :slots="lines" @open="open" />

    <template v-if="hasDocument">
      <span v-if="!view && unreadableRevision !== null" class="inspector-work-plan-rev" data-test="work-plan-rev">{{ t(k('rev'), { rev: unreadableRevision }) }}</span>
      <InspectorPaneState v-if="status === 'loading' && !view" state="loading" />
      <InspectorPaneState v-else-if="errorKey && !view" state="error" :message="t(k(errorKey))" retryable @retry="reload" />
      <template v-else-if="view">
        <InspectorPaneState v-if="errorKey" state="error" :message="t(k(errorKey))" retryable @retry="reload" />
        <div class="inspector-work-plan-line">
          <h4 class="inspector-work-plan-heading">{{ t(k('steps_title')) }}</h4>
          <span class="inspector-work-plan-rev" data-test="work-plan-rev">{{ t(k('rev'), { rev: view.revisionNo }) }}</span>
        </div>
        <p class="inspector-work-plan-sub" data-test="work-plan-totals">{{ t(k('totals'), { ...view.totals }) }}</p>
        <p class="inspector-work-plan-sub" data-test="work-plan-progress" :data-started="progress.started" :data-cards="progress.cards">
          {{ t(k('progress'), { ...progress }) }}
        </p>
        <p v-if="view.assignments.length" class="inspector-work-plan-sub" data-test="work-plan-assignments">
          {{ t(k('assignments'), { list: assignmentList }) }}
        </p>
        <p v-if="view.unassignedSteps" class="inspector-work-plan-sub" data-test="work-plan-unassigned">
          {{ t(k('unassigned'), { count: view.unassignedSteps }) }}
        </p>

        <p v-if="!cards.length" class="inspector-work-plan-sub" data-test="work-plan-empty">{{ t(k('empty')) }}</p>
        <ul v-else class="inspector-work-plan-rows">
          <li
            v-for="card in cards"
            :key="card.cardId"
            class="inspector-work-plan-row"
            data-test="work-plan-card"
            :data-card="card.cardId"
            :data-started="card.started"
          >
            <span class="inspector-work-plan-keys">{{ card.keys.join(' → ') }}</span>
            <span class="inspector-work-plan-sub" data-test="work-plan-card-provider">{{ providerText(card) }}</span>
            <span v-if="card.reviewCount" class="inspector-work-plan-sub" data-test="work-plan-card-review">{{
              t(k('review'), { count: card.reviewCount })
            }}</span>
            <span v-if="card.started" class="inspector-work-plan-started" data-test="work-plan-card-started">{{ t(k('started')) }}</span>
          </li>
        </ul>
      </template>

      <div class="inspector-work-plan-line">
        <span v-if="editKey" class="inspector-work-plan-sub" data-test="work-plan-edit">{{ t(k(editKey)) }}</span>
        <Button
          v-if="availability.open.visible"
          size="sm"
          variant="ghost"
          :disabled="!availability.open.enabled"
          :title="t(k('open_hint'))"
          data-test="work-plan-open"
          @click="open"
        >{{ t(k('open')) }}</Button>
      </div>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '../../../../../components/ui/Button.vue'
import InspectorPaneState from '../../parts/InspectorPaneState.vue'
import InspectorSlotList, { type InspectorSlotView } from '../../parts/InspectorSlotList.vue'
import type { InspectorTarget } from '../../inspectorTarget'
import { workPlanSlotFocus, workPlanSlotState, type WorkPlanCardRow } from './workPlanModel'
import { useWorkPlan } from './useWorkPlan'

const props = defineProps<{
  target: InspectorTarget
}>()

const { t, te } = useI18n()

const k = (key: string) => `gui.inspector.workPlan.${key}`

const { view, cards, progress, editKey, availability, status, errorKey, unreadableRevision, open, reload } = useWorkPlan(() => props.target)

const hasDocument = computed(() => props.target.docId !== null)

const assignmentList = computed(() =>
  (view.value?.assignments ?? []).map((entry) => t(k('assignment'), { name: entry.name, count: entry.stepCount })).join(' · '),
)

function providerText(card: WorkPlanCardRow): string {
  if (card.providers.length) return card.providers.join(' / ')
  return t(k(card.locked ? 'provider_server' : 'provider_none'))
}

/** The v7 lines: the plan document and its deployment result. */
const lines = computed<InspectorSlotView[]>(() => {
  const target = props.target
  const state = workPlanSlotState(target.docReviewStatus, target.docStatus)
  const stateKey = state ? `gui.designer.inspector.${state.key}` : ''
  const revision = view.value?.revisionNo ?? unreadableRevision.value
  const plan: InspectorSlotView =
    target.docId !== null
      ? {
          key: 'plan',
          name: t(k('slot.plan')),
          id: target.docId,
          state: state ? { label: te(stateKey) ? t(stateKey) : state.raw, tone: state.tone } : null,
          summary: view.value
            ? t(k('slot_summary'), { rev: view.value.revisionNo, steps: view.value.totals.steps })
            : revision !== null
              ? t(k('rev'), { rev: revision })
              : null,
          focus: workPlanSlotFocus(state),
          danger: state?.tone === 'danger',
          open: availability.value.open.enabled ? 'enabled' : 'disabled',
        }
      : { key: 'plan', name: t(k('slot.plan')), empty: true, emptyText: t(k('slot_empty.plan')), write: 'disabled', writeReason: t(k('write_unconnected')) }
  const deploy: InspectorSlotView = { key: 'deploy', kind: 'result', name: t(k('slot.deploy')), empty: true, emptyText: t(k('slot_empty.deploy')) }
  return [plan, deploy]
})
</script>

<style scoped>
.inspector-work-plan {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--fg2-line);
  font-size: 12px;
}

.inspector-work-plan-line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.inspector-work-plan-heading {
  margin: 0;
  font-size: 11.5px;
  font-weight: 600;
  color: var(--fg2-muted);
}

.inspector-work-plan-rev {
  font-weight: 600;
}

.inspector-work-plan-sub {
  margin: 0;
  color: var(--fg2-muted);
  font-size: 11.5px;
}

.inspector-work-plan-rows {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.inspector-work-plan-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  min-width: 0;
}

.inspector-work-plan-keys {
  font-weight: 600;
}

.inspector-work-plan-started {
  color: var(--fg2-status-info-fg, inherit);
}
</style>
