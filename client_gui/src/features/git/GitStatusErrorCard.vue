<template>
  <div class="gx-card danger" data-test="git-status-error">
    <div class="gx-title">{{ t('gui.git.status_error.title') }}</div>
    <p class="gx-reason" data-test="git-status-error-reason">
      {{ reason === 'network' ? t('gui.git.status_error.network') : t('gui.git.status_error.unreadable') }}
    </p>
    <div class="gx-actions">
      <button type="button" class="gx-btn" data-test="git-status-retry" @click="emit('retry')">
        {{ t('gui.git.status_error.retry') }}
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
// flowgate.v02.0065 — git/status could not be read (0065-R §3.1): its own block and words so a
// read or network failure is never taken for "Git 미연동". The session's statusError keeps the
// network / unreadable split; retry reads the status again.
import { useI18n } from 'vue-i18n'

defineProps<{ reason: 'unreadable' | 'network' | null }>()
const emit = defineEmits<{ retry: [] }>()
const { t } = useI18n()
</script>
