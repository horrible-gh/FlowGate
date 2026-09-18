<template>
  <!--
    Provider delete confirmation — flowgate.default.0560 T0020 (4.5순위), instance 3 of the
    three `AiProviderListEditor.vue` held. D0008 §6 maps it to `confirm-danger`.

    Footer is D0008 §6's Danger Confirm shape, `[취소] [위험 주버튼]`: 삭제 is role `primary`
    with `tone="danger"`, NOT role `danger`. Role `danger` is the separate position a
    destructive action takes when it sits BESIDE the button that completes the dialog
    (footerRolePriority puts it left of cancel); here the destructive action IS the completing
    action, so it takes the primary position and the tone is what paints it red.

    `closeOnBackdrop` is NOT overridden: the old overlay had no `@click.self`, and
    `confirm-danger`'s variant default is already `false` (dialogTypes.ts).

    Initial focus needs no `data-dialog-autofocus`: L0009 §4 sends `confirm-danger` to the
    cancel button first, which is the safer of the two buttons and the only sensible target on
    a body with no fields.
  -->
  <DialogShell
    :open="open"
    variant="confirm-danger"
    surface-class="ai-provider-delete-dialog"
    :return-focus-to="returnFocusTo"
    @request-close="emit('close')"
  >
    <template #header>
      <DialogHeader :title="t('settings.ai.delete_provider_title')" @close="emit('close')" />
    </template>

    <template #default>
      <p style="margin:0 0 8px;">{{ t('settings.ai.delete_provider_body', { name: row?.name || '' }) }}</p>
      <p class="form-hint" style="margin:0;">{{ t('settings.ai.delete_provider_hint') }}</p>
    </template>

    <template #footer>
      <DialogFooter :actions="actions">
        <template #action-delete>
          <AppIcon name="trash" /> {{ t('common.delete') }}
        </template>
      </DialogFooter>
    </template>
  </DialogShell>
</template>

<script setup>
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';
import AppIcon from '@shared/AppIcon.vue';
import DialogFooter from '@main/components/dialogs/DialogFooter.vue';
import DialogHeader from '@main/components/dialogs/DialogHeader.vue';
import DialogShell from '@main/components/dialogs/DialogShell.vue';

// The row being deleted stays the list editor's data and is only read here for the message
// (D0008 §1); the deletion itself is the parent's, reported through `confirm`.
defineProps({
  open: { type: Boolean, default: false },
  row: { type: Object, default: null },
  returnFocusTo: { type: Object, default: null },
});

const emit = defineEmits(['close', 'confirm']);

const { t } = useI18n();

const actions = computed(() => [
  {
    id: 'cancel',
    label: t('common.cancel'),
    role: 'cancel',
    onSelect: () => emit('close'),
  },
  {
    id: 'delete',
    label: t('common.delete'),
    role: 'primary',
    tone: 'danger',
    onSelect: () => emit('confirm'),
  },
]);
</script>

<!--
  Unscoped: `surface-class` lands on the dialog surface, which DialogShell renders and
  teleports out of this component's subtree, so a scoped rule could never reach it. 440px is
  the width this confirm box carried inline (`style="width:440px"`); `confirm-danger`'s `sm`
  track is 400px, and this box was deliberately wider than the app's plain confirm.
-->
<style>
.fg-dialog-surface.ai-provider-delete-dialog {
  width: 440px;
}
</style>
