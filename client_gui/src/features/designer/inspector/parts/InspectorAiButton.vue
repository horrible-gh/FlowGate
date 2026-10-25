<template>
  <!-- 0017-CH: [AI 호출] is the robot icon (Phosphor Icons, MIT — the same glyph as client/shared/iconData `robot`), its
       words the tooltip; a running call turns it into a spinner. flowgate.v02.0094: moved out of InspectorSlotList as it
       was, so a card that draws its own lines (the test card) shows the same button. -->
  <IconButton
    :label="label"
    :title="title ?? label"
    variant="secondary"
    size="sm"
    class="inspector-slot-action inspector-slot-ai"
    :class="`is-${state}`"
    :disabled="disabled"
    data-icon="robot"
    :data-state="state"
  >
    <span v-if="state === 'running'" class="inspector-slot-ai-spinner" aria-hidden="true" />
    <svg v-else viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false">
      <path :d="ROBOT_PATH" />
    </svg>
  </IconButton>
</template>

<script setup lang="ts">
import IconButton from '../../../../components/ui/IconButton.vue'

withDefaults(
  defineProps<{
    /** The button's words (its accessible name and, without `title`, its tooltip). */
    label: string
    /** The tooltip when it says more than the words (why it cannot be pressed). */
    title?: string | null
    state?: 'enabled' | 'running'
    disabled?: boolean
  }>(),
  { title: null, state: 'enabled', disabled: false },
)

/** Phosphor Icons `robot` (regular, 256 viewBox; MIT): the [AI 호출] icon. */
const ROBOT_PATH =
  'M200,48H136V16a8,8,0,0,0-16,0V48H56A32,32,0,0,0,24,80V192a32,32,0,0,0,32,32H200a32,32,0,0,0,32-32V80A32,32,0,0,0,200,48Zm16,144a16,16,0,0,1-16,16H56a16,16,0,0,1-16-16V80A16,16,0,0,1,56,64H200a16,16,0,0,1,16,16Zm-52-56H92a28,28,0,0,0,0,56h72a28,28,0,0,0,0-56Zm-24,16v24H116V152ZM80,164a12,12,0,0,1,12-12h8v24H92A12,12,0,0,1,80,164Zm84,12h-8V152h8a12,12,0,0,1,0,24ZM72,108a12,12,0,1,1,12,12A12,12,0,0,1,72,108Zm88,0a12,12,0,1,1,12,12A12,12,0,0,1,160,108Z'
</script>

<style scoped>
/* A running call wears the info status tokens (the row actions' rule). */
.inspector-slot-action.is-running {
  border-color: var(--fg2-status-info-border);
  background: var(--fg2-status-info-bg);
  color: var(--fg2-status-info-fg);
}

.inspector-slot-action.inspector-slot-ai {
  flex: 0 0 auto;
  color: #9fc4e8;
}

.inspector-slot-action.inspector-slot-ai :deep(svg) {
  width: 16px;
  height: 16px;
}

.inspector-slot-ai-spinner {
  width: 12px;
  height: 12px;
  box-sizing: border-box;
  border: 2px solid rgba(140, 192, 255, 0.3);
  border-top-color: currentColor;
  border-radius: 50%;
  animation: inspector-slot-ai-spin 0.7s linear infinite;
}

@keyframes inspector-slot-ai-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
