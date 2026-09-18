<template>
  <DialogShell :open="true" variant="blocking" surface-class="dialog-storage-migrate-progress-dialog" :blocking="state === 'running'" :closeable="state !== 'running'" :close-on-escape="state !== 'running'" @request-close="$emit('close')">
    <template #header>
      <DialogHeader title="" :closeable="state !== 'running'" @close="$emit('close')">
        <template #title>
          <AppIcon v-if="state === 'running'" name="spinner" spin style="color: var(--primary);" />
          <AppIcon v-else-if="state === 'success'" name="check-circle" style="color: var(--success, #10b981);" />
          <AppIcon v-else name="x-circle" style="color: var(--danger, #ef4444);" />
          {{ titleText }}
        </template>
      </DialogHeader>
    </template>

      
      <div class="dialog-feature-body">
        <p v-if="state === 'running'" style="margin: 0;">
          {{ $t('settings.project.storage_migrate.progress_body') }}
        </p>

        <template v-else-if="state === 'success' && result">
          <p style="margin: 0 0 8px 0;">{{ $t('settings.project.storage_migrate.success_body') }}</p>
          <div class="code-block" style="font-size: 0.8rem;">
            <div v-if="result.migrate?.groups">
              groups: inserted={{ result.migrate.groups.inserted }} unmatched={{ result.migrate.groups.unmatched?.length || 0 }}
            </div>
            <div v-if="result.migrate?.documents">
              documents: inserted={{ result.migrate.documents.inserted }} unmatched={{ result.migrate.documents.unmatched?.length || 0 }}
            </div>
            <div v-if="result.migrate?.events">
              events: inserted={{ result.migrate.events.inserted }} skipped={{ result.migrate.events.skipped }}
            </div>
            <div v-if="result.migrate?.files">
              files: copied={{ result.migrate.files.copied_files }} ({{ result.migrate.files.copied_dirs }} dirs)
            </div>
            <div v-if="result.delete">
              legacy deleted: files={{ result.delete.deleted_files }} dirs={{ result.delete.deleted_dirs }}
              <span v-if="result.delete.note"> — {{ result.delete.note }}</span>
            </div>
            <div v-if="result.stage === 'noop' || result.stage === 'settings_only'" style="color: var(--text-2);">
              {{ result.message }}
            </div>
          </div>
        </template>

        <template v-else-if="state === 'error'">
          <p style="margin: 0 0 8px 0; color: var(--danger, #ef4444);">
            {{ $t('settings.project.storage_migrate.error_body') }}
          </p>
          <div v-if="result?.verify?.failures?.length" class="code-block" style="font-size: 0.8rem; color: var(--danger, #ef4444);">
            <div v-for="(f, i) in result.verify.failures" :key="i">{{ f }}</div>
          </div>
          <p v-else-if="errorMessage" class="mono" style="font-size: 0.8rem; color: var(--danger, #ef4444);">{{ errorMessage }}</p>
        </template>
      </div>
      
    

    <template #footer>
      <DialogFooter :actions="[
        ...(((state !== 'running')) ? [{ id: 'action-0', role: 'cancel' as const, label: $t('common.close'), onSelect: () => { $emit('close') } }] : [])
      ]">
        <template #action-action-0><template v-if="(state !== 'running')">
          {{ $t('common.close') }}
        </template></template>
      </DialogFooter>
    </template>
  </DialogShell>
</template>

<script setup lang="ts">
import DialogShell from '../../main/components/dialogs/DialogShell.vue'
import DialogHeader from '../../main/components/dialogs/DialogHeader.vue'
import DialogFooter from '../../main/components/dialogs/DialogFooter.vue'
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import AppIcon from '@shared/AppIcon.vue'

const props = defineProps<{
  state: 'running' | 'success' | 'error'
  result?: any
  errorMessage?: string
}>()

defineEmits<{ close: [] }>()

const { t } = useI18n()

const titleText = computed(() => {
  if (props.state === 'running') return t('settings.project.storage_migrate.progress_title')
  if (props.state === 'success') return t('settings.project.storage_migrate.success_title')
  return t('settings.project.storage_migrate.error_title')
})
</script>

<style scoped>
:global(.fg-dialog-surface.dialog-storage-migrate-progress-dialog) { max-width: 520px; }
</style>
