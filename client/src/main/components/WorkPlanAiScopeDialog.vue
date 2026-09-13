<template>
  <!-- flowgate.default.0560 T0018 (4순위, NR0011 원장 ID 32) on the common dialog layer;
       D0008 maps this instance to `compact`.
       T0018 §2.3-7: this was one of the four overlays that never used Teleport — it was
       `position: absolute; inset: 0; z-index: 30` inside the WorkPlanEditor panel, so it
       dimmed that panel and nothing else. L0009 §2 "Teleport" sends every common dialog to
       one host, so it is a viewport-wide overlay from here on. That change is accepted
       rather than worked around: this dialog is already `aria-modal`, it blocks the whole
       editor while it is up, and a per-instance containment surface would be a D0008/L0009
       change, which this T does not authorise. The before/after coordinates are measured in
       `tests/browser/dialog-local-overlay-geometry.0560.mjs`.
       `:close-on-backdrop="false"` is explicit (T0018 §2.2-3): `compact` defaults to `true`,
       but NR0011 records this overlay as BD=X today.
       `size="lg"` keeps the 720px-class card this dialog measured (`min(760px, 100%)`). -->
  <DialogShell
    :open="visible"
    variant="compact"
    size="lg"
    surface-class="work-plan-ai-scope-dialog"
    :close-on-backdrop="false"
    @request-close="emit('close')"
  >
    <template #header>
      <DialogHeader
        :title="t('main.work_plan.ai_scope_title')"
        :subtitle="t('main.work_plan.ai_scope_intro')"
        @close="emit('close')"
      />
    </template>

    <template #default>
      <section>
        <div class="scope-heading">
          <strong>{{ t('main.work_plan.ai_scope_quantities') }}</strong>
          <span><button type="button" @click="selectAllQuantities">{{ t('main.work_plan.ai_scope_all') }}</button><button type="button" @click="quantityCodes.clear()">{{ t('main.work_plan.ai_scope_clear') }}</button></span>
        </div>
        <div class="scope-grid">
          <label v-for="item in countableTypes" :key="item.code">
            <input v-model="quantityCodes" type="checkbox" :value="item.code" />
            <span class="doc-tag" :class="`c-${item.code}`">{{ item.code }}</span> {{ item.label }}
          </label>
        </div>
      </section>

      <section>
        <div class="scope-heading">
          <strong>{{ t('main.work_plan.ai_scope_steps') }}</strong>
          <span><button type="button" @click="selectAllSteps">{{ t('main.work_plan.ai_scope_all') }}</button><button type="button" @click="stepKeys.clear()">{{ t('main.work_plan.ai_scope_clear') }}</button></span>
        </div>
        <div class="scope-step-list">
          <label v-for="(step, idx) in steps" :key="step.key" :class="{ locked: step.locked }">
            <input v-model="stepKeys" type="checkbox" :value="step.key" :disabled="step.locked" />
            {{ t('main.work_plan.step_no', { n: idx + 1 }) }} ·
            <span class="doc-tag" :class="`c-${step.type}`">{{ step.type }}</span>
            {{ step.label }}
            <small v-if="step.locked">{{ t('main.work_plan.locked_note') }}</small>
          </label>
        </div>
      </section>

      <section>
        <div class="scope-heading">
          <strong>{{ t('main.work_plan.ai_scope_providers') }}</strong>
          <span><button type="button" @click="selectAllProviders">{{ t('main.work_plan.ai_scope_all') }}</button><button type="button" @click="providerIds.clear()">{{ t('main.work_plan.ai_scope_clear') }}</button></span>
        </div>
        <div class="scope-grid">
          <label v-for="provider in candidates" :key="provider.provider_id">
            <input v-model="providerIds" type="checkbox" :value="provider.provider_id" />
            {{ provider.display_name ?? provider.provider_id }}
          </label>
        </div>
      </section>

    </template>

    <template #footer>
      <DialogFooter :actions="scopeActions" />
    </template>
  </DialogShell>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import DialogFooter from './dialogs/DialogFooter.vue'
import DialogHeader from './dialogs/DialogHeader.vue'
import DialogShell from './dialogs/DialogShell.vue'
import type { DialogAction } from './dialogs/dialogTypes'

export interface WorkPlanScope {
  quantity_type_codes: string[]
  step_keys: string[]
  provider_ids: string[]
}

const props = defineProps<{
  visible: boolean
  busy?: boolean
  countableTypes: { code: string; label: string }[]
  steps: { key: string; type: string; label: string; provider_id: string | null; locked: boolean }[]
  candidates: { provider_id: string; display_name: string | null }[]
}>()
const emit = defineEmits<{
  close: []
  'project-map': [scope: WorkPlanScope]
  ai: [scope: WorkPlanScope]
}>()
const { t } = useI18n()
const quantityCodes = ref(new Set<string>())
const stepKeys = ref(new Set<string>())
const providerIds = ref(new Set<string>())

function reset() {
  quantityCodes.value = new Set()
  stepKeys.value = new Set(props.steps.filter((step) => !step.locked && !step.provider_id).map((step) => step.key))
  providerIds.value = new Set(props.candidates.map((provider) => provider.provider_id))
}
watch(() => props.visible, (visible) => { if (visible) reset() }, { immediate: true })

function selectAllQuantities() {
  quantityCodes.value = new Set(props.countableTypes.map((item) => item.code))
}
function selectAllSteps() {
  stepKeys.value = new Set(props.steps.filter((step) => !step.locked).map((step) => step.key))
}
function selectAllProviders() {
  providerIds.value = new Set(props.candidates.map((provider) => provider.provider_id))
}
/**
 * `[프로젝트맵] [취소] [AI 위임]`.
 *
 * The painted order changes here, and that is the point: this footer used to read
 * `[취소] [프로젝트맵] [위임]`, with cancel stranded away from the primary button — one of
 * the arrangements R0001 reported. `footerRolePriority` now sorts `aux → cancel → primary`
 * before rendering, so 취소 sits immediately left of the primary action (DS0007 / D0008 §6)
 * and no edit to this array can move it back.
 *
 * 프로젝트맵 is `aux`: it fills the plan from the project map instead of delegating, a
 * secondary helper that leaves the dialog's own job (delegate) to the primary button.
 */
const scopeActions = computed<DialogAction[]>(() => [
  {
    id: 'cancel',
    label: t('common.cancel'),
    role: 'cancel',
    onSelect: () => emit('close'),
  },
  {
    id: 'project-map',
    label: t('main.work_plan.ai_scope_project_map'),
    role: 'aux',
    disabled: providerIds.value.size === 0 || props.busy === true,
    onSelect: () => emitScope('project-map'),
  },
  {
    id: 'delegate',
    label: t('main.work_plan.ai_scope_delegate'),
    role: 'primary',
    disabled: providerIds.value.size === 0 || props.busy === true,
    onSelect: () => emitScope('ai'),
  },
])

function emitScope(kind: 'project-map' | 'ai') {
  const scope: WorkPlanScope = {
    quantity_type_codes: [...quantityCodes.value],
    step_keys: [...stepKeys.value],
    provider_ids: [...providerIds.value],
  }
  if (kind === 'ai') emit('ai', scope)
  else emit('project-map', scope)
}
</script>

<style scoped>
/* The overlay, the card, the title block and the footer row all belong to the common
   dialog layer now (T0018). Only the scope body's own controls are left here.
   `section:first-child` has no top margin because the shell's body already pads it. */
section { margin-top:12px; }
section:first-child { margin-top:0; }
.scope-heading { display:flex; align-items:center; justify-content:space-between; margin-bottom:6px; font-size:.76rem; }
.scope-heading span { display:flex; gap:4px; }
.scope-heading button { padding:2px 8px; border:1px solid var(--border); border-radius:var(--r-sm); background:var(--surface); color:var(--text-m); cursor:pointer; }
.scope-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:4px 8px; }
.scope-grid label,.scope-step-list label { display:flex; align-items:center; gap:6px; min-width:0; padding:5px 7px; border:1px solid var(--border); border-radius:var(--r-sm); font-size:.72rem; }
.scope-step-list { display:flex; flex-direction:column; gap:4px; max-height:180px; overflow-y:auto; padding-right:4px; }
.scope-step-list label.locked { background:#fbfcfe; color:var(--text-m); }
.scope-step-list small { margin-left:auto; }
footer { display:flex; justify-content:flex-end; gap:8px; margin-top:16px; padding-top:12px; border-top:1px solid var(--border); }
</style>
