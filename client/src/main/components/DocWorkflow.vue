<template>
  <div v-if="stepStates.length > 0 || (isWorkflowRoot && (workflowDecided === false || decidedEmpty))" class="wf-section">
    <div class="sec-title">
      <i class="fa-solid fa-diagram-next"></i> {{ t('main.doc_workflow.title') }}
      <button
        v-if="workflowDecided"
        type="button"
        class="wf-edit-btn"
        @click="showEditModal = true"
      >
        <i class="fa-solid fa-pen-to-square"></i>
        {{ t('main.doc_workflow.edit_btn') }}
      </button>
    </div>
    <div class="wf-flow">
      <!-- Workflow root undecided → placeholder -->
      <template v-if="isWorkflowRoot && workflowDecided === false">
        <div class="wf-unit">
          <div class="wf-step wf-undecided current">
            <i class="fa-solid fa-circle-question"></i>
            <span class="s-lbl">{{ docTypeStore.getLabel(tab.typeCode ?? 'R') }}</span>
          </div>
          <span class="wf-arrow"><i class="fa-solid fa-chevron-right"></i></span>
        </div>
        <div class="wf-unit">
          <div class="wf-step wf-undecided">
            <i class="fa-solid fa-circle-question"></i>
            <span class="s-lbl">{{ t('main.doc_workflow.undecided') }}</span>
          </div>
        </div>
      </template>
      <!-- 0119 B0001 (NR0003 §6-B): a decided workflow whose every step was deleted
           (decided-but-empty). The normal strip would be blank and the section was
           previously hidden entirely — stranding the [Edit] affordance and leaving the
           workflow unrecoverable. Show a recovery hint; the [Edit] button above re-adds
           steps (edit_workflow_pending inserts pending items into the existing sequence). -->
      <template v-else-if="decidedEmpty">
        <div class="wf-empty-recover">
          <i class="fa-solid fa-circle-exclamation"></i>
          <span>{{ t('main.doc_workflow.decided_empty') }}</span>
        </div>
      </template>
      <!-- Normal: v-for over stepStates -->
      <template v-else>
        <div v-for="(s, idx) in stepStates" :key="s.code + idx" class="wf-unit">
          <div
            class="wf-step"
            :class="[
              s.className,
              (s.visual === 'highlight' || s.visual === 'current') && canNextAction ? 'wf-current-clickable' : '',
              s.visual === 'done' ? 'wf-done-clickable' : '',
              isReturnTarget(idx) ? 'wf-return-clickable' : '',
            ]"
            :title="stepHint(s, idx)"
            @click="onStepClick(s, idx)"
          >
            <i :class="s.iconClass"></i>
            <span class="s-lbl">{{ docTypeStore.getLabel(s.code) }}</span>
          </div>
          <span v-if="idx < stepStates.length - 1" class="wf-arrow">
            <i class="fa-solid fa-chevron-right"></i>
          </span>
        </div>
      </template>
    </div>
  </div>

  <WorkflowDecisionModal
    mode="edit"
    :visible="showEditModal"
    :doc-id="parentRDocId ?? tab.id"
    @update:visible="showEditModal = $event"
    @saved="emit('sequence-updated')"
  />
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import type { Tab } from '../stores/tabs'
import type { StepState } from '../workflow/workflowViewState'
import { useDocTypeStore } from '../stores/docTypeStore'
import WorkflowDecisionModal from './WorkflowDecisionModal.vue'

const props = defineProps<{
  tab: Tab
  workflowDecided?: boolean
  parentRDocId?: string | null
  stepStates: StepState[]
  /** workflowViewState output: whether "proceed to next step" action is available (enables click). */
  canNextAction?: boolean
  /** 0142 R0001 — reverse time-machine: strip indices that are "return targets". These are the
   *  rewound steps sitting AHEAD of the current head; hovering makes them clickable to roll the
   *  workflow forward (restore) to that step. Empty/absent when no active return point. */
  returnTargets?: number[]
}>()

const { t } = useI18n()
const docTypeStore = useDocTypeStore()
const isWorkflowRoot = computed(() => props.tab.typeCode === 'R' || props.tab.typeCode === 'B')

// 0119 B0001 (NR0003 §6-B): decided workflow root whose steps were all deleted. Used to
// keep the section + [Edit] button visible (recovery) instead of collapsing to nothing.
const decidedEmpty = computed(() =>
  isWorkflowRoot.value && props.workflowDecided === true && props.stepStates.length === 0,
)

const emit = defineEmits<{
  'sequence-updated': []
  'next-action': []
  // 0018 R0001 — workflow-strip time-machine: a completed ('done') step cell was clicked.
  // Emits the strip index + step type code so the parent can resolve the slot's realised
  // document (by slot identity) and reopen the workflow there.
  'time-machine': [payload: { index: number; code: string }]
  // 0142 R0001 — reverse time-machine: a return-target cell (a rewound step ahead of the head)
  // was clicked. Emits the same index+code so the parent restores the workflow forward to that
  // step and navigates there. Mirror of 'time-machine' in the opposite direction.
  'return-to': [payload: { index: number; code: string }]
}>()

// 0142 R0001 — a strip cell is a return target when the parent lists its index in returnTargets.
function isReturnTarget(idx: number): boolean {
  return props.returnTargets?.includes(idx) ?? false
}

// Hover tooltip: a rewound step ahead of the head hints "return here"; a completed step behind
// the head hints "roll back here". (A cell is only ever one of the two.)
function stepHint(s: StepState, idx: number): string | undefined {
  if (isReturnTarget(idx)) return t('main.doc_workflow.time_machine_return_hint')
  if (s.visual === 'done') return t('main.doc_workflow.time_machine_hint')
  return undefined
}

// 0018 R0001 / 0142 R0001 — head step keeps its "proceed to next step" action; a completed
// (done) step behind the head opens the time-machine (roll back); a return-target step ahead of
// the head restores forward (reverse time-machine); other future steps are inert.
function onStepClick(s: StepState, idx: number) {
  if ((s.visual === 'highlight' || s.visual === 'current') && props.canNextAction) {
    emit('next-action')
    return
  }
  if (isReturnTarget(idx)) {
    emit('return-to', { index: idx, code: s.code })
    return
  }
  if (s.visual === 'done') {
    emit('time-machine', { index: idx, code: s.code })
  }
}

const showEditModal = ref(false)
</script>

<style scoped>
/* 0119 B0001: decided-but-empty recovery hint */
.wf-empty-recover {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  font-size: .78rem;
  color: #92400e;
  background: var(--warning-l, #fffbeb);
  border: 1px dashed #fde68a;
  border-radius: var(--r, 8px);
}
.wf-empty-recover i {
  color: var(--warning, #d97706);
  flex-shrink: 0;
}

.wf-step.wf-undecided {
  border: 2px dashed var(--border-d, #94a3b8);
  opacity: 1;
  color: var(--text-m);
  background: none;
}
.wf-step.wf-undecided i {
  color: var(--text-m);
}

/* Current undecided step (requirements definition); blue emphasis. The "undecided" placeholder
   (wf-undecided without .current) stays gray */
.wf-step.wf-undecided.current {
  border-color: var(--primary);
  background: var(--primary-l);
  color: var(--primary);
  box-shadow: 0 0 0 2px rgba(37, 99, 235, .15);
}
.wf-step.wf-undecided.current i {
  color: var(--primary);
}
.wf-step.wf-undecided.current .s-lbl {
  font-weight: 700;
}

.wf-step.wf-current-clickable {
  cursor: pointer;
}
.wf-step.wf-current-clickable:hover {
  box-shadow: 0 0 0 3px rgba(37, 99, 235, .3);
}

/* 0018 R0001 — completed step is clickable to time-travel (roll the workflow back to it).
   Warning-tinted focus ring on hover signals the destructive (cascade) rollback intent. */
.wf-step.wf-done-clickable {
  cursor: pointer;
}
.wf-step.wf-done-clickable:hover {
  box-shadow: 0 0 0 3px rgba(217, 119, 6, .3);
}

/* 0142 R0001 — reverse time-machine: a rewound step AHEAD of the head is clickable to return
   forward (restore) to it. It renders as a normal (grey/future) cell until hovered; the green
   focus ring + forward cursor signal the non-destructive "go back to where I was" intent,
   deliberately mirroring the amber backward ring above so the two directions read as one strip. */
.wf-step.wf-return-clickable {
  cursor: pointer;
  border-style: dashed;
  border-color: var(--success, #16a34a);
  color: var(--success, #16a34a);
  opacity: 1;
}
.wf-step.wf-return-clickable i {
  color: var(--success, #16a34a);
}
.wf-step.wf-return-clickable:hover {
  background: var(--success-l, #f0fdf4);
  box-shadow: 0 0 0 3px rgba(22, 163, 74, .3);
}

.wf-edit-btn {
  order: 2;        /* render after ::after decorative line (order: 1) */
  margin-left: 0;  /* ::after flex:1 fills the gap; no auto-margin needed */
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 10px;
  font-size: .72rem;
  font-weight: 600;
  color: #0284c7;
  background: #e0f2fe;
  border: 1px solid #bae6fd;
  border-radius: 6px;
  cursor: pointer;
  transition: background .15s, border-color .15s;
}
.wf-edit-btn:hover {
  background: #bae6fd;
  border-color: #7dd3fc;
}

.sec-title {
  display: flex;
  align-items: center;
}
/* global .sec-title::after has flex:1 which consumed all free space before
   margin-left:auto could take effect, pushing the button against the title text.
   Reorder so the decorative line (::after) sits between the title and the button. */
.sec-title::after {
  order: 1;
}
</style>
