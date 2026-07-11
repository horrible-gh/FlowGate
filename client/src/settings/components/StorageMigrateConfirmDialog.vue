<template>
  <div class="modal-bg" role="dialog" aria-modal="true">
    <div class="modal-box" style="max-width: 480px;">
      <div class="modal-hd">
        <span class="modal-title">
          <AppIcon name="warning" style="color: var(--warning, #f59e0b);" />
          {{ $t('settings.project.storage_migrate.confirm_title') }}
        </span>
        <button class="modal-close" type="button" @click="$emit('cancel')">
          <AppIcon name="x" />
        </button>
      </div>
      <div class="modal-bd">
        <p style="margin: 0 0 12px 0;">
          {{ $t('settings.project.storage_migrate.confirm_body') }}
        </p>
        <div class="code-block" style="margin: 8px 0;">
          <div><strong>{{ $t('settings.project.storage_migrate.from_label') }}:</strong> {{ fromPath || $t('settings.project.storage_migrate.default_label') }}</div>
          <div><strong>{{ $t('settings.project.storage_migrate.to_label') }}:</strong> {{ toPath }}</div>
        </div>
        <p style="margin: 12px 0 0 0; color: var(--danger, #ef4444); font-size: 0.875rem;">
          <AppIcon name="warning-circle" />
          {{ $t('settings.project.storage_migrate.confirm_warning') }}
        </p>
      </div>
      <div class="modal-ft" style="display: flex; justify-content: flex-end; gap: 8px;">
        <button class="btn btn-secondary" @click="$emit('cancel')">
          {{ $t('common.cancel') }}
        </button>
        <button class="btn btn-primary" @click="$emit('confirm')">
          <AppIcon name="check" />
          {{ $t('settings.project.storage_migrate.confirm_button') }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import AppIcon from '@shared/AppIcon.vue'
defineProps<{ fromPath: string; toPath: string }>()
defineEmits<{ confirm: []; cancel: [] }>()
</script>
