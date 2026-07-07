<template>
  <teleport to="body">
    <div v-if="visible" class="modal-bg">
      <div class="modal-box" style="max-width:400px;">
        <div class="modal-hd">
          <span class="modal-title">
            <i v-if="danger" class="fa-solid fa-triangle-exclamation" style="color:var(--danger);"></i>
            <i v-else class="fa-solid fa-circle-question" style="color:var(--primary);"></i>
            {{ title }}
          </span>
          <button class="modal-close" type="button" @click="onCancel">
            <i class="fa-solid fa-xmark"></i>
          </button>
        </div>
        <div class="modal-bd">
          <p class="confirm-msg">{{ message }}</p>
          <!-- Optional extra content (e.g. flowgate.default.0162 §3.1 — the git
               finalize choice block shown inside the AC final-approval confirm). -->
          <slot />
        </div>
        <div class="modal-ft">
          <button type="button" class="btn btn-secondary" @click="onCancel">
            {{ cancelLabel ?? t('common.cancel') }}
          </button>
          <button type="button" :class="['btn', danger ? 'btn-danger' : 'btn-primary']" @click="onConfirm">
            {{ confirmLabel ?? t('common.confirm') }}
          </button>
        </div>
      </div>
    </div>
  </teleport>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'

defineProps<{
  visible: boolean
  title: string
  message: string
  confirmLabel?: string
  cancelLabel?: string
  danger?: boolean
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
  'confirm': []
  'cancel': []
}>()

const { t } = useI18n()

function onConfirm() {
  emit('confirm')
  emit('update:visible', false)
}

function onCancel() {
  emit('cancel')
  emit('update:visible', false)
}
</script>

<style scoped>
.confirm-msg {
  font-size: .9rem;
  color: var(--text);
  line-height: 1.5;
  margin: 0;
}
</style>

