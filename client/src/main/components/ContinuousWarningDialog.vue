<template>
  <teleport to="body">
    <div v-if="visible" class="modal-bg" @click.self="close">
      <div class="modal-box modal-cwarn">
        <!-- ── Header ── -->
        <div class="modal-hd">
          <span class="modal-title">
            <i class="fa-solid fa-triangle-exclamation" style="color:var(--danger); margin-right:6px;"></i>
            {{ t('main.continuous_work.warn_title') }}
          </span>
          <button class="modal-close" type="button" @click="close">
            <i class="fa-solid fa-xmark"></i>
          </button>
        </div>

        <!-- ── Body ── -->
        <div class="modal-bd cwarn-body">
          <div class="cwarn-summary">
            <i class="fa-solid fa-forward-fast"></i>
            {{ summaryText }}
          </div>

          <ul class="cwarn-list">
            <li>{{ t('main.continuous_work.warn_unmanned') }}</li>
            <li>{{ t('main.continuous_work.warn_responsibility') }}</li>
            <li>{{ t('main.continuous_work.warn_quality') }}</li>
          </ul>

          <label class="cwarn-consent">
            <input v-model="consented" type="checkbox" />
            <span>{{ t('main.continuous_work.warn_consent') }}</span>
          </label>
        </div>

        <!-- ── Footer ── -->
        <div class="modal-ft">
          <button type="button" class="btn btn-ghost" @click="close">{{ t('common.cancel') }}</button>
          <button
            type="button"
            class="btn btn-danger"
            :disabled="!consented"
            @click="onConfirm"
          >
            <i class="fa-solid fa-bolt"></i> {{ t('main.continuous_work.warn_btn_start') }}
          </button>
        </div>
      </div>
    </div>
  </teleport>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps<{
  visible: boolean
  stepCount: number
  targetLabel: string
  reviewMode: boolean
  // R0001 "워크플로 결정부터": the run starts from the workflow decision; there is no step
  // count to quote, so the summary uses a dedicated decision-first line.
  fromDecision?: boolean
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
  'confirm': []
}>()

const { t } = useI18n()
const consented = ref(false)

const summaryText = computed(() => {
  if (props.fromDecision) {
    return props.reviewMode
      ? t('main.continuous_work.warn_summary_from_decision_review')
      : t('main.continuous_work.warn_summary_from_decision')
  }
  return props.reviewMode
    ? t('main.continuous_work.warn_summary_review', { count: props.stepCount, target: props.targetLabel })
    : t('main.continuous_work.warn_summary', { count: props.stepCount, target: props.targetLabel })
})

function onConfirm() {
  if (!consented.value) return
  emit('confirm')
  emit('update:visible', false)
}

function close() {
  emit('update:visible', false)
}

// Reset the consent each time the dialog opens — the user must re-acknowledge every run.
watch(
  () => props.visible,
  (val) => {
    if (val) consented.value = false
  },
)
</script>

<style scoped>
.modal-cwarn {
  width: 440px;
  max-width: 96vw;
}
.cwarn-body {
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.cwarn-summary {
  font-size: .85rem;
  font-weight: 600;
  color: var(--text);
  background: var(--surface-h);
  border-radius: var(--r-sm);
  padding: 8px 10px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.cwarn-list {
  margin: 0;
  padding-left: 18px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.cwarn-list li {
  font-size: .82rem;
  color: var(--text-s);
  line-height: 1.5;
}
.cwarn-consent {
  display: flex;
  align-items: flex-start;
  gap: 9px;
  padding: 10px 12px;
  border: 1px solid var(--danger);
  border-radius: var(--r);
  background: #fef2f2;
  cursor: pointer;
}
.cwarn-consent input { margin-top: 2px; flex-shrink: 0; }
.cwarn-consent span {
  font-size: .82rem;
  font-weight: 600;
  color: #991b1b;
  line-height: 1.45;
}
</style>
