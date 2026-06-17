<template>
  <div class="modal-bg" role="dialog" aria-modal="true">
    <div class="modal-box" style="max-width: 520px;">
      <div class="modal-hd">
        <span class="modal-title">
          <i v-if="state === 'running'" class="fa-solid fa-spinner fa-spin" style="color: var(--primary);"></i>
          <i v-else-if="state === 'success'" class="fa-solid fa-circle-check" style="color: var(--success, #10b981);"></i>
          <i v-else class="fa-solid fa-circle-xmark" style="color: var(--danger, #ef4444);"></i>
          {{ titleText }}
        </span>
        <button v-if="state !== 'running'" class="modal-close" type="button" @click="$emit('close')">
          <i class="fa-solid fa-xmark"></i>
        </button>
      </div>
      <div class="modal-bd">
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
      <div v-if="state !== 'running'" class="modal-ft" style="display: flex; justify-content: flex-end;">
        <button class="btn btn-primary" @click="$emit('close')">
          {{ $t('common.close') }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

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
