<template>
  <GitFinalizePanel
    v-if="!readOnly && completed"
    :group-id="groupId"
    @open-archive="emit('open-archive', $event)"
    @archived="emit('archived', $event)"
  />
  <div class="card md-preview-card">
    <div class="card-hd">
      <span class="card-title">
        <AppIcon name="clipboard-text" style="color:var(--text-m);" />
        {{ t('main.review_action_bar.final_approval') }}
      </span>
    </div>
    <div class="card-bd ac-final-approval-body">
      <template v-if="completed">
        <AppIcon name="check-circle" class="ac-fa-icon ac-fa-icon-done" />
        <p class="ac-fa-title">{{ t('main.final_approval.panel_title_done') }}</p>
        <p class="ac-fa-desc">{{ t('main.final_approval.panel_desc_done') }}</p>
      </template>
      <template v-else>
        <AppIcon name="seal" class="ac-fa-icon" />
        <p class="ac-fa-title">{{ t('main.final_approval.panel_title') }}</p>
        <p class="ac-fa-desc">{{ t('main.final_approval.panel_desc') }}</p>
      </template>
    </div>
  </div>
  <GitFinalizePanel
    v-if="!readOnly && !completed"
    :group-id="groupId"
    @open-archive="emit('open-archive', $event)"
    @archived="emit('archived', $event)"
  />
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import AppIcon from '@shared/AppIcon.vue'
import GitFinalizePanel from '../GitFinalizePanel.vue'

defineProps<{
  completed: boolean
  groupId: string
  readOnly: boolean
}>()

const emit = defineEmits<{
  'open-archive': [groupId: string]
  archived: [groupId: string]
}>()
const { t } = useI18n()
</script>

<style scoped>
.ac-final-approval-body {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 48px 24px;
  text-align: center;
}
.ac-fa-icon { color: var(--primary, #2563eb); font-size: 2.5rem; opacity: .85; }
.ac-fa-icon-done { color: var(--success, #16a34a); }
.ac-fa-title { margin: 6px 0 0; color: var(--text, #1e293b); font-size: 1.05rem; font-weight: 600; }
.ac-fa-desc { max-width: 420px; margin: 0; color: var(--text-m, #64748b); font-size: .875rem; line-height: 1.5; }
</style>