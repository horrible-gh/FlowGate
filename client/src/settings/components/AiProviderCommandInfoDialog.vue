<template>
  <!--
    Provider command/info view — flowgate.default.0560 T0020 (4.5순위), instance 2 of the three
    `AiProviderListEditor.vue` held. D0008 §6 maps it to `readonly`: the body only reports what
    is stored, and the single footer button puts it away.

    `close-on-backdrop="true"` is explicit (0560 T0035 §3, NR0029 §10.3-11): `readonly`'s
    variant default flipped to `false` so a stray background click cannot lose a form or a
    diff behind another dialog, but this overlay closed on a backdrop click
    (`@click.self="closeCmd"`) before it ever joined the common layer, and a read-only command
    view has nothing to lose by closing — so the override keeps that legacy behaviour instead of
    letting it flip with the table.

    `size="md"` is the 520px `.modal-box` track this box used; `readonly`'s default is `lg`.
  -->
  <DialogShell
    :open="open"
    variant="readonly"
    size="md"
    :close-on-backdrop="true"
    :return-focus-to="returnFocusTo"
    @request-close="emit('close')"
  >
    <template #header>
      <DialogHeader :title="title" icon="terminal" @close="emit('close')" />
    </template>

    <template #default>
      <template v-if="row">
        <template v-if="row.exec_type === 'cli'">
          <div class="form-group">
            <label class="form-label">{{ t('settings.ai.label_cli_command') }}</label>
            <div class="code-block mono">{{ row.cli_command || '—' }}</div>
          </div>
          <p v-if="skipsPermissions(row)" class="form-hint ai-skip-warn">
            {{ t('settings.ai.skip_permissions_badge') }} — {{ t('settings.ai.skip_permissions_cmd_warn') }}
          </p>
        </template>
        <template v-else>
          <div class="form-group">
            <label class="form-label">{{ t('settings.ai.label_api_model') }}</label>
            <div class="code-block mono">{{ row.api_model || '—' }}</div>
          </div>
          <div v-if="row.api_base_url" class="form-group">
            <label class="form-label">{{ t('settings.ai.label_api_base_url') }}</label>
            <div class="code-block mono">{{ row.api_base_url }}</div>
          </div>
          <div class="form-group">
            <label class="form-label">{{ t('settings.ai.label_api_key') }}</label>
            <p class="form-hint" style="margin:0;">{{ keyStateLabel(row) }}</p>
          </div>
        </template>
      </template>
    </template>

    <template #footer>
      <DialogFooter :actions="actions" />
    </template>
  </DialogShell>
</template>

<script setup>
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';
import DialogFooter from '@main/components/dialogs/DialogFooter.vue';
import DialogHeader from '@main/components/dialogs/DialogHeader.vue';
import DialogShell from '@main/components/dialogs/DialogShell.vue';

// The row itself, and the two label helpers the list shares with this view, belong to
// `AiProviderListEditor` and are handed down (D0008 §1). `row` is null while the dialog is
// closed, which is also what `open` reports.
defineProps({
  open: { type: Boolean, default: false },
  row: { type: Object, default: null },
  title: { type: String, default: '' },
  keyStateLabel: { type: Function, required: true },
  skipsPermissions: { type: Function, required: true },
  returnFocusTo: { type: Object, default: null },
});

const emit = defineEmits(['close']);

const { t } = useI18n();

/**
 * `[닫기]` alone, as `dismiss`. Nothing here commits or aborts anything — D0008 §3's third
 * cancel meaning — and a read-only surface gets no primary button to inherit (D0008 §3-7),
 * which is the "닫기 painted as the primary" pattern NR0005 §4.1 named.
 */
const actions = computed(() => [
  {
    id: 'close',
    label: t('common.close'),
    role: 'dismiss',
    onSelect: () => emit('close'),
  },
]);
</script>

<style scoped>
/* Moved with the markup it styles (T0020). */
.ai-skip-warn {
  color: var(--danger, #d64545);
}
</style>
