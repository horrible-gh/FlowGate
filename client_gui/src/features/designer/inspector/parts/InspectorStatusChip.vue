<template>
  <!-- flowgate.v02.0048 T#1 — Inspector status chip (D#1 §2.2 parts): one short state label with a
       tone. The feature maps its own value to the label and tone; this part only shows them. -->
  <Badge class="inspector-status-chip" :tone="tone" size="sm" data-test="inspector-status-chip" :data-tone="tone">{{ label }}</Badge>
</template>

<script setup lang="ts">
import Badge from '../../../../components/ui/Badge.vue'

withDefaults(
  defineProps<{
    label: string
    tone?: 'neutral' | 'info' | 'success' | 'warning' | 'danger'
  }>(),
  { tone: 'neutral' },
)
</script>

<style scoped>
.inspector-status-chip {
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
