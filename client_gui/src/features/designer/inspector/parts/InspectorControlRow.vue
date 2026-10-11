<template>
  <!-- flowgate.v02.0051 — one v7 setting row (0035 base `.control-row`): a label on the left (with
       an optional hover hint) and the control on the right. Display only; the control comes from the
       caller (a Select, a segment, an input).
       flowgate.v02.0053 T#7 — `wrap`: the controls go on to a next line when they do not fit beside each other
       (a choice with its status note, a branch with two switches) instead of running past the row. -->
  <div class="inspector-control-row" :class="{ 'is-wrap': wrap }" data-test="inspector-control-row" :data-label="label">
    <span class="inspector-control-label" :title="hint ?? undefined">{{ label }}</span>
    <span class="inspector-control-value"><slot /></span>
  </div>
</template>

<script setup lang="ts">
withDefaults(
  defineProps<{
    label: string
    hint?: string | null
    /** The controls may take more than one line (each stays whole). */
    wrap?: boolean
  }>(),
  { hint: null, wrap: false },
)
</script>

<style scoped>
.inspector-control-row {
  display: grid;
  grid-template-columns: 112px minmax(0, 1fr);
  gap: 10px;
  align-items: center;
  min-width: 0;
  margin: 7px 0;
  color: #94a8bd;
  font-size: 12px;
}

.inspector-control-row:first-child {
  margin-top: 0;
}

.inspector-control-row:last-child {
  margin-bottom: 0;
}

.inspector-control-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.inspector-control-label[title] {
  cursor: help;
}

.inspector-control-value {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.inspector-control-row.is-wrap .inspector-control-value {
  flex-wrap: wrap;
  row-gap: 6px;
}

/* flowgate.v02.0052 T#2 — in a narrow block (the 264px Inspector) the label sits above its control,
   so the control keeps its width instead of running past the block. */
@container (max-width: 320px) {
  .inspector-control-row {
    grid-template-columns: minmax(0, 1fr);
    gap: 4px;
  }

  .inspector-control-value {
    flex-wrap: wrap;
  }
}
</style>
