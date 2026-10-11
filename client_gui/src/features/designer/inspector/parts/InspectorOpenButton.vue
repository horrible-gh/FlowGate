<template>
  <!-- flowgate.v02.0053 T#5 — the document icon that opens a document's (or a question's) modeless window
       in this tab (0053 CH: ↗ reads as an outside link, so the open button is a document icon; its tooltip
       is 열기). One part for the document tab lines and the Q&A rows. A button that really goes to another
       screen ("Git 패널 열기 ↗") keeps its ↗ and is not this part. Display only: the caller hears `click`. -->
  <IconButton
    class="inspector-open-button"
    :label="t('gui.designer.inspector.slot.open')"
    :title="title ?? t('gui.designer.inspector.slot.open')"
    variant="secondary"
    size="sm"
    :disabled="disabled"
    data-icon="document"
    @click="emit('click')"
  >
    <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path d="M4 1.75h5.1l3.15 3.15v9.35H4z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" />
      <path d="M9 1.75V5h3.25M6.1 8.1h4.1M6.1 10.6h4.1" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" />
    </svg>
  </IconButton>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import IconButton from '../../../../components/ui/IconButton.vue'

withDefaults(
  defineProps<{
    disabled?: boolean
    /** Hover text when it is not 열기 (why a disabled one cannot open). */
    title?: string | null
  }>(),
  { disabled: false, title: null },
)

const emit = defineEmits<{ click: [] }>()

const { t } = useI18n()
</script>
