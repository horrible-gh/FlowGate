<template>
  <!-- flowgate.v02.0053 T#7 — a refused change, next to its control: one red ! icon and no text on screen (0053
       CH: the state is an icon, the explanation a tooltip). `name` is its short accessible name, `reason`
       the tooltip (its accessible description); it takes the focus so the tooltip can be reached. -->
  <span class="inspector-failure-icon" role="img" tabindex="0" :aria-label="name" :title="reason" data-test="inspector-failure-icon" data-tone="danger">
    <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false" data-icon="error">
      <circle cx="8" cy="8" r="6.2" fill="none" stroke="currentColor" stroke-width="1.5" />
      <path d="M8 4.6v4.2" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" />
      <circle cx="8" cy="11.3" r="0.95" fill="currentColor" />
    </svg>
  </span>
</template>

<script setup lang="ts">
defineProps<{
  name: string
  reason: string
}>()
</script>

<style scoped>
.inspector-failure-icon {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  color: var(--fg2-status-danger-fg);
  cursor: help;
}

.inspector-failure-icon svg {
  width: 16px;
  height: 16px;
}

.inspector-failure-icon:focus-visible {
  outline: 2px solid var(--fg2-focus, #4c9aff);
  outline-offset: 1px;
}
</style>
