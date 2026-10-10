<template>
  <!-- flowgate.v02.0048 T#13 — the WP summary of the documents tab (D#1 §10.2 T#13 row): the plan's revision,
       totals, progress (cards the workflow started), provider assignment and one row per card, with ↗ 열기 to
       edit it in the v0.1 WorkPlanEditor. A thin container: useWorkPlan reads, workPlanModel summarizes. -->
  <section class="inspector-work-plan" data-test="inspector-work-plan" :data-status="status">
    <div class="inspector-work-plan-line">
      <h4 class="inspector-work-plan-heading">{{ t(k('title')) }}</h4>
      <span v-if="view" class="inspector-work-plan-rev" data-test="work-plan-rev">{{ t(k('rev'), { rev: view.revisionNo }) }}</span>
      <span v-else-if="unreadableRevision !== null" class="inspector-work-plan-rev" data-test="work-plan-rev">{{
        t(k('rev'), { rev: unreadableRevision })
      }}</span>
    </div>

    <InspectorPaneState v-if="status === 'loading' && !view" state="loading" />
    <InspectorPaneState v-else-if="errorKey && !view" state="error" :message="t(k(errorKey))" retryable @retry="reload" />
    <template v-else-if="view">
      <InspectorPaneState v-if="errorKey" state="error" :message="t(k(errorKey))" retryable @retry="reload" />
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
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '../../../../../components/ui/Button.vue'
import InspectorPaneState from '../../parts/InspectorPaneState.vue'
import type { InspectorTarget } from '../../inspectorTarget'
import type { WorkPlanCardRow } from './workPlanModel'
import { useWorkPlan } from './useWorkPlan'

const props = defineProps<{
  target: InspectorTarget
}>()

const { t } = useI18n()

const k = (key: string) => `gui.inspector.workPlan.${key}`

const { view, cards, progress, editKey, availability, status, errorKey, unreadableRevision, open, reload } = useWorkPlan(() => props.target)

const assignmentList = computed(() =>
  (view.value?.assignments ?? []).map((entry) => t(k('assignment'), { name: entry.name, count: entry.stepCount })).join(' · '),
)

function providerText(card: WorkPlanCardRow): string {
  if (card.providers.length) return card.providers.join(' / ')
  return t(k(card.locked ? 'provider_server' : 'provider_none'))
}
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
