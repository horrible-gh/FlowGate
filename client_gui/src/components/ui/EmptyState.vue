<template>
  <div class="ui-empty-state" :class="{ 'ui-empty-state--compact': compact }">
    <div v-if="$slots.icon" class="ui-empty-state-icon" aria-hidden="true"><slot name="icon" /></div>
    <p class="ui-empty-state-title">{{ title }}</p>
    <p v-if="description" class="ui-empty-state-description">{{ description }}</p>
    <div v-if="$slots.action" class="ui-empty-state-action"><slot name="action" /></div>
  </div>
</template>

<script setup lang="ts">
// Domain-free "nothing to show yet" state (T0008 §7). Knows nothing about which list or
// panel is empty -- title/description/action all come from the caller. Never hardcodes a
// domain sentence like "프로젝트가 없습니다" / "브랜치가 없습니다" (T0008 §7 금지).
withDefaults(
  defineProps<{
    title: string
    description?: string
    compact?: boolean
  }>(),
  { description: undefined, compact: false },
)
</script>

<style scoped>
.ui-empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 48px 24px;
  text-align: center;
  color: var(--fg2-muted);
}

.ui-empty-state--compact {
  padding: 20px 16px;
  gap: 6px;
}

.ui-empty-state-icon {
  display: inline-flex;
  margin-bottom: 4px;
  font-size: 28px;
  opacity: 0.7;
}

.ui-empty-state--compact .ui-empty-state-icon {
  font-size: 20px;
}

.ui-empty-state-title {
  margin: 0;
  font-size: 14px;
  font-weight: 650;
  color: var(--fg2-text);
}

.ui-empty-state-description {
  margin: 0;
  max-width: 360px;
  font-size: 12px;
  line-height: 1.6;
}

.ui-empty-state-action {
  margin-top: 6px;
}
</style>
