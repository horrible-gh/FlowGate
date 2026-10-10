<template>
  <!-- flowgate.v02.0048 T#1 — reason line of an unavailable operation (D#1 §2.2 parts, §8.1): a
       visible but disabled operation shows why. The feature resolves its own reason key to text;
       this part only shows it. -->
  <p class="inspector-reason" role="note" data-test="inspector-reason" :data-tone="tone">{{ text }}</p>
</template>

<script setup lang="ts">
withDefaults(
  defineProps<{
    text: string
    tone?: 'muted' | 'warning' | 'danger'
  }>(),
  { tone: 'muted' },
)
</script>

<style scoped>
.inspector-reason {
  margin: 4px 0 0;
  color: var(--fg2-muted);
  font-size: 11.5px;
  line-height: 1.45;
}

.inspector-reason[data-tone='warning'] {
  color: var(--fg2-status-warning-fg);
}

.inspector-reason[data-tone='danger'] {
  color: var(--fg2-status-danger-fg);
}
</style>
