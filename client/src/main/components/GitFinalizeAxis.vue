<template>
  <!-- 0331 T0006 / NR0005 §8: the ONE axis control shared by every finalize
       surface (document panel · AC approval dialog). The approved v4 mockup replaces
       the flat card list with two independent axes — reflect scope (merge → commit →
       wait) × push to remote — which is what lets 6 actions fit in the space 4
       used to take. The scope×push → action mapping comes from the server
       (`state.action_axes.matrix`), so the three surfaces can never drift apart
       and adding an action never means editing three templates. -->
  <div class="gf-axis" :class="{ 'gf-axis--compact': compact }">
    <div class="gf-axis-row">
      <div class="gf-axis-group">
        <div class="gf-axis-label">
          <AppIcon name="arrows-left-right" />
          {{ t('main.git_finalize.axis.scope_label') }}
        </div>
        <div class="gf-axis-options">
          <label
            v-for="s in scopes"
            :key="s"
            class="gf-axis-opt"
            :class="{ sel: scope === s, 'is-disabled': disabled }"
            :data-scope="s"
          >
            <input
              type="radio"
              :name="`${name}-scope`"
              :value="s"
              :checked="scope === s"
              :disabled="disabled"
              @change="selectScope(s)"
            />
            <span>{{ scopeLabel(s) }}</span>
          </label>
        </div>
      </div>
      <div class="gf-axis-group">
        <div class="gf-axis-label">
          <AppIcon name="cloud-arrow-up" />
          {{ t('main.git_finalize.axis.push_label') }}
        </div>
        <label class="gf-axis-push-chk" :class="{ sel: push, 'is-disabled': disabled }">
          <input type="checkbox" :checked="push" :disabled="disabled" @change="togglePush" />
          <AppIcon name="cloud-arrow-up" />
          <span>{{ t('main.git_finalize.axis.push') }}</span>
        </label>
      </div>
    </div>
    <p class="gf-axis-summary">
      <AppIcon name="info" />
      <span class="gf-axis-summary-text">{{ summary }}</span>
    </p>
  </div>
</template>

<script setup lang="ts">
import AppIcon from '@shared/AppIcon.vue'
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  actionOfPosition,
  positionOfAction,
  type FinalizeAxes,
} from '../composables/finalizeAxis'

const props = defineProps<{
  /** The finalize action currently chosen (server vocabulary). */
  modelValue: string
  /** `state.action_axes` straight from GET …/git/finalize. */
  axes: FinalizeAxes | null | undefined
  /** Radio-group name — must be unique per surface on the page. */
  name: string
  disabled?: boolean
  /** Tighter spacing for the approval dialog. */
  compact?: boolean
}>()
const emit = defineEmits<{ 'update:modelValue': [string] }>()

const { t } = useI18n()

const scopes = computed(() => props.axes?.scopes ?? [])
const position = computed(() => positionOfAction(props.axes, props.modelValue))
const scope = computed(() => position.value.scope)
const push = computed(() => position.value.push)

const summary = computed(() => t(`main.git_finalize.axis_summary.${props.modelValue}`))

function scopeLabel(s: string): string {
  return t(`main.git_finalize.axis.scope.${s}`)
}

function selectScope(s: string) {
  if (props.disabled) return
  emit('update:modelValue', actionOfPosition(props.axes, s, push.value, props.modelValue))
}
function togglePush(e: Event) {
  if (props.disabled) return
  const wantPush = (e.target as HTMLInputElement).checked
  emit('update:modelValue', actionOfPosition(props.axes, scope.value, wantPush, props.modelValue))
}
</script>

<style scoped>
.gf-axis {
  margin-bottom: 12px;
}
.gf-axis-row {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 14px 28px;
}
.gf-axis-group {
  flex: 0 1 auto;
  min-width: 0;
}
.gf-axis-label {
  display: flex;
  align-items: center;
  gap: 5px;
  margin-bottom: 7px;
  color: var(--text-m);
  font-size: 0.68rem;
  font-weight: 700;
  letter-spacing: 0.05em;
  text-transform: uppercase;
}
.gf-axis-options {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.gf-axis-opt,
.gf-axis-push-chk {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 15px;
  border: 1.5px solid var(--border);
  border-radius: 20px;
  background: var(--surface);
  color: var(--text-s);
  font-size: 0.8rem;
  font-weight: 600;
  white-space: nowrap;
  cursor: pointer;
  transition: all var(--tr);
}
.gf-axis-opt input,
.gf-axis-push-chk input {
  margin: 0;
}
.gf-axis-opt input {
  accent-color: var(--primary);
}
.gf-axis-push-chk input {
  accent-color: var(--success);
}
.gf-axis-opt:hover {
  border-color: var(--primary);
}
.gf-axis-opt.sel {
  border-color: var(--primary);
  background: var(--primary-l);
  color: var(--primary);
}
.gf-axis-push-chk:hover {
  border-color: var(--success);
}
.gf-axis-push-chk.sel {
  border-color: var(--success);
  background: var(--success-l);
  color: #15803d;
}
.gf-axis-opt.is-disabled,
.gf-axis-push-chk.is-disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.gf-axis-summary {
  display: flex;
  align-items: flex-start;
  gap: 7px;
  margin: 12px 0 0;
  padding: 9px 12px;
  border-radius: var(--r);
  background: var(--surface-h);
  color: var(--text-s);
  font-size: 0.78rem;
  line-height: 1.6;
  overflow-wrap: anywhere;
}
.gf-axis-summary :deep(svg) {
  flex: 0 0 auto;
  margin-top: 2px;
  color: var(--primary);
}
.gf-axis--compact .gf-axis-row {
  gap: 12px 20px;
}
.gf-axis--compact .gf-axis-summary {
  margin-top: 10px;
}
/* 390px: the two axes stack instead of squeezing the pills (NR0005 §10). */
@media (max-width: 480px) {
  .gf-axis-row {
    flex-direction: column;
    align-items: stretch;
    gap: 12px;
  }
}
</style>
