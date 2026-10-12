<template>
  <!-- flowgate.v02.0053 T#6 — the [+] of an Inspector tab (0053 CH: buttons are icons, their name is the
       tooltip): the Q&A tab registers a question with it, the attachments tab adds files. One part so
       both draw the same plus. Display only: the caller gives the name (accessible name), the tooltip
       (the name, a limit or why it is closed) and hears `click`. -->
  <IconButton
    class="inspector-add-button"
    :label="label"
    :title="title ?? label"
    variant="secondary"
    size="sm"
    :disabled="disabled"
    data-icon="add"
    @click="emit('click')"
  >
    <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path d="M8 3v10M3 8h10" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" />
    </svg>
  </IconButton>
</template>

<script setup lang="ts">
import IconButton from '../../../../components/ui/IconButton.vue'

withDefaults(
  defineProps<{
    /** What the button does (its accessible name). */
    label: string
    /** Hover text when it is not the label (a limit, or why it is disabled). */
    title?: string | null
    disabled?: boolean
  }>(),
  { title: null, disabled: false },
)

const emit = defineEmits<{ click: [] }>()
</script>
