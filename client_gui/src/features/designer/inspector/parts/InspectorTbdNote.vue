<template>
  <!-- flowgate.v02.0051 — the v7 "not decided yet" marks (0035 insp.css `.tbd-box` / `.tbd-inline`):
       `box` is the framed note of a card the mockup left empty (설계: "설명 대기"), `tag` the small
       pill next to an item still under discussion (확인 용도: "토론 중"). Display only: the feature
       says what is undecided; nothing here decides it (0051 turn 7: 설계·확인 카드의 미확정 기능은
       임의로 확정하지 않음). -->
  <span v-if="kind === 'tag'" class="inspector-tbd-tag" role="note" data-test="inspector-tbd" data-kind="tag" :title="title ?? undefined">{{ label }}</span>
  <div v-else class="inspector-tbd-box" role="note" data-test="inspector-tbd" data-kind="box">
    <b v-if="title" class="inspector-tbd-title">{{ title }}</b>
    <span v-if="label" class="inspector-tbd-text">{{ label }}</span>
    <slot />
  </div>
</template>

<script setup lang="ts">
withDefaults(
  defineProps<{
    kind?: 'box' | 'tag'
    /** The note text (box) or the pill text (tag). */
    label?: string | null
    /** The bold first line (box) or the hover text (tag). */
    title?: string | null
  }>(),
  { kind: 'box', label: null, title: null },
)
</script>

<style scoped>
.inspector-tbd-box {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  padding: 12px 14px;
  border: 1px dashed #6b5a2a;
  border-radius: 8px;
  background: #141208;
  color: #d7c9a0;
  font-size: 12.5px;
  line-height: 1.6;
}

.inspector-tbd-title {
  color: #ffd27a;
}

.inspector-tbd-text {
  overflow-wrap: anywhere;
}

.inspector-tbd-tag {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  padding: 0 7px;
  border: 1px solid #7b5a17;
  border-radius: 999px;
  background: #3b2a0d;
  color: #ffd27a;
  font-size: 10.5px;
  font-weight: 800;
  white-space: nowrap;
  cursor: help;
}
</style>
