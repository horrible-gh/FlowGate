<template>
  <div v-if="stepStates.length > 0 || (isWorkflowRoot && workflowDecided === false)" class="wf-section">
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
      <!-- Normal: v-for over stepStates -->
      <template v-else>
        <div v-for="(s, idx) in stepStates" :key="s.code + idx" class="wf-unit">
          <div
            class="wf-step"
            :class="[s.className, s.visual === 'current' && canNextAction ? 'wf-current-clickable' : '']"
            @click="s.visual === 'current' && canNextAction ? emit('next-action') : undefined"
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
}>()

const { t } = useI18n()
const docTypeStore = useDocTypeStore()
const isWorkflowRoot = computed(() => props.tab.typeCode === 'R' || props.tab.typeCode === 'B')

const emit = defineEmits<{
  'sequence-updated': []
  'next-action': []
}>()

const showEditModal = ref(false)
</script>

<style scoped>
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
