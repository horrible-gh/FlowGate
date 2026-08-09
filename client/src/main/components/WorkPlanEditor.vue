<template>
  <div ref="rootRef" class="card md-preview-card wp-editor" :class="`wp-layout-${layoutTier}`">
    <div class="card-hd">
      <span class="card-title">
        <span class="doc-tag c-WP" style="font-size:.68rem; padding:2px 5px; margin-right:4px;">WP</span>
        {{ t('main.work_plan.title') }}
        <span v-if="docReviewStatus" class="wp-status-pill" :class="`wp-status-${docReviewStatus}`">
          {{ statusLabel }}
        </span>
      </span>
      <!-- Mockup xc32frrg screen 1: the card header carries only 원문 보기 / 저장.
           [AI 제안 불러오기] sits in the table toolbar and [연속 작업에 채우기] in the
           document action bar. -->
      <div class="card-actions">
        <button class="btn btn-secondary btn-sm" type="button" :disabled="loading || !!unreadable" @click="rawViewOpen = true">
          <AppIcon name="code" /> {{ t('main.work_plan.raw_view') }}
        </button>
        <button
          class="btn btn-primary btn-sm"
          type="button"
          :disabled="loading || !!unreadable || saving || isLocked"
          @click="save"
        >
          <AppIcon name="floppy-disk" /> {{ saving ? t('main.work_plan.saving') : t('main.work_plan.save') }}
        </button>
      </div>
    </div>

    <div class="card-bd wp-body">
      <div v-if="loading" class="wp-loading">{{ t('common.loading') }}</div>

      <!-- P0009 §4.5: an unreadable canonical file never gets forced into a table. -->
      <div v-else-if="unreadable" class="wp-unreadable">
        <AppIcon name="warning-circle" class="wp-unreadable-icon" />
        <p class="wp-unreadable-title">{{ t('main.work_plan.unreadable_title') }}</p>
        <p class="wp-unreadable-desc">{{ unreadable.message }}</p>
        <p class="wp-unreadable-detail">{{ unreadable.detail }}</p>
        <div v-if="unreadable.revisions?.length" class="wp-unreadable-revisions">
          <p class="wp-unreadable-revisions-title">{{ t('main.work_plan.revisions_title') }}</p>
          <ul>
            <li v-for="rev in unreadable.revisions" :key="rev.revision_no">
              r{{ rev.revision_no }} — {{ rev.created_by }} · {{ rev.created_at }}
            </li>
          </ul>
        </div>
        <pre v-if="unreadable.raw" class="wp-unreadable-raw">{{ unreadable.raw }}</pre>
      </div>

      <template v-else-if="plan">
        <!-- Mockup xc32frrg screen 1 — 표 편집 모드 띠 -->
        <div class="wp-toolbar">
          <span class="wp-mode-pill"><AppIcon name="grid-four" /> {{ t('main.work_plan.table_mode') }}</span>
          <span class="wp-mode-desc">{{ t('main.work_plan.table_mode_desc') }}</span>
          <span class="wp-toolbar-spacer"></span>
          <button
            class="btn btn-outline btn-sm"
            type="button"
            :disabled="loading || !!unreadable || aiSuggesting"
            @click="aiScopeOpen = true"
          >
            <AppIcon name="robot" /> {{ aiSuggesting ? t('main.work_plan.ai_filling') : t('main.work_plan.ai_suggest') }}
          </button>
        </div>

        <div class="wp-advisory-notice">
          <AppIcon name="lightning" />
          <span><strong>{{ t('main.work_plan.advisory_lead') }}</strong> {{ t('main.work_plan.advisory_notice') }}</span>
        </div>
        <p v-if="isLocked" class="wp-locked-hint">
          <AppIcon name="lock" /> {{ t('main.work_plan.locked_after_approval') }}
        </p>
        <p v-else-if="docReviewStatus === 'pending_review'" class="wp-review-hint">
          {{ t('main.work_plan.review_pending_hint') }}
        </p>

        <div v-if="conflict" class="wp-conflict-banner">
          <AppIcon name="warning-circle" />
          <span>{{ t('main.work_plan.save_conflict_message', { who: conflict.updatedBy ?? '?', when: conflict.updatedAt ?? '' }) }}</span>
          <button type="button" class="btn btn-outline btn-sm" @click="reload">{{ t('main.work_plan.reload') }}</button>
        </div>
        <div v-if="topLevelErrors.length" class="wp-error-banner">
          <AppIcon name="warning-circle" />
          <span>{{ topLevelErrors.join(' ') }}</span>
        </div>

        <!-- ① 수량 -->
        <section class="wp-section">
          <div class="wp-section-hd">
            <span class="wp-step-no-badge">1</span>
            <AppIcon name="hash" class="wp-section-ico" />
            <span class="wp-section-title">{{ t('main.work_plan.section_quantities_title') }}</span>
            <span class="wp-section-desc">{{ t('main.work_plan.section_quantities_desc') }}</span>
            <span class="wp-section-totals">{{ t('main.work_plan.totals_line', { design: totals.design_sheets, work: totals.work_sets }) }}</span>
          </div>
          <div class="wp-qty-grid">
            <div
              v-for="code in renderedCountedTypes"
              :key="code"
              class="wp-qty-card"
              :class="{ zero: (plan.quantities[code]?.count ?? 0) === 0 }"
            >
              <span class="wp-qty-tags">
                <span class="doc-tag" :class="`c-${code}`" style="font-size:.62rem; padding:1px 5px;">{{ code }}</span>
                <span v-if="pairOf(code)" class="doc-tag" :class="`c-${pairOf(code)}`" style="font-size:.62rem; padding:1px 5px;">{{ pairOf(code) }}</span>
              </span>
              <span class="wp-qty-body">
                <span class="wp-qty-name">{{ qtyCardName(code) }}</span>
                <span class="wp-qty-unit">{{ unitLabel(code) }}</span>
              </span>
              <span class="wp-qty-stepper">
                <button type="button" class="wp-stepper-btn" :disabled="isLocked" @click="setQuantity(code, (plan.quantities[code]?.count ?? 0) - 1)">−</button>
                <span class="wp-qty-value" :class="{ zero: (plan.quantities[code]?.count ?? 0) === 0 }">{{ plan.quantities[code]?.count ?? 0 }}</span>
                <button type="button" class="wp-stepper-btn" :disabled="isLocked" @click="setQuantity(code, (plan.quantities[code]?.count ?? 0) + 1)">+</button>
              </span>
            </div>
          </div>
        </section>

        <!-- ② 단계별 공급자 · 한줄 멘트 -->
        <section class="wp-section">
          <div class="wp-section-hd">
            <span class="wp-step-no-badge">2</span>
            <AppIcon name="users" class="wp-section-ico" />
            <span class="wp-section-title">{{ t('main.work_plan.section_steps_title') }}</span>
            <span class="wp-section-desc">{{ t('main.work_plan.section_steps_desc') }}</span>
            <span v-if="unassignedStepCount > 0" class="wp-section-missing">
              {{ t('main.work_plan.summary_unassigned', { n: unassignedStepCount }) }}
            </span>
            <span class="wp-section-totals">{{ t('main.work_plan.steps_total', { n: plan.steps.length }) }}</span>
          </div>

          <div class="wp-defaults-row">
            <span class="wp-defaults-label">{{ t('main.work_plan.defaults_label') }}</span>
            <AiProviderSelect :providers="providerOptionsWithUnassigned" :model-value="plan.defaults.provider_id ?? ''" hide-label hide-icon compact @update:model-value="(v) => { if (plan) plan.defaults.provider_id = v || null }" />
            <input v-model="plan.defaults.note" type="text" class="wp-defaults-note" :maxlength="NOTE_MAX_CHARS" :placeholder="t('main.work_plan.defaults_note_placeholder')" :disabled="isLocked" />
            <button type="button" class="btn btn-outline btn-sm" :disabled="isLocked" @click="applyDefaults">{{ t('main.work_plan.apply_to_all') }}</button>
          </div>

          <div class="wp-step-head">
            <span>{{ t('main.work_plan.col_step') }}</span><span>{{ t('main.work_plan.col_type') }}</span>
            <span>{{ t('main.work_plan.col_document') }}</span><span>{{ t('main.work_plan.col_provider') }}</span>
            <span>{{ t('main.work_plan.col_note') }}</span>
          </div>
          <div class="wp-step-list">
            <div v-if="plan.steps.length === 0" class="step-empty">{{ t('main.work_plan.empty_all_zero') }}</div>
            <div v-for="(step, idx) in plan.steps" v-else :key="step.key" class="wp-step-row" :class="{ 'is-first': isFirstOfType(step, idx), 'is-locked': step.locked, 'wp-row-error': stepErrors[step.key] }">
              <span class="wp-step-no">{{ t('main.work_plan.step_no', { n: idx + 1 }) }}</span>
              <span class="doc-tag" :class="`c-${step.type}`">{{ step.type }}</span>
              <span class="wp-step-label">{{ stepDocName(step) }} <small>{{ stepDocQuantity(step) }}</small></span>
              <select v-if="step.locked" class="prov-select" disabled><option>{{ t('main.work_plan.locked_note') }}</option></select>
              <AiProviderSelect v-else :providers="providerOptionsWithUnassigned" :model-value="step.provider_id ?? ''" hide-label hide-icon compact @update:model-value="(v) => setStepProvider(step.key, v || null)" />
              <input class="wp-step-msg" :class="{ 'is-ai': step.origin === 'ai_suggested' }" type="text" :maxlength="NOTE_MAX_CHARS" :placeholder="t('main.work_plan.note_placeholder')" :value="step.locked ? '' : (step.note ?? '')" :disabled="step.locked" @input="(e) => setStepNote(step.key, (e.target as HTMLInputElement).value)" />
            </div>
          </div>

          <!-- Mockup xc32frrg screen 1 — 범례 -->
          <div class="wp-step-legend">
            <span><span class="wp-ai-dot"></span> {{ t('main.work_plan.legend_ai') }}</span>
            <span>{{ t('main.work_plan.legend_locked') }}</span>
          </div>
        </section>

        <!-- Mockup xc32frrg screen 1 — 수량 카드 3장 -->
        <div class="wp-sum-cards">
          <div class="wp-sum-card">
            <div class="wp-sum-label"><AppIcon name="compass-tool" /> {{ t('main.work_plan.sum_design_label') }}</div>
            <div class="wp-sum-value">{{ totals.design_sheets }}<small>{{ t('main.work_plan.unit_sheet_short') }}</small></div>
            <div class="wp-sum-desc">{{ t('main.work_plan.sum_design_desc') }}</div>
          </div>
          <div class="wp-sum-card">
            <div class="wp-sum-label"><AppIcon name="stack" /> {{ t('main.work_plan.sum_work_label') }}</div>
            <div class="wp-sum-value">{{ totals.work_sets }}<small>{{ t('main.work_plan.unit_set_short') }}</small></div>
            <div class="wp-sum-desc">{{ t('main.work_plan.sum_work_desc') }}</div>
          </div>
          <div class="wp-sum-card">
            <div class="wp-sum-label"><AppIcon name="file-text" /> {{ t('main.work_plan.sum_steps_label') }}</div>
            <div class="wp-sum-value">{{ totals.steps }}<small>{{ t('main.work_plan.unit_step_short') }}</small></div>
            <div class="wp-sum-desc">{{ t('main.work_plan.sum_steps_desc') }}</div>
          </div>
        </div>
      </template>
    </div>

    <!-- Raw view overlay (read-only) — P0009 §3.4 / D0007 §6.5 -->
    <div v-if="rawViewOpen" class="wp-raw-overlay" @click.self="rawViewOpen = false" @keydown.escape="rawViewOpen = false">
      <div class="wp-raw-box">
        <div class="wp-raw-hd">
          <span>{{ t('main.work_plan.raw_view_title') }}</span>
          <div>
            <button type="button" class="btn btn-outline btn-sm" @click="copyRaw">
              <AppIcon name="copy" /> {{ t('main.work_plan.copy') }}
            </button>
            <button type="button" class="btn btn-secondary btn-sm" @click="rawViewOpen = false">
              {{ t('main.work_plan.close') }}
            </button>
          </div>
        </div>
        <pre class="wp-raw-content">{{ rawJson }}</pre>
      </div>
    </div>

    <WorkPlanApplyPreview
      :visible="applyPreviewOpen"
      :doc-id="docId"
      @close="applyPreviewOpen = false"
      @applied="onPlanApplied"
    />
    <WorkPlanAiScopeDialog
      :visible="aiScopeOpen"
      :busy="aiSuggesting"
      :countable-types="docTypeStore.countableTypes.map((item) => ({ code: item.code, label: item.label }))"
      :steps="scopeSteps"
      :candidates="plan?.provider_candidates ?? []"
      @close="aiScopeOpen = false"
      @project-map="fetchSuggestion"
      @ai="startAiFill"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getRequest, postRequest, putRequest } from '@shared/api'
import AppIcon from '@shared/AppIcon.vue'
import AiProviderSelect from './AiProviderSelect.vue'
import WorkPlanApplyPreview from './WorkPlanApplyPreview.vue'
import WorkPlanAiScopeDialog from './WorkPlanAiScopeDialog.vue'
import type { WorkPlanScope } from './WorkPlanAiScopeDialog.vue'
import { useContentLayoutTier } from '../composables/useContentLayoutTier'
import type { WorkPlanFillPreset } from '../types/workPlanFillPreset'
import { useToast } from './common/useToast'
import { useDocTypeStore } from '../stores/docTypeStore'
import { copyToClipboard } from '../utils/clipboard'

// ── Canonical shape (mirrors flowgate.default.0395 P0009 §2 / L0010 §1-2) ────
interface WPCandidate { provider_id: string; display_name: string | null; group_label: string | null }
interface WPProviderStatus { provider_id: string; registered: boolean; current_name: string | null; snapshot_name: string | null; name_changed: boolean }
interface WPAssignmentSummary { provider_id: string; display_name: string; step_count: number }
interface WPQuantity { unit: 'sheet' | 'set'; count: number }
interface WPStep {
  key: string
  type: string
  ordinal: number
  pair_key: string | null
  pair_role: 'instruction' | 'result' | 'single'
  provider_id: string | null
  provider_display_name: string | null
  note: string | null
  locked: boolean
  locked_reason: string | null
  origin: 'human' | 'ai_suggested' | 'system'
}
interface WPBody {
  wp_version: number
  binding: string
  counted_types: string[]
  quantities: Record<string, WPQuantity>
  provider_candidates: WPCandidate[]
  defaults: { provider_id: string | null; note: string }
  steps: WPStep[]
}

const LOCKED_TYPES = new Set(['TSR'])
const COUNT_MIN = 0
const COUNT_MAX = 20
const NOTE_MAX_CHARS = 200

const props = defineProps<{
  docId: string
  projectId: string | null
}>()
const emit = defineEmits<{
  'apply-preset': [payload: { preset: WorkPlanFillPreset; ownerDocId: string }]
}>()

const { t } = useI18n()
const { showToast } = useToast()
const docTypeStore = useDocTypeStore()

const rootRef = ref<HTMLElement | null>(null)
const loading = ref(true)
const saving = ref(false)
const aiSuggesting = ref(false)
const aiScopeOpen = ref(false)
const aiRunId = ref<string | null>(null)
const rawViewOpen = ref(false)
const applyPreviewOpen = ref(false)

const plan = ref<WPBody | null>(null)
const providerStatuses = ref<WPProviderStatus[]>([])
const assignmentSummary = ref<WPAssignmentSummary[]>([])
const unassignedStepCount = ref(0)
const revisionNo = ref(0)
const docReviewStatus = ref<string | null>(null)
const totals = ref({ design_sheets: 0, work_sets: 0, steps: 0 })
const conflict = ref<{ updatedBy: string | null; updatedAt: string | null } | null>(null)
const topLevelErrors = ref<string[]>([])
const stepErrors = ref<Record<string, string[]>>({})
const unreadable = ref<{ message: string; detail: string; raw: string | null; revisions: { revision_no: number; created_by: string; created_at: string }[] } | null>(null)


// D0007 §3.2 결정4: a value-bearing step that a lower quantity would drop stays
// recoverable by its logical key until the plan is actually saved.
const restoreBuffer = new Map<string, WPStep>()

const isLocked = computed(() => docReviewStatus.value === 'approved')

const statusLabel = computed(() => {
  const key = `main.work_plan.status_${docReviewStatus.value}`
  const label = t(key)
  return label === key ? (docReviewStatus.value ?? '') : label
})

function unitLabel(code: string): string {
  const unit = plan.value?.quantities[code]?.unit
  return unit === 'set' ? t('main.work_plan.unit_set') : t('main.work_plan.unit_sheet')
}

/**
 * A set row is named after the pair, not after its instruction document —
 * 조사 / 작업 / 테스트, the way mockup xc32frrg screen 1 labels the quantity cards.
 */
function qtyCardName(code: string): string {
  if (plan.value?.quantities[code]?.unit !== 'set') return docTypeStore.getLabel(code)
  return docTypeStore.getSetName(code)
}

// L0010 §2.9: editor and apply preview share the same measured-width classifier.
const { layoutTier } = useContentLayoutTier(rootRef)
onMounted(() => { void fetchPlan() })

// ── Type ordering / expansion (mirrors L0010 §2.1 / §2.2) ────────────────────

function typeOrder(codes: string[]): string[] {
  const registryOrder = docTypeStore.countableTypes.map((item) => item.code)
  const wanted = new Set(codes)
  const ordered = registryOrder.filter((code) => wanted.has(code))
  const remainder = codes.filter((code) => !ordered.includes(code)).sort()
  return [...ordered, ...remainder]
}

const renderedCountedTypes = computed(() => {
  if (!plan.value) return []
  return typeOrder(Array.from(new Set([
    ...plan.value.counted_types,
    ...docTypeStore.countableTypes.map((item) => item.code),
  ])))
})

function pairOf(code: string): string | undefined {
  return docTypeStore.items.find((item) => item.code === code)?.pair_code
}

function makeKey(type: string, ordinal: number): string {
  return `${type}#${ordinal}`
}

function makeStep(type: string, ordinal: number, pairKey: string | null, pairRole: WPStep['pair_role']): WPStep {
  const locked = LOCKED_TYPES.has(type)
  return {
    key: makeKey(type, ordinal),
    type,
    ordinal,
    pair_key: pairKey,
    pair_role: pairRole,
    provider_id: null,
    provider_display_name: null,
    note: null,
    locked,
    locked_reason: locked ? 'server_assembled' : null,
    origin: locked ? 'system' : 'human',
  }
}

function expandSteps(countedTypes: string[], quantities: Record<string, WPQuantity>): WPStep[] {
  const steps: WPStep[] = []
  for (const code of typeOrder(countedTypes)) {
    const q = quantities[code]
    if (!q || q.count <= 0) continue
    if (q.unit === 'sheet') {
      for (let o = 1; o <= q.count; o++) steps.push(makeStep(code, o, null, 'single'))
    } else {
      const pair = pairOf(code)
      if (!pair) continue
      for (let o = 1; o <= q.count; o++) {
        steps.push(makeStep(code, o, makeKey(pair, o), 'instruction'))
        steps.push(makeStep(pair, o, makeKey(code, o), 'result'))
      }
    }
  }
  return steps
}

function hasValue(step: WPStep): boolean {
  if (step.locked) return false
  if (step.provider_id) return true
  if ((step.note ?? '').trim() !== '') return true
  return false
}

function reexpand(
  prevSteps: WPStep[],
  countedTypes: string[],
  quantities: Record<string, WPQuantity>,
): { result: WPStep[]; removalCandidates: WPStep[] } {
  const fresh = expandSteps(countedTypes, quantities)
  const prevByKey = new Map(prevSteps.map((s) => [s.key, s]))
  const result: WPStep[] = []
  for (const s of fresh) {
    const prior = prevByKey.get(s.key) ?? restoreBuffer.get(s.key)
    if (prior && !s.locked) {
      s.provider_id = prior.provider_id
      s.provider_display_name = prior.provider_display_name
      s.note = prior.note
      s.origin = prior.origin
    }
    result.push(s)
  }
  const freshKeys = new Set(fresh.map((s) => s.key))
  const removalCandidates: WPStep[] = []
  for (const p of prevSteps) {
    if (!freshKeys.has(p.key)) {
      restoreBuffer.set(p.key, p)
      if (hasValue(p)) removalCandidates.push(p)
    }
  }
  return { result, removalCandidates }
}

// ── Fetch ─────────────────────────────────────────────────────────────────

async function fetchPlan() {
  loading.value = true
  unreadable.value = null
  conflict.value = null
  topLevelErrors.value = []
  stepErrors.value = {}
  restoreBuffer.clear()
  try {
    if (!docTypeStore.loaded) await docTypeStore.loadLabels()
    const res = await getRequest<any>(`/api/v1/documents/${encodeURIComponent(props.docId)}/work-plan`)
    plan.value = res.data.body as WPBody
    const allCodes = typeOrder(Array.from(new Set([
      ...plan.value.counted_types,
      ...docTypeStore.countableTypes.map((item) => item.code),
    ])))
    const quantities = { ...plan.value.quantities }
    for (const code of allCodes) {
      if (quantities[code]) continue
      const unit = docTypeStore.countableTypes.find((item) => item.code === code)?.unit ?? 'sheet'
      quantities[code] = { unit: unit === 'set' ? 'set' : 'sheet', count: 0 }
    }
    plan.value.counted_types = allCodes
    plan.value.quantities = quantities
    providerStatuses.value = res.data.provider_status ?? []
    assignmentSummary.value = res.data.assignment_summary ?? []
    unassignedStepCount.value = res.data.unassigned_step_count ?? 0
    revisionNo.value = res.data.revision_no
    docReviewStatus.value = res.data.doc_review_status
    totals.value = res.data.totals ?? { design_sheets: 0, work_sets: 0, steps: plan.value.steps.length }
  } catch (e: any) {
    const status = e?.response?.status
    const data = e?.response?.data
    if (status === 409 && data?.code === 'wp_unreadable') {
      unreadable.value = {
        message: data.message,
        detail: data.detail ?? '',
        raw: data.raw ?? null,
        revisions: data.revisions ?? [],
      }
    } else {
      showToast(data?.message || data?.detail || String(e), 'danger')
    }
  } finally {
    loading.value = false
  }
}

async function reload() {
  await fetchPlan()
}

function onPlanApplied(payload: { preset: WorkPlanFillPreset; ownerDocId: string }) {
  applyPreviewOpen.value = false
  emit('apply-preset', payload)
}

// ── Quantity editing ─────────────────────────────────────────────────────

function updateDerivedSummary() {
  if (!plan.value) return
  let designSheets = 0
  let workSets = 0
  for (const quantity of Object.values(plan.value.quantities)) {
    if (quantity.unit === 'set') workSets += quantity.count
    else designSheets += quantity.count
  }
  totals.value = { design_sheets: designSheets, work_sets: workSets, steps: plan.value.steps.length }
  unassignedStepCount.value = plan.value.steps.filter((step) => !step.locked && !step.provider_id).length
  const counts = new Map<string, number>()
  for (const step of plan.value.steps) {
    if (step.provider_id) counts.set(step.provider_id, (counts.get(step.provider_id) ?? 0) + 1)
  }
  assignmentSummary.value = Array.from(counts.entries()).map(([providerId, stepCount]) => ({
    provider_id: providerId,
    display_name: providerDisplayName(providerId) ?? providerId,
    step_count: stepCount,
  }))
}

function setQuantity(code: string, next: number) {
  if (!plan.value || isLocked.value) return
  const clamped = Math.max(COUNT_MIN, Math.min(COUNT_MAX, next))
  const unit = plan.value.quantities[code]?.unit ?? 'sheet'
  const quantities = { ...plan.value.quantities, [code]: { unit, count: clamped } }
  const { result } = reexpand(plan.value.steps, plan.value.counted_types, quantities)
  plan.value.quantities = quantities
  plan.value.steps = result
  updateDerivedSummary()
}

// ── Step editing ──────────────────────────────────────────────────────────

function candidateStillRegistered(providerId: string): boolean {
  const live = providerStatuses.value.find((item) => item.provider_id === providerId)
  if (live) return live.registered
  // Older/mocked responses may omit provider_status; preserve their snapshot behavior.
  return !!plan.value?.provider_candidates.some((c) => c.provider_id === providerId)
}

function providerDisplayName(providerId: string | null): string | null {
  if (!providerId || !plan.value) return null
  return plan.value.provider_candidates.find((c) => c.provider_id === providerId)?.display_name ?? providerId
}

function setStepProvider(key: string, providerId: string | null) {
  const step = plan.value?.steps.find((s) => s.key === key)
  if (!step || step.locked) return
  step.provider_id = providerId
  step.provider_display_name = providerDisplayName(providerId)
  step.origin = 'human'
  updateDerivedSummary()
}

function setStepNote(key: string, note: string) {
  const step = plan.value?.steps.find((s) => s.key === key)
  if (!step || step.locked) return
  step.note = note
  step.origin = 'human'
}

const providerOptionsWithUnassigned = computed(() => {
  const candidates = (plan.value?.provider_candidates ?? []).map((c) => {
    const name = c.display_name ?? c.provider_id
    return {
      id: c.provider_id,
      name: candidateStillRegistered(c.provider_id)
        ? name
        : `${name} (${t('main.work_plan.unavailable_provider')})`,
    }
  })
  return [{ id: '', name: t('main.work_plan.unassigned') }, ...candidates]
})

function isFirstOfType(step: WPStep, idx: number): boolean {
  if (idx === 0) return true
  return plan.value?.steps[idx - 1]?.type !== step.type
}

/**
 * Mockup xc32frrg screen 1 names the document column by type, not by logical key:
 * "설계지시 1장", "작업 레포트 1세트". A report row has no quantity entry of its own,
 * so it reads its pair's.
 */
function stepDocParts(step: WPStep): { name: string; quantity: string } {
  const name = docTypeStore.getLabel(step.type)
  const own = plan.value?.quantities[step.type]
  const q = own ?? (step.pair_key ? plan.value?.quantities[step.pair_key.split('#')[0]] : undefined)
  if (!q) return { name: name || step.key, quantity: '' }
  const quantity = q.unit === 'sheet' && own
    ? (q.count <= 1
        ? `${step.ordinal}${t('main.work_plan.unit_sheet_short')}`
        : t('main.work_plan.doc_label_sheet', { n: step.ordinal, total: q.count }))
    : t('main.work_plan.doc_label_set', { n: step.ordinal })
  return { name, quantity }
}

function stepDocName(step: WPStep): string {
  return stepDocParts(step).name
}

function stepDocQuantity(step: WPStep): string {
  return stepDocParts(step).quantity
}

function applyDefaults() {
  if (!plan.value) return
  for (const step of plan.value.steps) {
    if (step.locked) continue
    step.provider_id = plan.value.defaults.provider_id
    step.provider_display_name = providerDisplayName(plan.value.defaults.provider_id)
    step.note = plan.value.defaults.note || null
    step.origin = 'human'
  }
  updateDerivedSummary()
  showToast(t('main.work_plan.apply_to_all_done'), 'success')
}

// ── Scoped project-map suggestion and real AI invocation ──────────────────

const scopeSteps = computed(() => (plan.value?.steps ?? []).map((step) => ({
  key: step.key,
  type: step.type,
  label: [stepDocName(step), stepDocQuantity(step)].filter(Boolean).join(' '),
  provider_id: step.provider_id,
  locked: step.locked,
})))

async function fetchSuggestion(scope: WorkPlanScope) {
  if (!plan.value) return
  aiSuggesting.value = true
  try {
    const res = await postRequest<any>(
      `/api/v1/documents/${encodeURIComponent(props.docId)}/work-plan/suggest`,
      { base_revision_no: revisionNo.value, scope },
    )
    const quantities = res.data?.suggested?.quantities ?? {}
    for (const [code, count] of Object.entries(quantities)) setQuantity(code, Number(count))
    const steps = res.data?.suggested?.steps ?? []
    for (const suggested of steps) {
      const target = plan.value.steps.find((item) => item.key === suggested.key)
      if (!target || target.locked) continue
      target.provider_id = suggested.provider_id ?? target.provider_id
      target.provider_display_name = suggested.provider_display_name ?? target.provider_display_name
      if (suggested.note !== undefined) target.note = suggested.note
      target.origin = 'ai_suggested'
    }
    updateDerivedSummary()
    aiScopeOpen.value = false
    showToast(t('main.work_plan.ai_scope_success', { quantities: Object.keys(quantities).length, steps: steps.length }), 'success')
  } catch (e: any) {
    showToast(e?.response?.data?.message || String(e), 'danger')
  } finally {
    aiSuggesting.value = false
  }
}

async function startAiFill(scope: WorkPlanScope) {
  if (!plan.value || !props.projectId) return
  aiSuggesting.value = true
  try {
    const parts = props.docId.split('.')
    const res = await postRequest<any>('/api/v1/ai-invoke/start', {
      project: props.projectId,
      module: parts[1] ?? null,
      group: parts[2] ?? '',
      doc_ref: props.docId,
      action_scope: 'work_plan_fill',
      mode: 'single',
      work_plan_scope: scope,
    })
    aiRunId.value = res.data?.run_id ?? null
    aiScopeOpen.value = false
  } catch (e: any) {
    aiSuggesting.value = false
    showToast(e?.response?.data?.error_message || e?.response?.data?.message || String(e), 'danger')
  }
}

function onAiInvoke(event: Event) {
  const detail = (event as CustomEvent).detail
  const payload = detail?.payload ?? {}
  if (detail?.kind !== 'finished' || !aiRunId.value || payload.run_id !== aiRunId.value) return
  aiRunId.value = null
  aiSuggesting.value = false
  void fetchPlan()
}

onMounted(() => window.addEventListener('fg:ai_invoke', onAiInvoke))
onBeforeUnmount(() => window.removeEventListener('fg:ai_invoke', onAiInvoke))

// ── Save (P0009 §4.6 ~ §4.8) ─────────────────────────────────────────────

function canonicalBody(): WPBody {
  const p = plan.value!
  return {
    wp_version: p.wp_version,
    binding: p.binding,
    counted_types: [...p.counted_types],
    quantities: { ...p.quantities },
    provider_candidates: p.provider_candidates.map((c) => ({ ...c })),
    defaults: { ...p.defaults },
    steps: p.steps.map((s) => ({ ...s })),
  }
}

const rawJson = computed(() => (plan.value ? JSON.stringify(canonicalBody(), null, 2) : ''))

async function copyRaw() {
  const ok = await copyToClipboard(rawJson.value)
  showToast(ok ? t('main.work_plan.copy_done') : t('main.work_plan.copy_failed'), ok ? 'success' : 'danger')
}

async function save() {
  if (!plan.value || saving.value) return
  saving.value = true
  conflict.value = null
  topLevelErrors.value = []
  stepErrors.value = {}
  try {
    const res = await putRequest<any>(`/api/v1/documents/${encodeURIComponent(props.docId)}/work-plan`, {
      base_revision_no: revisionNo.value,
      body: canonicalBody(),
    })
    revisionNo.value = res.data.revision_no
    totals.value = res.data.totals
    assignmentSummary.value = res.data.assignment_summary ?? []
    unassignedStepCount.value = res.data.unassigned_step_count ?? 0
    restoreBuffer.clear()
    showToast(t('main.work_plan.save_success'), 'success')
    const unassigned = res.data.unassigned_step_count ?? 0
    if (unassigned > 0) showToast(t('main.work_plan.unassigned_warning', { n: unassigned }), 'warning', 5000)
  } catch (e: any) {
    const status = e?.response?.status
    const data = e?.response?.data
    if (status === 422 && Array.isArray(data?.errors)) {
      const byKey: Record<string, string[]> = {}
      const top: string[] = []
      for (const err of data.errors) {
        if (err.key) {
          byKey[err.key] = byKey[err.key] ?? []
          byKey[err.key].push(err.msg)
        } else {
          top.push(err.msg)
        }
      }
      stepErrors.value = byKey
      topLevelErrors.value = top.length ? top : [data.message]
      showToast(data.message, 'danger', 5000)
    } else if (status === 409 && data?.code === 'wp_revision_conflict') {
      conflict.value = { updatedBy: data.updated_by ?? null, updatedAt: data.updated_at ?? null }
    } else {
      showToast(data?.message || data?.detail || String(e), 'danger')
    }
  } finally {
    saving.value = false
  }
}

watch(() => props.docId, () => { void fetchPlan() })

// The mockup puts [연속 작업에 채우기] in the document action bar, which lives
// outside this component; MainPanel calls this to open the same preview overlay.
function openApplyPreview() {
  if (loading.value || unreadable.value) return
  applyPreviewOpen.value = true
}
defineExpose({ openApplyPreview })
</script>

<style scoped>
.wp-status-pill {
  font-size: .64rem; font-weight: 600; padding: 1px 7px; border-radius: 999px;
  margin-left: 8px; background: var(--surface-h, #f1f5f9); color: var(--text-m);
}
.wp-status-approved { background: var(--success-l, #dcfce7); color: var(--success, #16a34a); }
.wp-status-rejected { background: var(--danger-l, #fee2e2); color: var(--danger, #dc2626); }
.wp-body { display: flex; flex-direction: column; gap: 14px; padding: 16px; }
.wp-loading { padding: 24px; text-align: center; color: var(--text-m); }
/* Mockup xc32frrg screen 1 — 표 편집 모드 띠 */
.wp-toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
.wp-mode-pill {
  display: inline-flex; align-items: center; gap: 5px; padding: 3px 9px; border-radius: 999px;
  background: var(--primary-l, #eff6ff); color: var(--primary, #2563eb);
  font-size: .7rem; font-weight: 700; white-space: nowrap;
}
.wp-mode-desc { font-size: .74rem; color: var(--text-m); }
.wp-toolbar-spacer { flex: 1; }
.wp-advisory-notice, .wp-review-hint, .wp-locked-hint {
  display: flex; align-items: flex-start; gap: 8px; font-size: .78rem; line-height: 1.55; color: var(--text-m);
  background: var(--surface-h, #f8fafc); border-radius: var(--r, 6px); padding: 9px 12px;
}
.wp-advisory-notice strong { color: var(--text, #1e293b); }
.wp-conflict-banner, .wp-error-banner {
  display: flex; align-items: center; gap: 8px; font-size: .8rem; padding: 8px 12px; border-radius: var(--r, 6px);
}
.wp-conflict-banner { background: var(--warning-l, #fef3c7); color: var(--warning, #b45309); }
.wp-error-banner { background: var(--danger-l, #fee2e2); color: var(--danger, #dc2626); }
.wp-section { display: flex; flex-direction: column; gap: 10px; }
.wp-section-hd { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.wp-step-no-badge {
  width: 20px; height: 20px; border-radius: 50%; background: var(--primary, #2563eb); color: #fff;
  font-size: .68rem; font-weight: 700; display: inline-flex; align-items: center; justify-content: center;
  flex-shrink: 0;
}
.wp-section-ico { color: var(--text-m); }
.wp-section-title { font-size: .82rem; font-weight: 700; color: var(--text); }
.wp-section-desc { font-size: .74rem; color: var(--text-m); }
.wp-section-missing {
  font-size: .7rem; font-weight: 700; padding: 1px 8px; border-radius: 999px;
  background: var(--danger-l, #fee2e2); color: var(--danger, #dc2626);
}
.wp-section-totals {
  margin-left: auto; font-size: .72rem; color: var(--text-s, #475569); font-weight: 700;
  padding: 2px 9px; border: 1px solid var(--border, #e2e8f0); border-radius: 999px; background: #fff;
}
/* Mockup xc32frrg screen 1 — 수량 카드: 태그 · 이름/단위 · 스테퍼가 한 줄. */
.wp-qty-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(208px, 1fr)); gap: 8px; }
.wp-qty-card { display:flex; align-items:center; gap:8px; min-width:0; padding:8px 10px; border:1px solid var(--border); border-radius:var(--r); background:var(--surface); }
.wp-qty-card.zero { border-style:dashed; background:#fbfcfe; }
.wp-qty-card.zero .wp-qty-name { color:var(--text-m); }
.wp-qty-tags { display:inline-flex; gap:3px; flex-shrink:0; }
.wp-qty-body { display:flex; flex-direction:column; min-width:0; }
.wp-qty-name { font-size:.76rem; font-weight:600; color:var(--text); }
.wp-qty-unit { font-size:.66rem; color:var(--text-m); }
.wp-qty-stepper { display:flex; align-items:center; margin-left:auto; overflow:hidden; border:1px solid var(--border); border-radius:var(--r-sm); background:var(--surface); }
.wp-stepper-btn { width:24px; height:24px; padding:0; border:0; background:transparent; color:var(--text-s); cursor:pointer; font-weight:700; }
.wp-stepper-btn:hover:not(:disabled) { background:var(--surface-h); }
.wp-stepper-btn:disabled { opacity:.4; cursor:not-allowed; }
.wp-qty-value { width:34px; text-align:center; font-weight:700; font-size:.76rem; }
.wp-qty-value.zero { color:var(--text-m); }
.wp-defaults-row { display:grid; grid-template-columns:auto minmax(150px,172px) minmax(190px,1fr) auto; align-items:center; gap:8px; margin-top:8px; }
.wp-defaults-label { font-size:.7rem; font-weight:700; color:var(--text-m); }
.wp-defaults-note { width:100%; min-width:0; padding:4px 8px; border:1px solid var(--border); border-radius:var(--r-sm); }
.wp-step-head,.wp-step-row { display:grid; gap:8px; grid-template-columns:52px 40px minmax(110px,1fr) 172px minmax(190px,1.5fr); align-items:center; }
.wp-step-head { padding:7px 10px 6px; margin-top:10px; border-bottom:1px solid var(--border-d); color:var(--text-m); font-size:.62rem; font-weight:700; letter-spacing:.07em; text-transform:uppercase; }
.wp-step-list {
  display: flex; flex-direction: column; gap: 4px;
  max-height: calc(342px + 1px); /* border-box keeps clientHeight at the mockup's 342px */
  padding: 8px 4px 8px 0;
  border-bottom: 1px solid var(--border);
  overflow-y: auto;
}
.wp-step-row { padding:3px 8px; border:1px solid var(--border); border-radius:var(--r-sm); background:var(--surface); flex-shrink:0; }
.wp-step-row.is-first { border-left:3px solid #c7d2fe; }
.wp-step-row.is-locked { background:#fbfcfe; }
.wp-step-no { color:var(--text-m); font-size:.66rem; font-weight:700; }
.wp-step-row .doc-tag { min-width:34px; text-align:center; font-size:.6rem; padding:1px 5px; }
.wp-step-label { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--text); font-size:.74rem; }
.wp-step-label small { color:var(--text-m); font-size:.66rem; }
.wp-step-row .prov-select,.wp-step-row select { width:100%; min-width:0; height:20px; padding:2px 8px; font-size:.74rem; }
.wp-step-msg { width:100%; min-width:0; height:20px; padding:2px 8px; border:1px solid var(--border); border-radius:var(--r-sm); color:var(--text); background:var(--surface); font-size:.74rem; }
.wp-step-msg.is-ai { border-color:#ddd6fe; background:#faf5ff; }
.step-empty { padding:16px; border:1px dashed var(--border-d); border-radius:var(--r); color:var(--text-m); background:var(--surface-h); }
.wp-row-error { border-color:var(--danger,#dc2626); }
.wp-layout-narrow .wp-step-head,.wp-layout-narrow .wp-step-row { grid-template-columns:48px 36px 0 minmax(130px,1fr) minmax(160px,1.2fr); }
.wp-layout-narrow .wp-step-label { visibility:hidden; }
.wp-step-legend {
  display: flex; flex-wrap: wrap; align-items: center; gap: 4px 14px;
  font-size: .7rem; color: var(--text-m); padding-top: 2px;
}
.wp-ai-dot { display: inline-block; width: 8px; height: 8px; border-radius: 2px; background: #ddd6fe; }
/* Mockup xc32frrg screen 1 — 하단 수량 카드 3장 */
.wp-sum-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 10px; }
.wp-sum-card {
  border: 1px solid var(--border, #e2e8f0); border-radius: var(--r, 6px);
  background: var(--surface-h, #f8fafc); padding: 11px 13px;
}
.wp-sum-label { display: flex; align-items: center; gap: 5px; font-size: .7rem; color: var(--text-m); font-weight: 600; }
.wp-sum-value { font-size: 1.5rem; font-weight: 800; color: var(--text, #1e293b); line-height: 1.25; }
.wp-sum-value small { font-size: .7rem; font-weight: 600; color: var(--text-m); margin-left: 3px; }
.wp-sum-desc { font-size: .66rem; color: var(--text-m); }
.wp-unreadable { display: flex; flex-direction: column; align-items: center; gap: 6px; padding: 32px 16px; text-align: center; }
.wp-unreadable-icon { font-size: 2rem; color: var(--danger, #dc2626); }
.wp-unreadable-title { font-weight: 700; }
.wp-unreadable-desc, .wp-unreadable-detail { font-size: .8rem; color: var(--text-m); margin: 0; }
.wp-unreadable-revisions { margin-top: 8px; font-size: .76rem; color: var(--text-m); text-align: left; }
.wp-unreadable-raw { margin-top: 10px; width: 100%; max-height: 200px; overflow: auto; background: #0f172a; color: #e2e8f0; padding: 10px; border-radius: var(--r, 6px); font-size: .7rem; text-align: left; }
.wp-raw-overlay {
  position: absolute; inset: 0; background: rgba(15,23,42,.45); display: flex;
  align-items: center; justify-content: center; z-index: 50; padding: 24px;
}
.wp-raw-box { background: #fff; border-radius: var(--r, 8px); width: 100%; max-width: 720px; max-height: 100%; display: flex; flex-direction: column; overflow: hidden; }
.wp-raw-hd { display: flex; align-items: center; justify-content: space-between; padding: 10px 14px; border-bottom: 1px solid var(--border, #e2e8f0); font-weight: 700; font-size: .84rem; }
.wp-raw-hd > div { display: flex; gap: 8px; }
.wp-raw-content { margin: 0; padding: 14px; overflow: auto; font-size: .74rem; background: #0f172a; color: #e2e8f0; flex: 1; }

/* L0010 §1.4: mid tier drops the 문서 column, narrow tier stacks cards. */
.wp-editor { position: relative; }
.wp-layout-mid .wp-col-doc { display: none; }
</style>
