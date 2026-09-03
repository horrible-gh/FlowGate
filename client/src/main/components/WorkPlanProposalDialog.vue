<template>
  <teleport to="body">
    <div
      v-if="visible"
      ref="overlayRef"
      class="modal-bg"
      tabindex="-1"
      @keydown.escape.prevent="onClose"
    >
      <div
        class="modal-box modal-wpp"
        :class="{ 'modal-wpp--solo': noProviders }"
        role="dialog"
        aria-modal="true"
        aria-labelledby="wpp-title"
      >
        <div class="modal-hd">
          <div class="modal-title" id="wpp-title">
            <AppIcon name="clipboard-text" class="wpp-title-ico" />
            {{ t('main.work_plan_proposal_dialog.title') }}
            <!-- 0405 T0011: This dialog's main subject is not [AI Invoke] but creating a
                 work plan. The badge next to the title states that fact plainly. -->
            <span class="wpp-main-task" data-test="wpp-main-task">
              {{ t('main.work_plan_proposal_dialog.main_task') }}
            </span>
          </div>
          <button type="button" class="modal-close" data-test="wpp-close" @click="onClose">
            <AppIcon name="x" />
          </button>
        </div>

        <!-- 0405 T0011 rev2 — Until the provider list arrives, this dialog's shape isn't
             decided yet. Whether there's one section or two, and whether the rightmost
             primary button is [AI Invoke] or [+ Create Document], both depend on that
             answer. So nothing to pick or press is drawn before the answer comes — it's
             drawn once after the answer arrives, and never moves again after that, no
             matter the state. -->
        <div v-if="!providersSettled" class="modal-bd wpp-body wpp-loading" data-test="wpp-loading">
          {{ t('main.work_plan_proposal_dialog.loading') }}
        </div>

        <div v-else class="modal-bd wpp-body">
          <p class="wpp-intro" data-test="wpp-intro">
            {{ noProviders
              ? t('main.work_plan_proposal_dialog.intro_no_providers')
              : t('main.work_plan_proposal_dialog.intro') }}
          </p>

          <div class="wpp-cols" :class="{ 'wpp-cols--solo': noProviders }">
            <!-- ① Document types to count -->
            <section class="wpp-sec">
              <div class="wpp-sec-hd">
                <span class="wpp-sec-no">1</span>
                <span class="wpp-sec-title">{{ t('main.work_plan_proposal_dialog.section_types') }}</span>
                <span class="wpp-count-pill" :class="{ zero: selectedTypes.size === 0 }">
                  {{ selectedTypes.size }} / {{ countableTypes.length }}
                </span>
                <div class="wpp-sec-acts">
                  <button type="button" class="wpp-mini-btn" data-test="wpp-select-all-types" @click="selectAllTypes">
                    {{ t('main.work_plan_proposal_dialog.select_all') }}
                  </button>
                  <button type="button" class="wpp-mini-btn" data-test="wpp-clear-all-types" @click="clearAllTypes">
                    {{ t('main.work_plan_proposal_dialog.clear_all') }}
                  </button>
                </div>
              </div>
              <div v-if="typesError" class="wpp-load-error">
                {{ t('main.work_plan_proposal_dialog.types_load_failed') }}
                <button type="button" class="wpp-mini-btn" @click="loadTypes">
                  {{ t('main.work_plan_proposal_dialog.retry') }}
                </button>
              </div>
              <div v-else class="wpp-scroll">
                <label
                  v-for="item in countableTypes"
                  :key="item.code"
                  class="wpp-check"
                  :class="{ on: selectedTypes.has(item.code) }"
                  data-test="wpp-type"
                  @click.prevent="toggleType(item.code)"
                >
                  <span class="wpp-check-box"><AppIcon name="check" class="wpp-check-ico" /></span>
                  <span class="doc-tag wpp-check-tag" :class="`c-${item.code}`">{{ item.code }}</span>
                  <span class="wpp-check-name">{{ item.label }}</span>
                </label>
              </div>
            </section>

            <!-- ② Candidate providers — 0405 T0011 rev2 (rejection: "AI공급자 선택할게
                 없으면 [2 후보공급자]는 안나오게 하고 1만 선택하고 생성할수 있게 해야하지
                 않겠니?"). A section with nothing to pick is not drawn at all. The case
                 where the list failed to load (providersError) is different — then the
                 section stays and offers [Retry]. -->
            <section v-if="!noProviders" class="wpp-sec" data-test="wpp-sec-providers">
              <div class="wpp-sec-hd">
                <span class="wpp-sec-no">2</span>
                <span class="wpp-sec-title">{{ t('main.work_plan_proposal_dialog.section_providers') }}</span>
                <span class="wpp-count-pill" :class="{ zero: selectedProviders.size === 0 }">
                  {{ selectedProviders.size }} / {{ providersLoaded.length }}
                </span>
                <div class="wpp-sec-acts">
                  <button type="button" class="wpp-mini-btn" data-test="wpp-select-all-providers" @click="selectAllProviders">
                    {{ t('main.work_plan_proposal_dialog.select_all') }}
                  </button>
                  <button type="button" class="wpp-mini-btn" data-test="wpp-clear-all-providers" @click="clearAllProviders">
                    {{ t('main.work_plan_proposal_dialog.clear_all') }}
                  </button>
                </div>
              </div>
              <div v-if="providersError" class="wpp-load-error">
                {{ t('main.work_plan_proposal_dialog.providers_load_failed') }}
                <button type="button" class="wpp-mini-btn" @click="loadProviders">
                  {{ t('main.work_plan_proposal_dialog.retry') }}
                </button>
              </div>
              <div v-else class="wpp-scroll">
                <label
                  v-for="p in providersLoaded"
                  :key="p.id"
                  class="wpp-check"
                  :class="{ on: selectedProviders.has(p.id) }"
                  data-test="wpp-provider"
                  @click.prevent="toggleProvider(p.id)"
                >
                  <span class="wpp-check-box"><AppIcon name="check" class="wpp-check-ico" /></span>
                  <span class="wpp-check-name">{{ p.name }}</span>
                </label>
              </div>
              <p class="wpp-hint">{{ t('main.work_plan_proposal_dialog.providers_hint') }}</p>
            </section>
          </div>

          <!-- flowgate.default.0416 TR0005 (rejection: "[실행 프로바이더] 이거 어디갔냐고" /
               "\"전달멘트\" 랑 박스 높낮이는 하나도 안맞고"): the box for picking the provider
               used by this run. rev2 made the contract identical, word for word, to the
               other dialogs — this dialog does not hold its own value; like
               AiInvokeDialog.vue:41,44 / AppHeader.vue:170 /
               ContinuousWarningDialog.vue:150 / WorkflowDecisionModal.vue:430, it reads
               aiProviderStore.selectedProviderId and writes back through
               aiProviderStore.selectProvider. rev1 filled a local ref from
               providersLoaded[0], so when the project default provider or the header's
               chosen value was A, this dialog alone showed B and could run with B — and
               the chosen value vanished when the dialog closed. It's still a distinct
               value from the multi-select candidates (section ②). It sits in one row
               next to the planner note and matches the two inputs' heights
               (.wpp-provider-select/.wpp-note-input both 30px) so the two side-by-side
               boxes never look misaligned. While running it locks with :disabled just
               like the input beside it — ignoring only the event would let the
               on-screen value and the value actually used drift apart. -->
          <div class="wpp-note-row">
            <span v-if="!noProviders" class="wpp-field-block wpp-provider-block">
              <span class="wpp-note-label">{{ t('main.work_plan_proposal_dialog.provider_label') }}</span>
              <AiProviderSelect
                class="wpp-provider-select"
                :providers="providersLoaded"
                :model-value="aiProviderStore.selectedProviderId"
                :errored="!!aiProviderStore.error"
                :disabled="creating || !!busyAction"
                hide-label
                hide-icon
                data-test="wpp-default-provider"
                @update:model-value="aiProviderStore.selectProvider"
              />
            </span>
            <!-- flowgate.default.0416 T0004 (B0001 "플래너한테 아무런 멘트도 전달할수가
                 없는거지?"): the planner note attached in common to every work-plan step.
                 Reuses the same contract as WorkPlanEditor.vue's plan.defaults.note input
                 (placeholder, character count, over-limit state). All three paths —
                 create document, copy mention, AI invoke — carry this value through as
                 scope.note. -->
            <span class="wpp-field-block wpp-note-block">
              <span class="wpp-note-label">{{ t('main.work_plan_proposal_dialog.note_label') }}</span>
              <span class="wpp-note-field">
                <input
                  :value="note"
                  type="text"
                  class="wpp-note-input"
                  :class="{ 'is-over-limit': noteOverLimit }"
                  :placeholder="t('main.work_plan.defaults_note_placeholder')"
                  :disabled="creating || !!busyAction"
                  data-test="wpp-note"
                  @input="(e) => { note = (e.target as HTMLInputElement).value; noteTouched = true }"
                />
                <small
                  class="wpp-note-count"
                  :class="{ 'is-over-limit': noteOverLimit }"
                  data-test="wpp-note-count"
                >
                  {{ noteOverLimit
                    ? t('main.work_plan.note_char_over', { current: note.length, max: noteMaxChars })
                    : t('main.work_plan.note_char_count', { current: note.length, max: noteMaxChars }) }}
                </small>
                <!-- flowgate.default.0421 NR0003 §3 — a notice that shows only when the
                     value was auto-filled. It disappears once the user edits the field
                     directly (noteTouched). Uses its own data-test and CSS class —
                     reusing a shared class would make findAll assertions flaky. -->
                <small
                  v-if="showNoteAutoFilled"
                  class="wpp-note-auto"
                  data-test="wpp-note-auto"
                >
                  {{ t('main.work_plan_proposal_dialog.note_auto_filled') }}
                </small>
              </span>
            </span>
          </div>

          <!-- P0004 [disabled reason]: this line always occupies its slot — a reason when there is
               one, the chosen summary otherwise. Never changes the button row's height. -->
          <div class="wpp-notice" :class="{ warn: !!blockReason }" data-test="wpp-notice">
            <AppIcon :name="blockReason ? 'warning-circle' : 'info'" />
            <span>{{ blockReason || summaryLine }}</span>
          </div>
        </div>

        <div class="modal-ft wpp-ft">
          <button
            type="button"
            class="btn btn-ghost"
            data-test="wpp-cancel"
            @click="onClose"
          >{{ t('common.cancel') }}</button>
          <template v-if="providersSettled">
            <!-- 0405 T0011 rev2 (rejection 3: "AI공급자 선택할게 없으면 [AI호출이 의미
                 없잖아] [+ 문서생성] 이 맨 우측으로 오게하고 이걸 강조해야지"): when there's
                 no provider, this button becomes the blue primary button and the
                 wpp-ft-last rule pushes it to the rightmost position. When a provider
                 exists, it stays the white secondary button in the second slot as
                 before. -->
            <button
              type="button"
              class="btn"
              :class="noProviders ? 'btn-primary wpp-main-btn wpp-ft-last' : 'btn-secondary'"
              data-test="wpp-create-empty"
              :disabled="!canRun || creating"
              @click="onCreateEmpty"
            >
              <AppIcon name="plus" />
              {{ creating
                ? t('main.work_plan_proposal_dialog.btn_create_busy')
                : t('main.work_plan_proposal_dialog.btn_create') }}
            </button>
            <button
              type="button"
              class="btn btn-secondary"
              data-test="wpp-copy-mention"
              :disabled="!canRun || busyAction === 'copy'"
              @click="onCopyMention"
            >
              <AppIcon name="copy" />
              {{ busyAction === 'copy'
                ? t('main.work_plan_proposal_dialog.btn_copy_busy')
                : t('main.work_plan_proposal_dialog.btn_copy') }}
            </button>
            <!-- When there is no provider to pick, this button would be clickable but
                 have nothing to do. Rather than leave it disabled, it's not drawn at
                 all. -->
            <button
              v-if="!noProviders"
              type="button"
              class="btn btn-primary wpp-main-btn"
              data-test="wpp-invoke-ai"
              :disabled="!canRun || busyAction === 'ai' || aiActive"
              @click="onInvokeAi"
            >
              <AppIcon name="robot" />
              {{ busyAction === 'ai'
                ? t('main.work_plan_proposal_dialog.btn_ai_busy')
                : t('main.work_plan_proposal_dialog.btn_ai') }}
            </button>
          </template>
        </div>
      </div>
    </div>
  </teleport>
</template>

<script setup lang="ts">
/**
 * The dedicated proposal dialog opened when the next action is a work plan (WP) —
 * flowgate.default.0405 P0004.
 *
 * The sections build a single scope object, and the buttons all carry that same
 * object forward as-is. The scope object doesn't invent new names — it reuses the
 * field names the work-plan edit screen's scope-picking dialog
 * (WorkPlanAiScopeDialog) already uses.
 *
 * 0405 T0011 rev1 — two lines of user rejection shaped this dialog's design.
 *   "문서생성이 아니라 AI호출을 강조해야지"  → the blue primary button is [AI Invoke], and
 *                                             only that.
 *   "맡길 단계??? 이건 대체 왜나와"          → the step-picking section was removed. Step
 *                                             assignment is always the job of whoever
 *                                             writes the work plan.
 *
 * 0405 T0011 rev2 — two lines of user rejection shaped the "project with no
 * provider" case.
 *   "AI공급자 선택할게 없으면 [2 후보공급자]는 안나오게 하고 1만 선택하고 생성할수 있게"
 *   "AI공급자 선택할게 없으면 [AI호출이 의미 없잖아] [+ 문서생성] 이 맨 우측으로 오게하고
 *    이걸 강조해야지"
 *   → when there are zero registered providers, neither section ② nor [AI Invoke] is
 *     drawn; the dialog can be completed with section ① alone, and
 *     [+ Create Document] becomes the rightmost blue primary button.
 *   No button is drawn before that answer (the provider count) arrives — buttons
 *   must never move once the dialog has been rendered.
 *
 * Button responsibilities:
 *   [Create Document]  this dialog itself POSTs /documents/work-plan (same body as
 *                       the existing creation path)
 *   [Copy Mention]      the parent POSTs /workflow/advance → clipboard (the write
 *                       has to stay inside the click's user gesture, so it reuses
 *                       the parent's deferred-copy path as-is)
 *   [AI Invoke]         the parent POSTs /ai-invoke/start — this dialog's primary
 *                       button when a provider exists
 * The busy state / failure reason while the parent is working comes back through
 * busyAction / externalNotice. The dialog doesn't close before the request
 * completes — only the parent lowers `visible` once it succeeds.
 */
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { extractApiErrorMessage, getRequest, postRequest } from '@shared/api'
import AppIcon from '@shared/AppIcon.vue'
import AiProviderSelect from './AiProviderSelect.vue'
import { useDocTypeStore, type WorkPlanCountableType } from '../stores/docTypeStore'
import { useAiProviderStore } from '../stores/aiProvider'
import { findSequenceHeadIndex } from '../composables/useSequenceStepNote'

/** P0004 [scope payload] — the single shape shared by all three paths. 0405 T0011
 *  rev1 dropped step_keys (the human step-picking section is gone). rev2 sends
 *  provider_ids as an empty array for projects with no registered provider. 0416
 *  T0004 added note — the planner note common to every step, the same value across
 *  all three paths: create document, copy mention, AI invoke. */
export interface WorkPlanScope {
  quantity_type_codes: string[]
  provider_ids: string[]
  /** flowgate.default.0416 T0004 — the one-line planner note common to every step. */
  note: string
  /** flowgate.default.0416 TR0005 (rejection: "[실행 프로바이더] 이거 어디갔냐고") — the provider
   *  this run actually executes with, i.e. the app-wide selection every other dialog
   *  shows (aiProviderStore.selectedProviderId). Distinct from provider_ids, which are
   *  the step-assignment candidates. Empty string when the project has no provider. */
  provider_id: string
}

const props = withDefaults(defineProps<{
  visible: boolean
  parentDocId: string
  projectId: string
  groupId: string
  /** The request currently running in the parent. Doesn't remove the button, only
   *  changes the one-line reason. */
  busyAction?: '' | 'copy' | 'ai'
  /** The failure reason left by the parent request (409 sequence_exhausted /
   *  head_in_progress / run_in_progress …). */
  externalNotice?: string
  /** Another AI run is in progress for this group. Only [AI Invoke] is disabled. */
  aiActive?: boolean
}>(), { busyAction: '', externalNotice: '', aiActive: false })

const emit = defineEmits<{
  'update:visible': [value: boolean]
  'created': [payload: { docId: string; title: string; body: Record<string, unknown> }]
  'copy-mention': [scope: WorkPlanScope]
  'invoke-ai': [payload: { scope: WorkPlanScope; providerId: string }]
}>()

const { t, locale } = useI18n()
const docTypeStore = useDocTypeStore()
const aiProviderStore = useAiProviderStore()

const overlayRef = ref<HTMLElement | null>(null)
const typesError = ref(false)
const providersError = ref(false)
/** Whether the answer for the provider list has arrived. This dialog's shape
 *  (number of sections, primary button) depends on this value. */
const providersSettled = ref(false)
const selectedTypes = ref<Set<string>>(new Set())
const selectedProviders = ref<Set<string>>(new Set())
const creating = ref(false)
const createError = ref('')
/** flowgate.default.0416 T0004 — shared with WorkPlanEditor.vue's plan.defaults.note. */
const note = ref('')
/** flowgate.default.0421 NR0003 §1 — tracks whether the user already typed a value
 *  before the sequence head row's note arrives. Once true, a late-arriving prefill
 *  response won't overwrite it (precedent: ContinuousWorkDialog.vue's touchedNoteSeqs /
 *  touchedProviderSeqs, split in two by 0444 T0007 so a typed note stops freezing the row's
 *  provider as well). */
const noteTouched = ref(false)
/** Whether this opening was auto-filled from the sequence note — used only to
 *  decide whether to show the notice text. */
const noteAutoFilled = ref(false)
/** 0406 T0022 contract: the server is the source of truth for the one-line note's
 *  character limit. WorkPlanEditor.vue:541 and WorkflowDecisionModal.vue:1132
 *  already read the server-provided value; if this dialog alone kept a hardcoded
 *  copy of 1000, it would silently drift the moment the server constant changes
 *  (letting over-limit input through into a 422, or blocking input that should
 *  pass). The constant only remains for before the answer arrives and for the
 *  case it can't be read. */
const noteMaxChars = ref(1000)
/** flowgate.default.0416 TR0005 rev2 — this dialog doesn't hold its own execution
 *  provider value. The app-wide selection is the source of truth (aiProviderStore
 *  decides it in order: the user's saved choice → the server's
 *  default_provider_id → the list's first value), and the header, AI invoke
 *  dialog, and continuous-work dialog all see the same value. rev1's local ref was
 *  the cause of this dialog alone showing a different provider. */
const defaultProviderId = computed(() => aiProviderStore.selectedProviderId)

const countableTypes = computed<WorkPlanCountableType[]>(() => docTypeStore.countableTypes)
const providersLoaded = computed(() => aiProviderStore.providers)

/**
 * 0405 T0011 rev2 — "there is not a single provider to pick." Distinct from
 * failing to load the list (providersError): a failed load calls for a retry, not
 * for treating it as empty.
 */
const noProviders = computed(() =>
  providersSettled.value && !providersError.value && providersLoaded.value.length === 0,
)

/** The single scope the sections build together. Order follows the server's
 *  registration order, with no duplicates. */
const scope = computed<WorkPlanScope>(() => ({
  quantity_type_codes: countableTypes.value
    .filter((item) => selectedTypes.value.has(item.code)).map((item) => item.code),
  provider_ids: providersLoaded.value
    .filter((p) => selectedProviders.value.has(p.id)).map((p) => p.id),
  note: note.value,
  provider_id: defaultProviderId.value,
}))

/** T0004 — the same limit as the server's NOTE_MAX_CHARS (work_plan_service.py).
 *  Over-limit input is blocked on all three execution paths (the server also
 *  rejects it with the same limit in defaults.note validation). */
const noteOverLimit = computed(() => note.value.length > noteMaxChars.value)
/** flowgate.default.0421 NR0003 §3 — the auto-fill notice shows only when the
 *  value was auto-filled and the user hasn't edited it directly yet. */
const showNoteAutoFilled = computed(() => noteAutoFilled.value && !noteTouched.value)

const hasContext = computed(() => !!props.projectId && !!props.groupId && !!props.parentDocId)
const canRun = computed(() =>
  hasContext.value
  && !props.aiActive
  && !noteOverLimit.value
  && scope.value.quantity_type_codes.length > 0
  // For a project with no provider, section ① alone is enough to create.
  && (noProviders.value || scope.value.provider_ids.length > 0),
)

/** P0004 [disabled reason] table — records only the first matching line from top
 *  to bottom. */
const blockReason = computed<string>(() => {
  if (!hasContext.value) return t('main.work_plan_proposal_dialog.block_context')
  if (props.aiActive) return t('main.work_plan_proposal_dialog.block_ai_active')
  if (createError.value) return createError.value
  if (props.externalNotice) return props.externalNotice
  if (creating.value) return t('main.work_plan_proposal_dialog.busy_create')
  if (props.busyAction === 'copy') return t('main.work_plan_proposal_dialog.busy_copy')
  if (props.busyAction === 'ai') return t('main.work_plan_proposal_dialog.busy_ai')
  if (noteOverLimit.value) return t('main.work_plan_proposal_dialog.block_note_too_long')
  if (scope.value.quantity_type_codes.length === 0) return t('main.work_plan_proposal_dialog.block_types')
  if (!noProviders.value && scope.value.provider_ids.length === 0) {
    return t('main.work_plan_proposal_dialog.block_providers')
  }
  return ''
})

const summaryLine = computed(() => {
  let design = 0
  let work = 0
  for (const item of countableTypes.value) {
    if (!selectedTypes.value.has(item.code)) continue
    if (item.unit === 'set') work += 1
    else design += 1
  }
  if (noProviders.value) {
    return t('main.work_plan_proposal_dialog.summary_no_providers', { design, work })
  }
  return t('main.work_plan_proposal_dialog.summary', {
    design, work,
    providers: scope.value.provider_ids.length,
  })
})

const generatedTitle = computed(() => {
  let design = 0
  let work = 0
  for (const item of countableTypes.value) {
    if (!selectedTypes.value.has(item.code)) continue
    if (item.unit === 'set') work += 1
    else design += 1
  }
  return t('main.work_plan_proposal_dialog.generated_title', { design, work }).slice(0, 100)
})

function toggleType(code: string) {
  const next = new Set(selectedTypes.value)
  if (next.has(code)) next.delete(code)
  else next.add(code)
  selectedTypes.value = next
}
function selectAllTypes() {
  selectedTypes.value = new Set(countableTypes.value.map(item => item.code))
}
function clearAllTypes() {
  selectedTypes.value = new Set()
}
function toggleProvider(id: string) {
  const next = new Set(selectedProviders.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  selectedProviders.value = next
}
function selectAllProviders() {
  selectedProviders.value = new Set(providersLoaded.value.map(p => p.id))
}
function clearAllProviders() {
  selectedProviders.value = new Set()
}

async function loadTypes() {
  typesError.value = false
  try {
    await docTypeStore.loadLabels(locale.value)
    if (docTypeStore.countableTypes.length === 0) typesError.value = true
  } catch {
    typesError.value = true
  }
}

/** flowgate.default.0416 TR0005 rev2 — this dialog has no code that fills the
 *  execution provider. loadForProject already decides it in order: saved
 *  selection → server default provider → list's first value, and this dialog only
 *  reads that value (same contract as the other dialogs). */
async function loadProviders() {
  providersError.value = false
  try {
    await aiProviderStore.loadForProject(props.projectId, true)
    if (aiProviderStore.error) providersError.value = true
  } catch {
    providersError.value = true
  } finally {
    providersSettled.value = true
  }
}

/** 0406 T0022 — the server tells us the limit. Since this dialog has no work-plan
 *  document yet, instead of the GET /work-plan that WorkPlanEditor.vue uses, it
 *  reads the same value (STEP_NOTE_MAX_CHARS) carried in the parent document's
 *  sequence response, exactly like WorkflowDecisionModal.vue:1132. If it can't be
 *  read, it silently falls back to the default of 1000 — that's not a reason for
 *  the dialog to fail to open. */
async function loadNoteLimit() {
  if (!props.parentDocId) return
  try {
    const res = await getRequest<any>('/api/v1/workflow/sequence', { doc_id: props.parentDocId })
    noteMaxChars.value = Number((res.data as any)?.note_max_chars) || 1000
    // flowgate.default.0421 NR0003 §1 — the same response already carries every
    // row's note (workflow_decision_service.py get_workflow_sequence). Rather than
    // add a new GET, this just picks the head row's note out of it — the same
    // extraction pattern as AiInvokeDialog.vue:loadSingleStepNote.
    const items = ((res.data as any)?.items ?? []) as { status?: string | null; note?: string | null }[]
    const headIndex = findSequenceHeadIndex(items)
    const fetchedNote = headIndex < items.length ? String(items[headIndex]?.note ?? '').trim() : ''
    // An empty string has nothing to overwrite, so it's always safe, and if the
    // user has already touched the field, the late-arriving value won't overwrite
    // it.
    if (fetchedNote && !noteTouched.value) {
      note.value = fetchedNote
      noteAutoFilled.value = true
    }
  } catch {
    noteMaxChars.value = 1000
  }
}

watch(
  () => props.visible,
  (val) => {
    if (!val) return
    // P0004 [cancel]: reopening resets every section to its initial state (nothing
    // selected).
    // T0004: the planner note is also reset along with the selections.
    selectedTypes.value = new Set()
    selectedProviders.value = new Set()
    note.value = ''
    noteTouched.value = false
    noteAutoFilled.value = false
    // flowgate.default.0416 TR0005 rev2: the execution provider is not reset here.
    // That value isn't this dialog's own — it's the app-wide selection — and if the
    // value chosen in the header or AI invoke dialog vanished just because this one
    // reopened, it would drift out of sync with those dialogs again.
    createError.value = ''
    creating.value = false
    providersError.value = false
    // 0405 T0011 rev2: if this project's list has already been fetched, the dialog
    // opens already knowing the answer, so it's drawn in its final shape from the
    // start. Otherwise it waits until the answer arrives.
    providersSettled.value = aiProviderStore.loadedProjectId === props.projectId
      && !aiProviderStore.error
    void loadTypes()
    void loadProviders()
    void loadNoteLimit()
    setTimeout(() => overlayRef.value?.focus(), 50)
  },
  { immediate: true },
)

function onClose() {
  emit('update:visible', false)
}

/**
 * [Create Document] — reuses the existing creation path as-is. The scope's type
 * selection is carried through quantities: a selected type sends no value at all, so the
 * server fills in its workflow_type_counts-derived value (0 when there is none), and only
 * an unselected type is pinned to 0 explicitly (flowgate.default.0423 T0005 item 11 —
 * selected=1/unselected=0 used to be hardcoded here). The provider carries over as
 * provider_candidates. Steps are still built server-side from quantities (see the
 * P0004 mapping table) — the screen doesn't pick steps, so there's nothing extra to
 * send. For a project with no registered provider, provider_candidates goes out as
 * an empty array (the server only expects an empty array in that case too).
 */
async function onCreateEmpty() {
  if (!canRun.value || creating.value || props.aiActive) return
  creating.value = true
  createError.value = ''
  try {
    const allCodes = countableTypes.value.map((item) => item.code)
    const res = await postRequest<{ ok: boolean; doc_id: string; title: string; body: Record<string, unknown> }>(
      '/api/v1/documents/work-plan',
      {
        parent_doc_id: props.parentDocId,
        title: generatedTitle.value,
        counted_types: allCodes,
        provider_candidates: scope.value.provider_ids,
        quantities: Object.fromEntries(
          allCodes.filter((code) => !selectedTypes.value.has(code)).map((code) => [code, 0]),
        ),
        // flowgate.default.0416 TR0005 rev2: defaults.provider_id is null again.
        // This value would otherwise propagate as-is into the provider_id of every
        // step built from initial_body, but T0004 task 3 pinned this down: "개별
        // 단계/기본 공급자 배정은 생성 후 WorkPlanEditor.vue 의 기존 책임으로 남긴다."
        // Carrying the execution provider in here the way rev1 did would (1) make
        // that assignment happen behind the user's back, (2) leave no blank option
        // in the box to pick "unassigned" with, and (3) silently create a step with
        // no display name if the provider falls outside the candidates. The
        // execution provider is instead carried by the two paths that actually run
        // AI ([AI Invoke] / [Copy Mention]).
        defaults: { provider_id: null, note: note.value },
        type_providers: {},
      },
    )
    const data = res.data
    emit('created', { docId: data.doc_id, title: data.title, body: data.body })
    emit('update:visible', false)
  } catch (e: any) {
    const detail = e?.response?.data
    createError.value = extractApiErrorMessage(e, detail?.message || String(e))
  } finally {
    creating.value = false
  }
}

function onCopyMention() {
  if (!canRun.value || props.busyAction || props.aiActive) return
  emit('copy-mention', scope.value)
}

function onInvokeAi() {
  if (!canRun.value || props.busyAction || props.aiActive || noProviders.value) return
  // flowgate.default.0416 TR0005: the provider used for this run is the value of
  // the newly added execution-provider box. It falls back to the first candidate
  // only in the rare case where the list is empty and the value hasn't been filled
  // yet.
  emit('invoke-ai', {
    scope: scope.value,
    providerId: defaultProviderId.value || scope.value.provider_ids[0],
  })
}
</script>

<style scoped>
/* Sections + button row. The button row never changes its count or positions in
   any state after the dialog is rendered (P0004). */
.modal-wpp { width: 1040px; max-width: 96vw; }
/* 0405 T0011 rev2 — a dialog with no section ② has just one section. Keep the
   remaining single section from having all of 1040px to itself. */
.modal-wpp--solo { width: 620px; }
.wpp-title-ico { color: var(--primary, #4f46e5); margin-right: 6px; }
/* 0405 T0011 — the badge marking this dialog's main task. Pins down visually that
   the title is [Create Work Plan]. */
.wpp-main-task {
  margin-left: 8px; padding: 1px 8px; border-radius: 999px; vertical-align: middle;
  font-size: .68rem; font-weight: 700; letter-spacing: .02em;
  color: var(--primary, #2563eb); background: var(--primary-l, #eff6ff);
  border: 1px solid var(--primary-b, #bfdbfe);
}
.wpp-body { padding: 16px 18px; display: flex; flex-direction: column; gap: 12px; }
.wpp-loading {
  align-items: center; justify-content: center; min-height: 120px;
  font-size: .82rem; color: var(--text-m, #64748b);
}
.wpp-intro {
  margin: 0; padding: 10px 12px; font-size: .78rem; line-height: 1.55;
  color: var(--text-s, #475569); background: var(--primary-l, #eff6ff);
  border: 1px solid var(--primary-b, #bfdbfe); border-radius: var(--r, 6px);
}
.wpp-cols { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; align-items: start; }
.wpp-cols--solo { grid-template-columns: 1fr; }
.wpp-sec { display: flex; flex-direction: column; min-width: 0; }
.wpp-sec-hd { display: flex; align-items: center; gap: 7px; margin-bottom: 8px; }
.wpp-sec-no {
  width: 18px; height: 18px; border-radius: 50%; background: var(--primary, #2563eb); color: #fff;
  font-size: .66rem; font-weight: 700; display: inline-flex; align-items: center; justify-content: center;
  flex-shrink: 0;
}
.wpp-sec-title { font-size: .82rem; font-weight: 700; color: var(--text, #1e293b); white-space: nowrap; }
.wpp-count-pill {
  font-size: .68rem; font-weight: 700; padding: 1px 7px; border-radius: 999px;
  background: var(--primary-l, #eff6ff); color: var(--primary, #2563eb); font-variant-numeric: tabular-nums;
}
.wpp-count-pill.zero { background: var(--danger-l, #fee2e2); color: var(--danger, #dc2626); }
.wpp-sec-acts { margin-left: auto; display: inline-flex; gap: 4px; }
.wpp-mini-btn {
  padding: 2px 9px; font-size: .68rem; border: 1px solid var(--border, #e2e8f0);
  border-radius: var(--r-sm, 4px); background: #fff; color: var(--text-m, #64748b); cursor: pointer;
}
.wpp-scroll {
  height: 288px; overflow-y: auto; padding: 10px; display: flex; flex-direction: column; gap: 6px;
  border: 1px solid var(--border, #e2e8f0); border-radius: var(--r, 6px); background: #fff;
}
.wpp-check {
  display: flex; align-items: center; gap: 7px; padding: 7px 9px; min-width: 0;
  border: 1px solid var(--border, #e2e8f0); border-radius: var(--r, 6px);
  cursor: pointer; user-select: none; transition: all .1s;
}
.wpp-check:hover { background: var(--surface-h, #f8fafc); border-color: var(--border-d, #cbd5e1); }
.wpp-check.on { border-color: var(--primary, #2563eb); background: var(--primary-l, #eff6ff); }
.wpp-check.locked { cursor: not-allowed; opacity: .6; }
.wpp-check-box {
  width: 15px; height: 15px; border: 1.5px solid var(--border-d, #cbd5e1); border-radius: 4px;
  background: #fff; flex-shrink: 0; display: flex; align-items: center; justify-content: center;
}
.wpp-check.on .wpp-check-box { background: var(--primary, #2563eb); border-color: var(--primary, #2563eb); }
.wpp-check-ico { font-size: .55rem; color: transparent; }
.wpp-check.on .wpp-check-ico { color: #fff; }
.wpp-check-tag { font-size: .62rem; padding: 1px 5px; flex-shrink: 0; }
.wpp-check-name {
  font-size: .78rem; color: var(--text-s, #475569); min-width: 0;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.wpp-check.on .wpp-check-name { color: var(--text, #1e293b); font-weight: 600; }
.wpp-lock-note { margin-left: auto; font-size: .66rem; color: var(--text-m, #94a3b8); white-space: nowrap; }
.wpp-hint { margin: 6px 0 0; font-size: .7rem; line-height: 1.5; color: var(--text-m, #64748b); }
.wpp-empty-hint { font-size: .74rem; color: var(--text-m); font-style: italic; margin: 4px 0; }
.wpp-load-error { display: flex; align-items: center; gap: 8px; font-size: .78rem; color: var(--danger, #dc2626); }
/* flowgate.default.0416 TR0005 (rejection "박스 높낮이는 하나도 안맞고"): places the
   execution-provider box and the note input side by side in one row, and pins both
   controls' (select/input) height at 30px to match — the labels share the same
   height and font, so the alignment point matches too (align-items: flex-start). */
.wpp-note-row { display: flex; align-items: flex-start; gap: 20px; }
.wpp-field-block { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.wpp-provider-block { flex: 0 0 220px; }
.wpp-note-block { flex: 1; min-width: 0; }
.wpp-note-label { font-size: .7rem; font-weight: 700; color: var(--text-m, #64748b); white-space: nowrap; }
.wpp-provider-select { width: 100%; }
.wpp-provider-select :deep(.aip-select-input) {
  height: 30px; box-sizing: border-box; font-size: .78rem;
}
.wpp-note-field { display: flex; flex: 1; min-width: 0; flex-direction: column; gap: 2px; }
.wpp-note-input {
  width: 100%; min-width: 0; height: 30px; box-sizing: border-box;
  padding: 0 9px; font-size: .78rem; border: 1px solid var(--border, #e2e8f0);
  border-radius: var(--r-sm, 4px);
}
.wpp-note-input.is-over-limit { border-color: var(--danger, #dc2626); background: color-mix(in srgb, var(--danger, #dc2626) 6%, #fff); }
.wpp-note-count { color: var(--text-m, #94a3b8); font-size: .62rem; line-height: 1.15; text-align: right; }
.wpp-note-count.is-over-limit { color: var(--danger, #dc2626); font-weight: 700; }
.wpp-note-auto { margin: 0; color: var(--primary, #2563eb); font-size: .62rem; line-height: 1.15; text-align: right; }
.wpp-notice {
  display: flex; align-items: flex-start; gap: 8px; padding: 9px 11px; min-height: 38px;
  font-size: .77rem; line-height: 1.55; box-sizing: border-box;
  color: var(--text-s, #475569); background: var(--primary-l, #eff6ff);
  border: 1px solid var(--primary-b, #bfdbfe); border-left: 3px solid var(--primary, #2563eb);
  border-radius: var(--r, 6px);
}
.wpp-notice.warn {
  color: var(--warning, #b45309); background: var(--warning-l, #fef3c7);
  border-color: #fde68a; border-left-color: var(--warning, #b45309);
}
/* 0405 T0011 rev1 — when a provider exists, the dialog's blue primary button is
   [AI Invoke], and only that.
   rev2 — when no provider exists, [AI Invoke] is gone, and [+ Create Document]
   takes over the primary-button slot and moves to the far right. The DOM order
   stays the same; a single `order` line moves its position. */
.wpp-ft { display: flex; gap: 8px; justify-content: flex-end; }
.wpp-main-btn { font-weight: 700; }
.wpp-ft-last { order: 9; }

@media (max-width: 1000px) {
  .wpp-cols { grid-template-columns: 1fr; }
  .wpp-scroll { height: 200px; }
}
</style>
