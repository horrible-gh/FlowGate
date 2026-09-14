<template>
  <div class="card md-preview-card">
    <div class="card-hd">
      <span class="card-title">
        <AppIcon name="prohibit" style="color:var(--danger, #dc2626);" />
        {{ t('main.group_discard.panel_title') }}
      </span>
    </div>
    <div class="card-bd ac-final-approval-body">
      <AppIcon name="x-circle" class="ac-fa-icon" style="color:var(--danger, #dc2626);" />
      <p class="ac-fa-title">{{ t('main.group_discard.panel_title_done') }}</p>
      <p class="ac-fa-desc">{{ t('main.group_discard.panel_desc_done') }}</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import AppIcon from '@shared/AppIcon.vue'

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
.ac-fa-icon { font-size: 2.5rem; opacity: .85; }
.ac-fa-title { margin: 6px 0 0; color: var(--text, #1e293b); font-size: 1.05rem; font-weight: 600; }
.ac-fa-desc { max-width: 420px; margin: 0; color: var(--text-m, #64748b); font-size: .875rem; line-height: 1.5; }
</style>