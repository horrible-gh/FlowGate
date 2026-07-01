<template>
  <teleport to="body">
    <div
      v-if="visible"
      ref="overlayRef"
      class="modal-bg"
      tabindex="-1"
      @keydown.escape.prevent="onClose"
    >
      <div class="modal-box modal-tmd" role="dialog" aria-modal="true" aria-labelledby="tmd-title">

        <!-- Header -->
        <div class="modal-hd">
          <div class="modal-title" id="tmd-title">
            <i class="fa-solid fa-clock-rotate-left" style="color:var(--warning, #d97706); margin-right:6px;"></i>{{ t('main.time_machine.title') }}
          </div>
          <button type="button" class="modal-close" @click="onClose">
            <i class="fa-solid fa-xmark"></i>
          </button>
        </div>

        <!-- Body -->
        <div class="modal-bd tmd-body">
          <p class="tmd-desc">{{ t('main.time_machine.desc') }}</p>

          <div v-if="loading" class="tmd-loading">
            <i class="fa-solid fa-spinner fa-spin"></i> {{ t('main.time_machine.loading') }}
          </div>

          <div v-else-if="steps.length === 0" class="tmd-empty">
            {{ t('main.time_machine.empty') }}
          </div>

          <ul v-else class="tmd-step-list">
            <li
              v-for="step in steps"
              :key="step.docId"
              class="tmd-step"
              :class="{ 'tmd-step--selected': selectedDocId === step.docId }"
              @click="selectedDocId = step.docId"
            >
              <span class="tmd-step-radio">
                <i :class="selectedDocId === step.docId ? 'fa-solid fa-circle-dot' : 'fa-regular fa-circle'"></i>
              </span>
              <span class="tmd-step-type">{{ typeLabel(step.typeCode) }}</span>
              <span class="tmd-step-title">{{ step.title || step.docId }}</span>
            </li>
          </ul>

          <p v-if="selectedStep" class="tmd-cascade-note">
            <i class="fa-solid fa-triangle-exclamation"></i>
            {{ t('main.time_machine.cascade_note', { step: typeLabel(selectedStep.typeCode) }) }}
          </p>
        </div>

        <!-- Footer -->
        <div class="modal-ft tmd-footer">
          <button type="button" class="btn btn-outline btn-sm" @click="onClose">
            {{ t('common.cancel') }}
          </button>
          <button
            type="button"
            class="btn btn-warning btn-sm"
            :disabled="!selectedStep || submitting"
            @click="onConfirm"
          >
            <template v-if="submitting">
              <i class="fa-solid fa-spinner fa-spin"></i> {{ t('main.time_machine.reopening') }}
            </template>
            <template v-else>
              <i class="fa-solid fa-clock-rotate-left"></i> {{ t('main.time_machine.confirm') }}
            </template>
          </button>
        </div>
      </div>
    </div>
  </teleport>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useDocTypeStore } from '../stores/docTypeStore'

interface TimeMachineStep {
  docId: string
  seq: number
  typeCode: string | null
  title: string | null
}

const props = defineProps<{
  visible: boolean
  steps: TimeMachineStep[]
  loading?: boolean
  /** 0018 R0001 — when opened from a workflow-strip step click, pre-select that step's
   *  document so the confirm targets the clicked step (still changeable in the picker). */
  preselectDocId?: string | null
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
  'confirm': [step: TimeMachineStep]
}>()

const { t } = useI18n()
const docTypeStore = useDocTypeStore()
const overlayRef = ref<HTMLElement | null>(null)
const selectedDocId = ref<string | null>(null)
const submitting = ref(false)

const selectedStep = computed(() => props.steps.find(s => s.docId === selectedDocId.value) ?? null)

function typeLabel(typeCode: string | null): string {
  return typeCode ? docTypeStore.getLabel(typeCode) : ''
}

watch(
  () => props.visible,
  (v) => {
    if (v) {
      // 0018 R0001 — honour a strip-click pre-selection; AC-reject opens with none (null).
      selectedDocId.value = props.preselectDocId ?? null
      submitting.value = false
      nextTick(() => overlayRef.value?.focus())
    }
  },
)

function onClose() {
  emit('update:visible', false)
}

function onConfirm() {
  if (!selectedStep.value || submitting.value) return
  submitting.value = true
  emit('confirm', selectedStep.value)
}
</script>

<style scoped>
.modal-bg {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1200;
}

.modal-box {
  background: var(--bg-card, #fff);
  border-radius: 10px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.18);
  display: flex;
  flex-direction: column;
  max-height: 90vh;
  overflow: hidden;
}

.modal-tmd {
  width: 480px;
}

.modal-hd {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  border-bottom: 1px solid var(--border, #e2e8f0);
}

.modal-title {
  font-size: 1rem;
  font-weight: 600;
  color: var(--text, #1e293b);
}

.modal-close {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 1rem;
  color: var(--text-m, #64748b);
  padding: 4px 6px;
  border-radius: 4px;
  transition: background 0.1s;
}
.modal-close:hover {
  background: var(--bg-hover, #f1f5f9);
}

.modal-bd {
  padding: 20px;
  overflow-y: auto;
}

.tmd-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.tmd-desc {
  font-size: 0.875rem;
  color: var(--text-m, #64748b);
  margin: 0;
  line-height: 1.5;
}

.tmd-loading,
.tmd-empty {
  font-size: 0.875rem;
  color: var(--text-m, #64748b);
  padding: 16px 0;
  text-align: center;
}

.tmd-step-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.tmd-step {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 6px;
  cursor: pointer;
  transition: border-color 0.1s, background 0.1s;
}
.tmd-step:hover {
  background: var(--bg-hover, #f1f5f9);
}
.tmd-step--selected {
  border-color: var(--primary, #2563eb);
  background: var(--bg-sub, #f8fafc);
}

.tmd-step-radio {
  color: var(--primary, #2563eb);
  flex-shrink: 0;
}

.tmd-step-type {
  font-weight: 600;
  font-size: 0.8125rem;
  color: var(--text, #1e293b);
  flex-shrink: 0;
}

.tmd-step-title {
  font-size: 0.8125rem;
  color: var(--text-m, #64748b);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.tmd-cascade-note {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  font-size: 0.8125rem;
  color: var(--warning, #d97706);
  background: var(--bg-sub, #fffbeb);
  border-radius: 6px;
  padding: 10px 12px;
  margin: 0;
  line-height: 1.45;
}

.modal-ft {
  padding: 14px 20px;
  border-top: 1px solid var(--border, #e2e8f0);
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
}
</style>
