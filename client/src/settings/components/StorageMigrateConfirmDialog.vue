<template>
  <div class="modal-bg" role="dialog" aria-modal="true">
    <div class="modal-box" style="max-width: 480px;">
      <div class="modal-hd">
        <span class="modal-title">
          <i class="fa-solid fa-triangle-exclamation" style="color: var(--warning, #f59e0b);"></i>
          {{ $t('settings.project.storage_migrate.confirm_title') }}
        </span>
        <button class="modal-close" type="button" @click="$emit('cancel')">
          <i class="fa-solid fa-xmark"></i>
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
          <i class="fa-solid fa-circle-exclamation"></i>
          {{ $t('settings.project.storage_migrate.confirm_warning') }}
        </p>
      </div>
      <div class="modal-ft" style="display: flex; justify-content: flex-end; gap: 8px;">
        <button class="btn btn-secondary" @click="$emit('cancel')">
          {{ $t('common.cancel') }}
        </button>
        <button class="btn btn-primary" @click="$emit('confirm')">
          <i class="fa-solid fa-check"></i>
          {{ $t('settings.project.storage_migrate.confirm_button') }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
defineProps<{ fromPath: string; toPath: string }>()
defineEmits<{ confirm: []; cancel: [] }>()
</script>
