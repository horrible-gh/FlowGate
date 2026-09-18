<template>
  <DialogShell :open="true" variant="confirm" surface-class="dialog-storage-migrate-confirm-dialog"  @request-close="$emit('cancel')">
    <template #header>
      <DialogHeader title="" :closeable="true" @close="$emit('cancel')">
        <template #title>
          <AppIcon name="warning" style="color: var(--warning, #f59e0b);" />
          {{ $t('settings.project.storage_migrate.confirm_title') }}
        </template>
      </DialogHeader>
    </template>

      
      <div class="dialog-feature-body">
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
      
    

    <template #footer>
      <DialogFooter :actions="[
        { id: 'action-0', role: 'cancel', label: $t('common.cancel'), onSelect: () => { $emit('cancel') } },
        { id: 'action-1', role: 'primary', label: $t('settings.project.storage_migrate.confirm_button'), onSelect: () => { $emit('confirm') } }
      ]">
        <template #action-action-0>
          {{ $t('common.cancel') }}
        </template>
        <template #action-action-1>
          <AppIcon name="check" />
          {{ $t('settings.project.storage_migrate.confirm_button') }}
        </template>
      </DialogFooter>
    </template>
  </DialogShell>
</template>

<script setup lang="ts">
import DialogShell from '../../main/components/dialogs/DialogShell.vue'
import DialogHeader from '../../main/components/dialogs/DialogHeader.vue'
import DialogFooter from '../../main/components/dialogs/DialogFooter.vue'
import AppIcon from '@shared/AppIcon.vue'
defineProps<{ fromPath: string; toPath: string }>()
defineEmits<{ confirm: []; cancel: [] }>()
</script>

<style scoped>
:global(.fg-dialog-surface.dialog-storage-migrate-confirm-dialog) { max-width: 480px; }
</style>
