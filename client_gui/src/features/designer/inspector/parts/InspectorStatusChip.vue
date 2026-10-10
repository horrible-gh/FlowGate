<template>
  <!-- flowgate.v02.0048 T#1 — Inspector status chip (D#1 §2.2 parts): one short state label with a
       tone. The feature maps its own value to the label and tone; this part only shows them.
       flowgate.v02.0051 — the v7 pill (0035 insp-v4 `.pill` / `.status-pill`): seven tones (the five
       0048 tones plus `running` (purple) and `hold` (steel)), an optional leading ● dot, an optional
       sub text after the label ("· 자동", "2/4") and a hover title (the reason behind the state). -->
  <span
    class="inspector-status-chip"
    :class="[`inspector-status-chip--${tone}`, { 'inspector-status-chip--dot': dot }]"
    :title="title"
    data-test="inspector-status-chip"
    :data-tone="tone"
  >{{ label }}<span v-if="sub" class="inspector-status-chip-sub" data-test="inspector-status-chip-sub">· {{ sub }}</span></span>
</template>

<script setup lang="ts">
export type InspectorChipTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger' | 'running' | 'hold'

withDefaults(
  defineProps<{
    label: string
    tone?: InspectorChipTone
    /** Short text after the label ("자동", "2/4"); shown as `· sub`. */
    sub?: string | null
    /** Leading ● dot (the header state pill). */
    dot?: boolean
    /** Hover text: the reason behind the state (a rejection reason, "자동 승인 — 전체 실행"). */
    title?: string
  }>(),
  { tone: 'neutral', sub: null, dot: false, title: undefined },
)
</script>

<style scoped>
.inspector-status-chip {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 5px;
  max-width: 100%;
  height: 20px;
  padding: 0 8px;
  overflow: hidden;
  border: 1px solid #2c4560;
  border-radius: var(--fg2-control-radius-pill);
  background: #14243a;
  color: #8ea4bb;
  font-size: 11px;
  font-weight: 700;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.inspector-status-chip--dot::before {
  content: '';
  flex: 0 0 auto;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}

.inspector-status-chip-sub {
  font-weight: 500;
  opacity: 0.85;
}

.inspector-status-chip--info {
  border-color: #2d5f9b;
  background: #10294a;
  color: #8cc0ff;
}

.inspector-status-chip--running {
  border-color: #5e4b9e;
  background: #201a3c;
  color: #c9b6ff;
}

.inspector-status-chip--warning {
  border-color: #7b5a17;
  background: #3b2a0d;
  color: #ffc75d;
}

.inspector-status-chip--danger {
  border-color: #71313d;
  background: #3a1c24;
  color: #ff9eae;
}

.inspector-status-chip--success {
  border-color: #246c53;
  background: #0d2b22;
  color: #72e4b8;
}

.inspector-status-chip--hold {
  border-color: #315271;
  background: #172b42;
  color: #9fc4e8;
}
</style>
