<template>
  <div v-if="visible" class="wp-ai-scope" role="dialog" aria-modal="true">
    <div class="wp-ai-scope-card">
      <header>
        <h3>{{ t('main.work_plan.ai_scope_title') }}</h3>
        <p>{{ t('main.work_plan.ai_scope_intro') }}</p>
      </header>

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

      <footer>
        <button type="button" class="btn btn-secondary" @click="emit('close')">{{ t('common.cancel') }}</button>
        <button type="button" class="btn btn-outline" :disabled="providerIds.size === 0 || busy" @click="emitScope('project-map')">
          {{ t('main.work_plan.ai_scope_project_map') }}
        </button>
        <button type="button" class="btn btn-primary" :disabled="providerIds.size === 0 || busy" @click="emitScope('ai')">
          {{ t('main.work_plan.ai_scope_delegate') }}
        </button>
      </footer>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

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
.wp-ai-scope { position:absolute; inset:0; z-index:30; display:flex; align-items:center; justify-content:center; padding:18px; background:rgba(15,23,42,.38); }
.wp-ai-scope-card { width:min(760px,100%); max-height:calc(100% - 24px); overflow:auto; padding:18px; border:1px solid var(--border-d); border-radius:var(--r); background:var(--surface); box-shadow:0 16px 40px rgba(15,23,42,.22); }
header h3 { margin:0; font-size:1rem; }
header p { margin:5px 0 14px; color:var(--text-m); font-size:.76rem; }
section { margin-top:12px; }
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
