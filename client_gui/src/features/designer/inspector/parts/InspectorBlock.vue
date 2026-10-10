<template>
  <!-- flowgate.v02.0051 — the v7 settings block (0035 insp.css `.blk` / `.blk-h`): a framed group
       with a title line (title · sub · controls on the right) and its rows. `locked` dims the rows
       (v7 `.set-locked`: a running card's settings cannot change) — the caller still disables its
       controls; this only draws it. -->
  <section class="inspector-block" :class="{ 'is-locked': locked }" data-test="inspector-block" :aria-disabled="locked ? 'true' : undefined">
    <div v-if="title || $slots.head" class="inspector-block-head">
      <span v-if="title" class="inspector-block-title" data-test="inspector-block-title">{{ title }}</span>
      <span v-if="sub" class="inspector-block-sub" data-test="inspector-block-sub">{{ sub }}</span>
      <span class="inspector-block-push" />
      <slot name="head" />
    </div>
    <div class="inspector-block-body">
      <slot />
    </div>
  </section>
</template>

<script setup lang="ts">
withDefaults(
  defineProps<{
    title?: string | null
    sub?: string | null
    locked?: boolean
  }>(),
  { title: null, sub: null, locked: false },
)
</script>

<style scoped>
.inspector-block {
  box-sizing: border-box;
  min-width: 0;
  /* flowgate.v02.0052 T#2 — the rows inside follow the block's width, not the window's (264px Inspector). */
  container-type: inline-size;
  padding: 10px 12px;
  border: 1px solid var(--fg2-line);
  border-radius: 8px;
  background: #0e2034;
}

.inspector-block + .inspector-block {
  margin-top: 8px;
}

.inspector-block-head {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  margin-bottom: 8px;
  color: #dce7f2;
  font-size: 12.5px;
  font-weight: 750;
}

.inspector-block-sub {
  color: #7f96af;
  font-size: 11px;
  font-weight: 500;
}

.inspector-block-push {
  flex: 1 1 auto;
}

.inspector-block-body {
  min-width: 0;
}

.inspector-block.is-locked .inspector-block-body {
  opacity: 0.6;
  pointer-events: none;
}
</style>
