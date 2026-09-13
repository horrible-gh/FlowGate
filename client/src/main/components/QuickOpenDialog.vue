<template>
  <!--
    Quick Open — flowgate.default.0560 T0018 (4순위, NR0011 원장 ID 43).
    D0008 §4 split this out of `MainPanel.vue`; D0008 maps it to `compact`.

    T0018 §2.3-4 — this dialog is deliberately left as the shell it already is. NR0011 §9-4
    found that the input is `disabled` (so the old `watch → quickInputRef.focus()` could
    never take), and the body is a single explanatory line rather than search results. This
    T is not an instruction to build the search: the disabled input and the notice stay
    exactly as they are, and only the overlay/header move onto the common layer. The
    `MainPanel.vue` watcher that tried to focus the input is gone with the overlay; with the
    input still disabled it had nothing left to do, and the common layer's initial-focus tree
    skips disabled elements by construction.

    No footer, and none is invented (T0018 §2.2-2): the header X is the only way out, as
    before. `:close-on-backdrop="false"` is explicit (T0018 §2.2-3) — `compact` defaults to
    `true`, but NR0011 records BD=X for this overlay.
  -->
  <DialogShell
    :open="visible"
    variant="compact"
    size="md"
    surface-class="quick-open-dialog"
    :close-on-backdrop="false"
    @request-close="emit('close')"
  >
    <template #header>
      <DialogHeader :title="t('main.quick_open.placeholder')" @close="emit('close')" />
    </template>
    <template #default>
      <input
        :value="query"
        class="form-ctrl"
        :placeholder="t('main.quick_open.placeholder')"
        disabled
        @input="emit('update:query', ($event.target as HTMLInputElement).value)"
      />
      <div class="empty" style="margin-top:16px;">
        <p>{{ t('main.main_panel.description_187') }}</p>
      </div>
    </template>
  </DialogShell>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'

import DialogHeader from './dialogs/DialogHeader.vue'
import DialogShell from './dialogs/DialogShell.vue'

defineProps<{
  visible: boolean
  query: string
}>()

const emit = defineEmits<{ 'update:query': [value: string]; close: [] }>()

const { t } = useI18n()
</script>

<!--
  Unscoped: `surface-class` lands on the dialog SURFACE, which DialogShell renders and
  teleports out of this component's subtree. 480px is the same `max-width` the old
  `.modal` box carried inline, kept so the migration does not resize the box.
-->
<style>
.fg-dialog-surface.quick-open-dialog {
  width: 480px;
}
</style>
