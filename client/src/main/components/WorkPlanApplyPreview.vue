<template>
  <div v-if="visible" ref="rootRef" class="wpa-overlay" :class="'wpa-layout-' + layoutTier" @click.self="emit('close')">
    <section class="wpa-panel" role="dialog" aria-modal="true">
      <header class="wpa-head">
        <div>
          <strong>{{ t('main.work_plan_apply.title') }}</strong>
          <p v-if="preview && preview.wp_review_status !== 'approved'" class="wpa-review-note">{{ t('main.work_plan_apply.review_pending') }}</p>
        </div>
        <div class="wpa-mode">
          <label><input v-model="instructionMode" type="radio" value="auto_approved" />{{ t('main.continuous_work.instruction_mode_auto') }}</label>
          <label><input v-model="instructionMode" type="radio" value="ai_direct" />{{ t('main.continuous_work.instruction_mode_ai') }}</label>
        </div>
      </header>
      <div class="wpa-body">
        <div v-if="loading" class="wpa-state">{{ t('common.loading') }}</div>
        <div v-else-if="error" class="wpa-state wpa-error">
          <p>{{ error }}</p>
          <button class="btn btn-outline btn-sm" type="button" @click="loadPreview">{{ t('main.work_plan_apply.retry') }}</button>
        </div>
        <template v-else-if="preview">
          <section class="wpa-section">
            <h4>{{ t('main.work_plan_apply.comparison') }}</h4>
            <div class="wpa-summary-grid">
              <div><strong>{{ preview.comparison.kept.count }}</strong><span>{{ t('main.work_plan_apply.kept') }}</span></div>
              <div><strong>{{ preview.comparison.added.count }}</strong><span>{{ t('main.work_plan_apply.added') }}</span></div>
              <div><strong>{{ preview.comparison.not_deleted.count }}</strong><span>{{ t('main.work_plan_apply.not_deleted') }}</span></div>
            </div>
            <ul v-if="preview.comparison.added.items.length" class="wpa-compact-list">
              <li v-for="row in preview.comparison.added.items" :key="row.position + '-' + row.type">
                {{ row.position }} · {{ row.type }} · {{ row.label }}
              </li>
            </ul>
          </section>
          <section class="wpa-section">
            <h4>{{ t('main.work_plan_apply.assignments') }}</h4>
            <table v-if="layoutTier !== 'narrow'" class="wpa-table">
              <thead><tr>
                <th>{{ t('main.work_plan_apply.plan_step') }}</th>
                <th>{{ t('main.work_plan_apply.type') }}</th>
                <th>{{ t('main.work_plan_apply.destination') }}</th>
                <th>{{ t('main.work_plan_apply.provider') }}</th>
                <th v-if="layoutTier === 'wide'">{{ t('main.work_plan_apply.note') }}</th>
              </tr></thead>
              <tbody>
                <tr v-for="row in preview.step_map" :key="row.key">
                  <td>{{ row.key }}</td>
                  <td><span class="doc-tag" :class="'c-' + row.type">{{ row.type }}</span></td>
                  <td>{{ destination(row) }}</td>
                  <td>{{ providerText(row) }}</td>
                  <td v-if="layoutTier === 'wide'">{{ noteText(row) }}</td>
                </tr>
              </tbody>
            </table>
            <div v-else class="wpa-cards">
              <article v-for="row in preview.step_map" :key="row.key" class="wpa-card">
                <div><strong>{{ row.key }}</strong><span class="doc-tag" :class="'c-' + row.type">{{ row.type }}</span><b>{{ destination(row) }}</b></div>
                <p>{{ providerText(row) }}</p><p>{{ noteText(row) }}</p>
              </article>
            </div>
          </section>
          <section class="wpa-section">
            <h4>{{ t('main.work_plan_apply.warnings') }}</h4>
            <p v-if="preview.warnings.length === 0" class="wpa-empty">{{ t('main.work_plan_apply.no_warnings') }}</p>
            <details v-for="warning in preview.warnings" :key="warning.code" class="wpa-warning">
              <summary>{{ warningText(warning) }} ({{ warning.count }})</summary>
              <div v-if="warning.keys.length || warning.item_seqs.length" class="wpa-warning-details">
                <span v-for="key in warning.keys" :key="key">{{ key }}</span>
                <span v-for="seq in warning.item_seqs" :key="'s-' + seq">#{{ seq }}</span>
              </div>
            </details>
          </section>
          <section class="wpa-section wpa-target">
            <h4>{{ t('main.work_plan_apply.target') }}</h4>
            <strong v-if="preview.fill_preview.target_seq != null">
              {{ preview.fill_preview.target_key ?? preview.fill_preview.target_label }} · #{{ preview.fill_preview.target_seq }}
            </strong>
            <span v-else>{{ t('main.work_plan_apply.no_target') }}</span>
          </section>
        </template>
      </div>
      <footer class="wpa-footer">
        <button class="btn btn-ghost" type="button" :disabled="applying" @click="emit('close')">{{ t('common.cancel') }}</button>
        <div class="wpa-action">
          <button data-test="work-plan-fill-only" class="btn btn-outline" type="button" :disabled="!canApplyWithoutWorkflow || applying" @click="applyPlan(false)">{{ t('main.work_plan_apply.fill_only') }}</button>
          <small v-if="preview && !canApplyWithoutWorkflow" class="wpa-disabled-reason">{{ blockerText(false) }}</small>
        </div>
        <div v-if="preview?.can_change_workflow" class="wpa-action">
          <button data-test="work-plan-fit-and-fill" class="btn btn-primary" type="button" :disabled="!canApplyWithWorkflow || applying" @click="applyPlan(true)">{{ t('main.work_plan_apply.fit_and_fill') }}</button>
          <small v-if="!canApplyWithWorkflow" class="wpa-disabled-reason">{{ blockerText(true) }}</small>
        </div>
      </footer>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { postRequest } from '@shared/api'
import { useContentLayoutTier } from '../composables/useContentLayoutTier'
import type { WorkPlanFillPreset, WorkPlanWarning } from '../types/workPlanFillPreset'

interface StepMapRow {
  key: string; type: string; matched: boolean; item_seq: number | null
  position_after_apply: number | null; status: string
}
interface PreviewResponse {
  wp_revision_no: number; wp_review_status: string; instruction_mode: 'auto_approved' | 'ai_direct'
  workflow: { owner_doc_id: string; workflow_tag: string }
  comparison: {
    kept: { count: number; done_count: number }
    added: { count: number; items: { position: number; type: string; label: string }[] }
    not_deleted: { count: number; items: number[] }
  }
  step_map: StepMapRow[]
  fill_preview: {
    target_seq: number | null; target_key: string | null; target_label: string | null
    provider_overrides: Record<string, string>; note_overrides: Record<string, string>
    folded: { from_key: string; to_key: string | null; to_item_seq: number }[]
  }
  warnings: WorkPlanWarning[]
  can_apply: boolean
  can_apply_without_workflow: boolean
  can_apply_with_workflow: boolean
  can_change_workflow: boolean
  apply_blockers: { keep_workflow: string | null; change_workflow: string | null }
}

const props = defineProps<{ visible: boolean; docId: string }>()
const emit = defineEmits<{
  close: []
  applied: [payload: { preset: WorkPlanFillPreset; ownerDocId: string }]
}>()
const { t } = useI18n()
const rootRef = ref<HTMLElement | null>(null)
const { layoutTier } = useContentLayoutTier(rootRef)
const instructionMode = ref<'auto_approved' | 'ai_direct'>('auto_approved')
const preview = ref<PreviewResponse | null>(null)
const loading = ref(false)
const applying = ref(false)
const error = ref('')
const canApplyWithoutWorkflow = computed(() => !!preview.value?.can_apply_without_workflow)
const canApplyWithWorkflow = computed(() => !!preview.value?.can_apply_with_workflow)

async function loadPreview() {
  if (!props.visible) return
  loading.value = true
  error.value = ''
  try {
    const res = await postRequest<PreviewResponse>(
      '/api/v1/documents/' + encodeURIComponent(props.docId) + '/work-plan/apply/preview',
      { instruction_mode: instructionMode.value },
    )
    preview.value = res.data
  } catch (e: any) {
    preview.value = null
    error.value = e?.response?.data?.message || e?.response?.data?.detail || t('main.work_plan_apply.load_failed')
  } finally {
    loading.value = false
  }
}

function numericMap(values: Record<string, string>): Record<number, string> {
  return Object.fromEntries(Object.entries(values ?? {}).map(([key, value]) => [Number(key), value]))
}

async function applyPlan(changeWorkflow: boolean) {
  const allowed = changeWorkflow ? canApplyWithWorkflow.value : canApplyWithoutWorkflow.value
  if (!preview.value || !allowed) return
  applying.value = true
  error.value = ''
  try {
    const res = await postRequest<any>(
      '/api/v1/documents/' + encodeURIComponent(props.docId) + '/work-plan/apply',
      {
        instruction_mode: instructionMode.value,
        change_workflow: changeWorkflow,
        workflow_tag: preview.value.workflow.workflow_tag,
        wp_revision_no: preview.value.wp_revision_no,
      },
    )
    const fill = res.data.fill
    emit('applied', {
      ownerDocId: res.data.workflow.owner_doc_id,
      preset: {
        sourceDocId: fill.source_doc_id,
        sourceRevisionNo: fill.source_revision_no,
        instructionMode: fill.instruction_mode,
        targetSeq: fill.target_seq,
        providerOverrides: numericMap(fill.provider_overrides),
        messageOverrides: numericMap(fill.note_overrides),
        defaultMessage: fill.default_note ?? '',
        filledSeqs: fill.filled_item_seqs ?? [],
        warnings: res.data.warnings ?? [],
      },
    })
  } catch (e: any) {
    const data = e?.response?.data
    error.value = data?.message || data?.detail || t('main.work_plan_apply.apply_failed')
    if (e?.response?.status === 409) await loadPreview()
  } finally {
    applying.value = false
  }
}

function folded(row: StepMapRow) {
  return preview.value?.fill_preview.folded.find(item => item.from_key === row.key)
}
function destination(row: StepMapRow): string {
  const fold = folded(row)
  if (fold) return (fold.to_key ?? '') + ' · #' + fold.to_item_seq
  const seq = row.item_seq ?? row.position_after_apply
  return seq == null ? '—' : '#' + seq
}
function providerText(row: StepMapRow): string {
  if (row.type === 'TSR') return t('main.work_plan_apply.server_assembled')
  const fold = folded(row)
  if (fold) return t('main.work_plan_apply.folded_into', { key: fold.to_key ?? '#' + fold.to_item_seq })
  const seq = row.item_seq ?? row.position_after_apply
  return (seq != null && preview.value?.fill_preview.provider_overrides[String(seq)]) || t('main.work_plan_apply.unassigned')
}
function noteText(row: StepMapRow): string {
  if (row.type === 'TSR') return t('main.work_plan_apply.not_entered')
  const seq = folded(row)?.to_item_seq ?? row.item_seq ?? row.position_after_apply
  return (seq != null && preview.value?.fill_preview.note_overrides[String(seq)]) || '—'
}
function warningText(warning: WorkPlanWarning): string {
  const key = 'main.work_plan_apply.warning_' + warning.code
  const translated = t(key, { n: warning.count })
  return translated === key ? (warning.message || warning.code) : translated
}

function blockerText(changeWorkflow: boolean): string {
  const code = changeWorkflow
    ? preview.value?.apply_blockers.change_workflow
    : preview.value?.apply_blockers.keep_workflow
  if (!code || code === 'no_target') return t('main.work_plan_apply.no_target')
  const key = 'main.work_plan_apply.warning_' + code
  const translated = t(key, { n: 1 })
  return translated === key ? code : translated
}

watch(() => [props.visible, instructionMode.value] as const, ([visible]) => {
  if (visible) void loadPreview()
}, { immediate: true })
</script>

<style scoped>
.wpa-overlay { position:absolute; inset:48px 0 0; z-index:55; padding:12px; background:rgba(15,23,42,.42); display:flex; }
.wpa-panel { width:100%; min-height:0; background:var(--surface,#fff); border-radius:var(--r,8px); display:flex; flex-direction:column; overflow:hidden; }
.wpa-head,.wpa-footer { flex:0 0 auto; display:flex; align-items:center; justify-content:space-between; gap:12px; padding:10px 14px; border-bottom:1px solid var(--border); }
.wpa-footer { border-top:1px solid var(--border); border-bottom:0; justify-content:flex-end; align-items:flex-start; }
.wpa-action { display:flex; flex-direction:column; align-items:flex-end; gap:4px; max-width:240px; }
.wpa-disabled-reason { color:var(--text-m); font-size:.68rem; line-height:1.3; text-align:right; }
.wpa-head p { margin:3px 0 0; }.wpa-review-note { color:#b45309; font-size:.74rem; }
.wpa-mode { display:flex; gap:12px; font-size:.76rem; }.wpa-mode label { display:flex; gap:4px; align-items:center; }
.wpa-body { flex:1; min-height:0; overflow:auto; padding:12px 14px; display:grid; gap:12px; align-content:start; }
.wpa-section { border:1px solid var(--border); border-radius:var(--r-sm,5px); padding:10px; }.wpa-section h4 { margin:0 0 8px; font-size:.78rem; }
.wpa-summary-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:8px; }.wpa-summary-grid div { background:var(--surface-h); padding:8px; border-radius:4px; display:flex; gap:6px; align-items:baseline; font-size:.72rem; }
.wpa-summary-grid strong { font-size:1rem; }.wpa-compact-list { margin:8px 0 0; font-size:.72rem; }
.wpa-table { width:100%; border-collapse:collapse; font-size:.74rem; }.wpa-table th,.wpa-table td { text-align:left; padding:6px; border-bottom:1px solid var(--border); }
.wpa-cards { display:grid; gap:7px; }.wpa-card { border:1px solid var(--border); border-radius:5px; padding:8px; }.wpa-card div { display:flex; align-items:center; gap:8px; }.wpa-card b { margin-left:auto; }.wpa-card p { margin:5px 0 0; font-size:.72rem; }
.wpa-warning { padding:5px 0; font-size:.74rem; }.wpa-warning-details { display:flex; flex-wrap:wrap; gap:5px; padding:6px 0; }.wpa-warning-details span { background:var(--surface-h); border-radius:99px; padding:2px 7px; }
.wpa-state { margin:auto; text-align:center; }.wpa-error { color:var(--danger); }.wpa-target strong { color:var(--primary); }
.wpa-layout-narrow .wpa-summary-grid { grid-template-columns:1fr; }
</style>
