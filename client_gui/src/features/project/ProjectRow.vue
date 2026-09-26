<template>
  <li
    class="project-row"
    :class="{ 'is-selected': selected, 'is-current': current }"
    :data-project-id="project.id"
  >
    <button
      type="button"
      class="project-row-main"
      :aria-pressed="selected"
      :aria-current="current ? 'true' : undefined"
      @click="emit('select', project)"
      @dblclick="emit('open', project)"
    >
      <span class="project-row-color" :style="{ background: rowColor }" aria-hidden="true"></span>
      <span class="project-row-select-mark" aria-hidden="true">{{ selected ? '✓' : '' }}</span>
      <span class="project-row-info">
        <span class="project-row-name-line">
          <span class="project-row-name">{{ project.name }}</span>
          <Badge v-if="current" tone="success" size="sm">현재</Badge>
        </span>
        <span v-if="project.description" class="project-row-desc">{{ project.description }}</span>
      </span>
      <span v-if="project.updatedAt" class="project-row-updated">최종 수정 {{ formattedUpdatedAt }}</span>
    </button>
    <Button type="button" variant="secondary" size="sm" class="project-row-open" @click="emit('open', project)">열기</Button>
  </li>
</template>

<script setup lang="ts">
// One Project row inside ProjectList (WP0005 T#2 §2.1/§2.3). Purely presentational: it
// never decides what "select" or "open" means (that is ProjectDialog's job) and never
// touches the store itself. `selected` (Dialog-local candidate) and `current` (store's
// actual current Project) are two independent, non-color-only signals — a text Badge for
// current and a visible checkmark plus aria-pressed for selected (D0004 §2.3/§3.3, T#2
// §8) — so neither state is ever expressed by border/background color alone.
//
// The row itself is not a native interactive element (a <li>, not a <button>, and not
// role="option"/"listbox"): ProjectList renders a plain <ul>, so this <li> carries no ARIA
// widget role that constrains it to a single focusable descendant. The selectable area and
// the explicit "열기" action are two sibling native <button>s (the latter the shared Button
// primitive, D0004 §1.3/§4.2 "일반 액션 → Button/IconButton"), never one nested inside the
// other, and each is independently Tab/Enter/Space-operable (T#2 §8 "row 내부 interactive
// element를 잘못 중첩하지 않는다" and 접근성 조건 — a role=option li with two native buttons
// inside it would both flatten under assistive tech and not match the single-focus/arrow-key
// contract a listbox widget declares). Selection state itself is exposed as aria-pressed on
// the one focusable control the state actually belongs to (project-row-main), not on the
// non-interactive <li>.
import { computed } from 'vue'
import Badge from '../../components/ui/Badge.vue'
import Button from '../../components/ui/Button.vue'
import type { Project } from './types'

const props = defineProps<{
  project: Project
  selected: boolean
  current: boolean
}>()
const emit = defineEmits<{ select: [project: Project]; open: [project: Project] }>()

// "최종 수정" only — never framed as "최근 사용/최근 열기" (D0004 §2.3, T#2 §3). A computed,
// not a plain value read once at setup: ProjectList's keyed rows are reused across a list
// refresh (e.g. right after this Project is created), so the same ProjectRow instance can
// receive a new `updatedAt` on an existing prop object -- a value captured once here would
// go stale and never reflect it.
const formattedUpdatedAt = computed(() => props.project.updatedAt?.slice(0, 10) ?? '')

// Project 식별 색상 (D0004 §2.3): purely a supplementary identity cue alongside the name —
// never the only signal for anything (selected/current stay Badge+checkmark, per §8). A
// Project's `color` is nullable at the model level, so a neutral fallback swatch keeps the
// dot from disappearing or rendering as `background: null`.
const FALLBACK_COLOR = '#64748b'
const rowColor = computed(() => props.project.color ?? FALLBACK_COLOR)
</script>

<style scoped>
.project-row {
  display: flex;
  align-items: stretch;
  gap: 8px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius);
  background: var(--fg2-control-surface-flat);
}

.project-row.is-selected {
  border-color: var(--fg2-focus);
}

.project-row-main {
  flex: 1;
  min-width: 0;
  display: grid;
  grid-template-columns: 10px 18px minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border: 0;
  background: none;
  color: var(--fg2-text);
  text-align: left;
  cursor: pointer;
}

.project-row-main:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: -2px;
}

.project-row-color {
  width: 10px;
  height: 10px;
  border-radius: 50%;
}

.project-row-select-mark {
  color: var(--fg2-focus);
  font-weight: 700;
}

.project-row-info {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.project-row-name-line {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.project-row-name {
  font-size: 14px;
  font-weight: 650;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.project-row-desc {
  color: var(--fg2-muted);
  font-size: 12px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.project-row-updated {
  color: var(--fg2-muted-2);
  font-size: 11px;
  white-space: nowrap;
}

.project-row-open {
  flex: none;
  align-self: center;
  margin-right: 10px;
}
</style>
