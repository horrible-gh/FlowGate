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
        <!-- 0399 D0010 §6.1 (시안 fgh29xnk v3 · 화면 1) — the sentence that closes the gap
             this whole module exists for: people approved a plan and expected the workflow
             to follow. It does not, and it never did; the plan is a record until somebody
             pours it. Said here, above the plan itself, rather than only on the button. -->
        <div class="wp-usage-note">
          <AppIcon name="lightning" />
          <span>
            <strong>{{ t('main.work_plan_pour.usage_title') }}</strong>
            {{ t('main.work_plan_pour.usage_desc') }}
          </span>
        </div>
        <!-- 0403 NR0004 F3 — 이 계획이 워크플로에 실제로 부어진 적이 있는지 한 줄로 말한다.
             서버는 적용 이력을 갖고 있었지만 화면 어디에도 나오지 않았고, 새 붓기 경로는
             기록조차 남기지 않아 "적용했는데 아무 데도 안 남는다"가 보이지 않았다. 한 번도
             부은 적이 없는 계획은 그 사실을 그대로 그린다. -->
        <p class="wp-last-apply" :class="{ 'is-none': !lastApplication }">
          <AppIcon name="info" />
          <span v-if="lastApplication">
            {{ t('main.work_plan.last_application', {
              who: lastApplication.applied_by ?? '?',
              when: lastApplication.applied_at ?? '',
              rev: lastApplication.wp_revision_no ?? 0,
            }) }}
          </span>
          <span v-else>{{ t('main.work_plan.last_application_none') }}</span>
        </p>

        <!-- Mockup xc32frrg screen 1 — 표 편집 모드 띠 -->
        <div class="wp-toolbar">
          <span class="wp-mode-pill"><AppIcon name="grid-four" /> {{ t('main.work_plan.table_mode') }}</span>
          <span class="wp-mode-desc">{{ t('main.work_plan.table_mode_desc') }}</span>
          <span class="wp-toolbar-spacer"></span>
          <button
            class="btn btn-outline btn-sm"
            type="button"
            :disabled="loading || !!unreadable || aiSuggesting || dirty || isLocked"
            :title="aiRunLocked ? lockedHint : (dirty ? t('main.work_plan.ai_needs_save') : undefined)"
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
          <AppIcon name="lock" /> {{ lockedHint }}
        </p>
        <p v-else-if="docReviewStatus === 'pending_review'" class="wp-review-hint">
          {{ t('main.work_plan.review_pending_hint') }}
        </p>

        <!-- 0403 NR0004 F5 — 저장하지 않은 편집이 AI 채우기에 조용히 지워지지 않게 한다.
             AI 채우기는 서버의 정본을 읽고, 끝나면 화면을 그 정본으로 통째로 갈아끼운다.
             그래서 직전에 손으로 넣은 수량·배정·멘트가 아무 안내 없이 사라졌다. 여기서
             먼저 저장하게 하면 AI 가 읽는 계획과 화면의 계획이 같아진다(F6 과 같은 이유). -->
        <div v-if="dirty" class="wp-dirty-banner">
          <AppIcon name="warning-circle" />
          <span>{{ t('main.work_plan.unsaved_changes') }}</span>
          <button
            type="button"
            class="btn btn-outline btn-sm"
            :disabled="saving || isLocked"
            @click="save"
          >
            {{ saving ? t('main.work_plan.saving') : t('main.work_plan.save') }}
          </button>
        </div>

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
            <AiProviderSelect :providers="providerOptionsWithUnassigned" :model-value="plan.defaults.provider_id ?? ''" :disabled="isLocked" hide-label hide-icon compact @update:model-value="(v) => setDefaultProvider(v || null)" />
            <span class="wp-note-field">
              <input :value="plan.defaults.note" type="text" class="wp-defaults-note" :class="{ 'is-over-limit': plan.defaults.note.length > noteMaxChars }" :placeholder="t('main.work_plan.defaults_note_placeholder')" :disabled="isLocked" @input="(e) => setDefaultNote((e.target as HTMLInputElement).value)" />
              <small class="wp-note-count" :class="{ 'is-over-limit': plan.defaults.note.length > noteMaxChars }">
                {{ plan.defaults.note.length > noteMaxChars
                  ? t('main.work_plan.note_char_over', { current: plan.defaults.note.length, max: noteMaxChars })
                  : t('main.work_plan.note_char_count', { current: plan.defaults.note.length, max: noteMaxChars }) }}
              </small>
            </span>
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
              <AiProviderSelect v-else :providers="providerOptionsWithUnassigned" :model-value="step.provider_id ?? ''" :disabled="isLocked" hide-label hide-icon compact @update:model-value="(v) => setStepProvider(step.key, v || null)" />
              <span class="wp-note-field">
                <input class="wp-step-msg" :class="{ 'is-ai': step.origin === 'ai_suggested', 'is-over-limit': (step.note ?? '').length > noteMaxChars }" type="text" :placeholder="t('main.work_plan.note_placeholder')" :value="step.locked ? '' : (step.note ?? '')" :disabled="step.locked || isLocked" @input="(e) => setStepNote(step.key, (e.target as HTMLInputElement).value)" />
                <small v-if="!step.locked" class="wp-note-count" :class="{ 'is-over-limit': (step.note ?? '').length > noteMaxChars }">
                  {{ (step.note ?? '').length > noteMaxChars
                    ? t('main.work_plan.note_char_over', { current: (step.note ?? '').length, max: noteMaxChars })
                    : t('main.work_plan.note_char_count', { current: (step.note ?? '').length, max: noteMaxChars }) }}
                </small>
              </span>
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
    <div v-if="rawViewOpen" class="wp-raw-overlay" @keydown.escape="rawViewOpen = false">
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

    <WorkPlanAiScopeDialog
      :visible="aiScopeOpen"
      :busy="aiSuggesting"
      :countable-types="docTypeStore.countableTypes.map((item) => ({ code: item.code, label: item.label }))"
      :steps="scopeSteps"
      :candidates="scopeProviderOptions"
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
import WorkPlanAiScopeDialog from './WorkPlanAiScopeDialog.vue'
import type { WorkPlanScope } from './WorkPlanAiScopeDialog.vue'
import { useContentLayoutTier } from '../composables/useContentLayoutTier'
import { useToast } from './common/useToast'
import { useDocTypeStore } from '../stores/docTypeStore'
import { useAiProviderStore } from '../stores/aiProvider'
import { groupIdFromDocId, useAiInvokeRunsStore } from '../stores/aiInvokeRuns'
import { copyToClipboard } from '../utils/clipboard'

// ── Canonical shape (mirrors flowgate.default.0395 P0009 §2 / L0010 §1-2) ────
interface WPCandidate { provider_id: string; display_name: string | null; group_label: string | null }
interface WPRegisteredProvider { id: string; name: string | null; group_label: string | null }
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
// The server response limit is canonical; 1000 is compatibility for old mocks.
const noteMaxChars = ref(1000)

const props = defineProps<{
  docId: string
  projectId: string | null
  /** 0424 TR0005 rev2 — the document panel's AI-run lock, passed down exactly like
   *  DocHeader / AttachmentCard / MdViewer already receive it. Until now this editor
   *  was the one card in the column that took no lock at all, so every control on the
   *  work plan (저장 · 수량 스테퍼 · 공급자 · 멘트 · 모두 적용 · AI 제안) stayed clickable
   *  through an AI run and only answered with a 423 toast. */
  readOnly?: boolean
}>()
const { t } = useI18n()
const { showToast } = useToast()
const docTypeStore = useDocTypeStore()
const aiProviderStore = useAiProviderStore()

const rootRef = ref<HTMLElement | null>(null)
const loading = ref(true)
const saving = ref(false)
const aiSuggesting = ref(false)
const aiScopeOpen = ref(false)
const aiRunId = ref<string | null>(null)
const rawViewOpen = ref(false)

const plan = ref<WPBody | null>(null)
const serverRegisteredProviders = ref<WPRegisteredProvider[]>([])
const serverRegisteredProvidersKnown = ref(false)
const providerStatuses = ref<WPProviderStatus[]>([])
const assignmentSummary = ref<WPAssignmentSummary[]>([])
const unassignedStepCount = ref(0)
const revisionNo = ref(0)
const docReviewStatus = ref<string | null>(null)
// 0403 NR0004 F5 — 저장하지 않은 편집이 있는지. AI 채우기가 화면을 서버 정본으로
// 갈아끼우기 때문에, 이 값이 참인 동안에는 AI 에 맡기지 못하게 한다.
const dirty = ref(false)
// 0403 NR0004 F7 — 편집 가능 여부는 서버가 판정해 응답에 실어 준다. 화면이 승인 상태만
// 보고 따로 잠그면, 서버는 허용하는데 화면만 잠긴 계획(그리고 그 반대)이 생긴다.
const editable = ref(true)
const editLockedReason = ref<string | null>(null)
// 0403 NR0004 F3 — 이 계획이 마지막으로 워크플로에 부어진 기록.
const lastApplication = ref<{ applied_at?: string; applied_by?: string; wp_revision_no?: number } | null>(null)
const totals = ref({ design_sheets: 0, work_sets: 0, steps: 0 })
const conflict = ref<{ updatedBy: string | null; updatedAt: string | null } | null>(null)
const topLevelErrors = ref<string[]>([])
const stepErrors = ref<Record<string, string[]>>({})
const unreadable = ref<{ message: string; detail: string; raw: string | null; revisions: { revision_no: number; created_by: string; created_at: string }[] } | null>(null)


// D0007 §3.2 결정4: a value-bearing step that a lower quantity would drop stays
// recoverable by its logical key until the plan is actually saved.
const restoreBuffer = new Map<string, WPStep>()

// 0424 B0001 / TR0005 rev2 — "AI실행중에 버튼들이 안눌리게 하던가 없애야지 토스트 띄우면
// 다인가?". This group's own [AI 제안 불러오기] starts a run against this very WP document
// (action_scope 'work_plan_fill'), and every mutating work-plan route is behind the group's
// AI lease, so from that moment the server answers 423 GROUP_AI_RUN_LOCKED. The editor has
// to already read as locked instead of letting the click through to a toast. Same predicate
// the explorers use (GroupTreeNode / FileExplorer): the run registry OR a live lease.
const aiInvokeRunsStore = useAiInvokeRunsStore()
const wpGroupId = computed(() => groupIdFromDocId(props.docId))
const groupBusy = computed(() => {
  const groupId = wpGroupId.value
  return !!groupId && (
    aiInvokeRunsStore.isGroupRunning(groupId)
    || aiInvokeRunsStore.isGroupInlineVisible(groupId)
  )
})
// A lease can outlive this tab's own view of the run (0401 NR0003 SS3), so ask the lease
// endpoint too. Single-flight + generation-guarded inside the store, and it only ever adds
// a lock, so it cannot make a control flicker back to enabled. This tab is long-lived and
// never remounts around a run, so the phase is watched as well: without it a lease read as
// live at mount would keep the editor locked after the run had already ended.
watch(
  () => [wpGroupId.value, aiInvokeRunsStore.runsByGroup[wpGroupId.value ?? '']?.phase] as const,
  ([groupId]) => {
    if (groupId) void aiInvokeRunsStore.refreshGroupLease(groupId)
  },
  { immediate: true },
)

const aiRunLocked = computed(() => props.readOnly === true || groupBusy.value)
const isLocked = computed(() => !editable.value || aiRunLocked.value)

const lockedHint = computed(() =>
  aiRunLocked.value
    ? t('main.review_action_bar.ai_running_hint')
    : editLockedReason.value === 'final_approved'
      ? t('main.work_plan.locked_after_final_approval')
      : t('main.work_plan.locked_by_status'),
)

// The scope dialog is a write surface too — a run that starts while it is open must not
// leave [프로젝트 지도로 채우기] / [AI 로 채우기] sitting there ready to fire.
watch(aiRunLocked, (locked) => {
  if (locked) aiScopeOpen.value = false
})

function markDirty() {
  dirty.value = true
}

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
  serverRegisteredProviders.value = []
  serverRegisteredProvidersKnown.value = false
  try {
    const providerLoad = props.projectId
      ? aiProviderStore.ensureLoaded(props.projectId)
      : Promise.resolve()
    if (!docTypeStore.loaded) await Promise.all([docTypeStore.loadLabels(), providerLoad])
    else await providerLoad
    const res = await getRequest<any>(`/api/v1/documents/${encodeURIComponent(props.docId)}/work-plan`)
    serverRegisteredProvidersKnown.value = Array.isArray(res.data.registered_providers)
    serverRegisteredProviders.value = serverRegisteredProvidersKnown.value
      ? res.data.registered_providers
      : []
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
    noteMaxChars.value = Number(res.data.limits?.note_max_chars) || 1000
    docReviewStatus.value = res.data.doc_review_status
    // 0403 NR0004 F7: 서버가 판정한 값을 그대로 쓴다. 응답에 없으면(옛 응답·목) 열어 둔다 —
    // 잠글지 말지는 서버만 알고, 화면이 혼자 추측해 잠그던 것이 이 결함이었다.
    editable.value = res.data.editable === undefined ? true : !!res.data.editable
    editLockedReason.value = res.data.edit_locked_reason ?? null
    lastApplication.value = res.data.last_application ?? null
    dirty.value = false
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
  markDirty()
  updateDerivedSummary()
}

function setDefaultProvider(providerId: string | null) {
  if (!plan.value || isLocked.value) return
  plan.value.defaults.provider_id = providerId
  markDirty()
}

function setDefaultNote(note: string) {
  if (!plan.value || isLocked.value) return
  plan.value.defaults.note = note
  markDirty()
}

// ── Step editing ──────────────────────────────────────────────────────────

/**
 * 0411 T0004: the server response is the atomic source for the registered set.
 * The project store covers old responses and project transitions; frozen candidates are only
 * a final compatibility fallback for old tests/servers that expose neither source.
 */
const liveProviderRowsKnown = computed(() =>
  serverRegisteredProvidersKnown.value
  || (!!props.projectId
    && aiProviderStore.loadedProjectId === props.projectId
    && !aiProviderStore.error),
)

const liveProviderRows = computed<WPRegisteredProvider[]>(() => {
  if (serverRegisteredProvidersKnown.value) return serverRegisteredProviders.value
  if (liveProviderRowsKnown.value) {
    return aiProviderStore.providers.map((provider) => ({
      id: provider.id,
      name: provider.name,
      group_label: null,
    }))
  }
  return (plan.value?.provider_candidates ?? []).map((candidate) => ({
    id: candidate.provider_id,
    name: candidate.display_name,
    group_label: candidate.group_label,
  }))
})

const liveProviderById = computed(() =>
  new Map(liveProviderRows.value.map((provider) => [provider.id, provider])),
)

const scopeProviderOptions = computed<WPCandidate[]>(() =>
  liveProviderRows.value.map((provider) => ({
    provider_id: provider.id,
    display_name: provider.name,
    group_label: provider.group_label,
  })),
)

function candidateStillRegistered(providerId: string): boolean {
  if (serverRegisteredProvidersKnown.value) return liveProviderById.value.has(providerId)
  // An old server may omit registered_providers but still return an authoritative status row.
  const status = providerStatuses.value.find((item) => item.provider_id === providerId)
  if (status) return status.registered
  if (liveProviderRowsKnown.value) return liveProviderById.value.has(providerId)
  // Older/mocked responses may omit both registered_providers and provider_status.
  return !!plan.value?.provider_candidates.some((candidate) => candidate.provider_id === providerId)
}

function providerDisplayName(providerId: string | null): string | null {
  if (!providerId || !plan.value) return null
  return liveProviderById.value.get(providerId)?.name
    ?? plan.value.provider_candidates.find((candidate) => candidate.provider_id === providerId)?.display_name
    ?? providerId
}

function setStepProvider(key: string, providerId: string | null) {
  if (isLocked.value) return
  const step = plan.value?.steps.find((s) => s.key === key)
  if (!step || step.locked) return
  step.provider_id = providerId
  step.provider_display_name = providerDisplayName(providerId)
  step.origin = 'human'
  markDirty()
  updateDerivedSummary()
}

function setStepNote(key: string, note: string) {
  if (isLocked.value) return
  const step = plan.value?.steps.find((s) => s.key === key)
  if (!step || step.locked) return
  step.note = note
  step.origin = 'human'
  markDirty()
}

const providerOptionsWithUnassigned = computed(() => {
  const options: { id: string; name: string }[] = []
  const seen = new Set<string>()
  const append = (providerId: string) => {
    if (!providerId || seen.has(providerId)) return
    seen.add(providerId)
    const name = providerDisplayName(providerId) ?? providerId
    options.push({
      id: providerId,
      name: candidateStillRegistered(providerId)
        ? name
        : `${name} (${t('main.work_plan.unavailable_provider')})`,
    })
  }

  // Manual assignment is registered-all, independent from the frozen AI candidate scope.
  liveProviderRows.value.forEach((provider) => append(provider.id))
  // Keep deleted providers visible only when the body actually uses them.
  append(plan.value?.defaults.provider_id ?? '')
  ;(plan.value?.steps ?? []).forEach((step) => append(step.provider_id ?? ''))

  return [{ id: '', name: t('main.work_plan.unassigned') }, ...options]
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
  if (!plan.value || isLocked.value) return
  for (const step of plan.value.steps) {
    if (step.locked) continue
    step.provider_id = plan.value.defaults.provider_id
    step.provider_display_name = providerDisplayName(plan.value.defaults.provider_id)
    step.note = plan.value.defaults.note || null
    step.origin = 'human'
  }
  markDirty()
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
  if (!plan.value || isLocked.value) return
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
    // 제안을 받은 결과도 아직 저장되지 않은 편집이다.
    markDirty()
    updateDerivedSummary()
    aiScopeOpen.value = false
    showToast(t('main.work_plan.ai_scope_success', { quantities: Object.keys(quantities).length, steps: steps.length }), 'success')
  } catch (e: any) {
    const data = e?.response?.data
    // 0403 NR0004 F6: 제안 기준이 화면과 어긋났다. 저장 경로와 같은 코드로 오므로 같은
    // [새로 읽기] 띠를 띄운다 — 토스트만 띄우면 무엇을 해야 하는지가 사라진다.
    if (e?.response?.status === 409 && data?.code === 'wp_revision_conflict') {
      conflict.value = { updatedBy: data.updated_by ?? null, updatedAt: data.updated_at ?? null }
      aiScopeOpen.value = false
    }
    showToast(data?.message || String(e), 'danger')
  } finally {
    aiSuggesting.value = false
  }
}

async function startAiFill(scope: WorkPlanScope) {
  if (!plan.value || !props.projectId || isLocked.value) return
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
  if (!plan.value || saving.value || isLocked.value) return
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
    dirty.value = false
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
/* 0399 D0010 §6.1 — 작업계획 본문 위 안내 한 줄 */
.wp-usage-note {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin-bottom: 12px;
  padding: 9px 12px;
  background: #fffbeb;
  border: 1px solid #fde68a;
  border-radius: var(--r, 8px);
  font-size: .72rem;
  line-height: 1.55;
  color: #92400e;
}
.wp-usage-note i {
  flex-shrink: 0;
  margin-top: 2px;
  color: #d97706;
}

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
/* 0403 NR0004 F5 — 저장하지 않은 편집 띠. 저장 충돌 띠와 같은 자리, 같은 모양. */
.wp-dirty-banner {
  display: flex; align-items: center; gap: 8px; font-size: .8rem; padding: 8px 12px;
  border-radius: var(--r, 6px); background: var(--warning-l, #fef3c7); color: var(--warning, #b45309);
}
.wp-dirty-banner button { margin-left: auto; }
/* 0403 NR0004 F3 — 마지막 적용 한 줄. */
.wp-last-apply {
  display: flex; align-items: center; gap: 6px; margin: 0;
  font-size: .72rem; color: var(--text-m);
}
.wp-last-apply.is-none { color: var(--text-m); opacity: .85; }
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
.wp-note-field { display:flex; min-width:0; flex-direction:column; gap:2px; }
.wp-defaults-note { width:100%; min-width:0; padding:4px 8px; border:1px solid var(--border); border-radius:var(--r-sm); }
.wp-note-count { color:var(--text-m); font-size:.62rem; line-height:1.15; text-align:right; }
.wp-note-count.is-over-limit { color:var(--danger); font-weight:700; }
.wp-defaults-note.is-over-limit,.wp-step-msg.is-over-limit { border-color:var(--danger); background:color-mix(in srgb,var(--danger) 6%,var(--surface)); }
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
