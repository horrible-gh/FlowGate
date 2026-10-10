<template>
  <!-- flowgate.v02.0051 — one item of the header ⋯ menu (0035 insp.js `.menu-pop button`): a card
       feature puts its own items (실행 이력 · ■ 중단 · ↻ 재실행 · 위임 중단 …) after the common ID 복사.
       Display only: `disabled` with a `reason` (hover title) is how an item whose operation is not
       connected yet is shown (0051-R §2.3) — it is never left out and never looks as if it worked. -->
  <button
    type="button"
    role="menuitem"
    class="inspector-menu-item"
    :disabled="disabled"
    :title="disabled ? (reason ?? undefined) : (hint ?? undefined)"
    :aria-disabled="disabled ? 'true' : undefined"
    data-test="inspector-menu-item"
    :data-item="item"
    @click="onClick"
  ><slot /></button>
</template>

<script setup lang="ts">
const props = withDefaults(
  defineProps<{
    /** Stable item name (data-item) for tests and styling. */
    item: string
    disabled?: boolean
    /** Why the item is disabled (hover title). */
    reason?: string | null
    /** Hover title of an enabled item. */
    hint?: string | null
  }>(),
  { disabled: false, reason: null, hint: null },
)

const emit = defineEmits<{ select: [] }>()

function onClick() {
  if (props.disabled) return
  emit('select')
}
</script>

<style scoped>
.inspector-menu-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  height: 30px;
  padding: 0 10px;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: #c3d3e3;
  font-size: 12px;
  text-align: left;
  white-space: nowrap;
  cursor: pointer;
}

.inspector-menu-item:hover:not(:disabled) {
  background: #15345a;
}

.inspector-menu-item:disabled {
  color: #5d738b;
  cursor: not-allowed;
}
</style>
