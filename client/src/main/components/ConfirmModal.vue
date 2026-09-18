<template>
  <!-- flowgate.default.0560 T0016 §2.2 (2단계) — migrated onto the common dialog layer.
       D0008 "기존 instance 이관 목적지" maps this instance to the `confirm` variant (and
       `confirm-danger` when the caller asks for the destructive flavour), so the shell
       decides the frame, the header and the footer ORDER, and this file keeps only the
       message, the optional extra slot and the two outcomes. -->
  <DialogShell
    ref="shellRef"
    :open="visible"
    :variant="danger ? 'confirm-danger' : 'confirm'"
    @request-close="onCancel"
  >
    <template #header>
      <DialogHeader :title="title" @close="onHeaderClose">
        <template #icon>
          <AppIcon v-if="danger" name="warning" style="color:var(--danger);" />
          <AppIcon v-else name="question" style="color:var(--primary);" />
        </template>
      </DialogHeader>
    </template>
    <template #default="{ descriptionId }">
      <p :id="descriptionId" class="confirm-msg">{{ message }}</p>
      <!-- Optional extra content (e.g. flowgate.default.0162 §3.1 — the git
           finalize choice block shown inside the AC final-approval confirm). -->
      <slot />
    </template>
    <template #footer>
      <DialogFooter :actions="actions" />
    </template>
  </DialogShell>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import AppIcon from '@shared/AppIcon.vue'

import DialogFooter from './dialogs/DialogFooter.vue'
import DialogHeader from './dialogs/DialogHeader.vue'
import DialogShell from './dialogs/DialogShell.vue'
import type { DialogAction } from './dialogs/dialogTypes'

const props = defineProps<{
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

const shellRef = ref<InstanceType<typeof DialogShell> | null>(null)

/**
 * `[취소] [확인]` — cancel(30) then primary(40). This component was already the 3순위
 * baseline (T0016 §2.1 #1: `btn btn-secondary`, immediately left of the primary button),
 * so the roles below reproduce the order it had; what changed is that the order is now a
 * property of the layer instead of a property of this file's markup.
 */
const actions = computed<DialogAction[]>(() => [
  {
    id: 'cancel',
    label: props.cancelLabel ?? t('common.cancel'),
    role: 'cancel',
    onSelect: onCancel,
  },
  {
    id: 'confirm',
    label: props.confirmLabel ?? t('common.confirm'),
    role: 'primary',
    tone: props.danger ? 'danger' : 'default',
    onSelect: onConfirm,
  },
])

function onConfirm() {
  emit('confirm')
  emit('update:visible', false)
}

// header X / ESC / backdrop all arrive here, exactly as the 취소 button does — the
// single close meaning L0009 §2 fixed for this variant.
function onCancel() {
  emit('cancel')
  emit('update:visible', false)
}

function onHeaderClose() {
  shellRef.value?.requestClose('header')
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
