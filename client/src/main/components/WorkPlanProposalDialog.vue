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
            <!-- 0405 T0011: 이 창의 주인공은 [AI 호출]이 아니라 작업계획을 만드는 일이다.
                 제목 옆의 이 표가 그 사실을 화면에 그대로 적어 둔다. -->
            <span class="wpp-main-task" data-test="wpp-main-task">
              {{ t('main.work_plan_proposal_dialog.main_task') }}
            </span>
          </div>
          <button type="button" class="modal-close" data-test="wpp-close" @click="onClose">
            <AppIcon name="x" />
          </button>
        </div>

        <!-- 0405 T0011 rev2 — 공급자 목록이 오기 전에는 이 창의 모양이 아직 정해지지 않았다.
             칸이 하나인지 둘인지도, 맨 오른쪽 주버튼이 [AI 호출]인지 [+ 문서생성]인지도 그
             답에 달렸다. 그래서 답이 오기 전에는 고를 것도 누를 것도 그리지 않는다 — 답이
             온 뒤 한 번만 그리고, 그 뒤로는 어떤 상태에서도 움직이지 않는다. -->
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
            <!-- ① 장수를 셀 타입 -->
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

            <!-- ② 후보 공급자 — 0405 T0011 rev2 (반려: "AI공급자 선택할게 없으면
                 [2 후보공급자]는 안나오게 하고 1만 선택하고 생성할수 있게 해야하지 않겠니?").
                 고를 것이 하나도 없는 칸은 그리지 않는다. 읽지 못한 경우(providersError)는
                 다르다 — 그때는 칸을 남기고 [다시 시도]를 준다. -->
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

          <!-- flowgate.default.0416 TR0005 (반려: "[실행 프로바이더] 이거 어디갔냐고" /
               "\"전달멘트\" 랑 박스 높낮이는 하나도 안맞고"): 이번 실행에 쓸 프로바이더를 고르는
               박스. rev2 에서 계약을 다른 다이얼로그와 글자 그대로 같게 맞췄다 — 이 창은 값을
               따로 들고 있지 않고 AiInvokeDialog.vue:41,44 / AppHeader.vue:170 /
               ContinuousWarningDialog.vue:150 / WorkflowDecisionModal.vue:430 처럼
               aiProviderStore.selectedProviderId 를 읽고 aiProviderStore.selectProvider 로
               되쓴다. rev1 은 로컬 ref 를 providersLoaded[0] 으로 채웠던 탓에, 프로젝트 기본
               공급자나 헤더에서 고른 값이 A 인데 이 창만 B 를 보여 주고 B 로 실행할 수 있었고,
               창을 닫으면 고른 값도 사라졌다. 후보 다중선택(② 칸)과는 여전히 다른 값이다.
               플래너 멘트와 한 행에 나란히 두고 두 입력의 높이를 맞춰서
               (.wpp-provider-select/.wpp-note-input 모두 30px) 나란한 두 박스가 서로 다른
               높이로 어긋나 보이지 않게 한다. 실행 중에는 옆 입력과 똑같이 :disabled 로
               잠근다 — 이벤트만 무시하면 화면의 값과 실제로 쓰는 값이 갈라진다. -->
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
                 없는거지?"): 작업계획 전체 단계에 공통으로 붙는 플래너 멘트. WorkPlanEditor.vue
                 의 plan.defaults.note 입력과 같은 계약(placeholder·글자 수 표시·초과 상태)을
                 재사용한다. 문서생성·멘트복사·AI호출 세 경로가 이 값을 scope.note 로 그대로
                 나른다. -->
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
                <!-- flowgate.default.0421 NR0003 §3 — 값이 자동으로 채워졌을 때만 뜨는 안내.
                     사용자가 칸을 직접 고치면(noteTouched) 사라진다. 전용 data-test와 자기
                     CSS 클래스를 쓴다 — 공용 클래스 재사용은 findAll 단언을 흔든다. -->
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

          <!-- P0004 [비활성 사유]: this line always occupies its slot — a reason when there is
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
            <!-- 0405 T0011 rev2 (반려 3: "AI공급자 선택할게 없으면 [AI호출이 의미 없잖아]
                 [+ 문서생성] 이 맨 우측으로 오게하고 이걸 강조해야지"): 공급자가 없으면 이
                 버튼이 파란 주버튼이 되고 wpp-ft-last 규칙이 이 버튼을 맨 오른쪽으로 보낸다.
                 공급자가 있으면 예전 그대로 두 번째 자리의 흰 보조 버튼이다. -->
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
            <!-- 고를 공급자가 하나도 없으면 이 버튼은 누를 수 있어도 할 일이 없다. 비활성으로
                 남겨 두지 않고 아예 그리지 않는다. -->
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
 * 다음 액션이 작업계획(WP)일 때 여는 전용 제안 다이얼로그 — flowgate.default.0405 P0004.
 *
 * 칸이 하나의 범위 객체를 만들고, 버튼들이 그 하나를 그대로 나른다. 범위 객체의 이름은 새로
 * 짓지 않고 작업계획 편집 화면의 범위 고르기 창(WorkPlanAiScopeDialog)이 이미 쓰는 항목
 * 이름을 그대로 쓴다.
 *
 * 0405 T0011 rev1 — 사용자 반려 두 줄이 이 창의 모양을 정한다.
 *   "문서생성이 아니라 AI호출을 강조해야지"  → 파란 주버튼은 [AI 호출] 하나뿐이다.
 *   "맡길 단계??? 이건 대체 왜나와"          → 단계를 고르는 칸을 없앴다. 단계 배분은 언제나
 *                                             작업계획을 쓰는 쪽 몫이다.
 *
 * 0405 T0011 rev2 — 사용자 반려 두 줄이 "공급자가 없는 프로젝트"의 모양을 정한다.
 *   "AI공급자 선택할게 없으면 [2 후보공급자]는 안나오게 하고 1만 선택하고 생성할수 있게"
 *   "AI공급자 선택할게 없으면 [AI호출이 의미 없잖아] [+ 문서생성] 이 맨 우측으로 오게하고
 *    이걸 강조해야지"
 *   → 등록된 공급자가 0개면 ② 칸도 [AI 호출]도 그리지 않고, ① 칸만으로 만들 수 있으며,
 *     [+ 문서생성]이 맨 오른쪽의 파란 주버튼이 된다.
 *   그 답(공급자 개수)이 오기 전에는 아무 버튼도 그리지 않는다. 창이 그려진 뒤에 버튼이
 *   자리를 옮기는 일이 없어야 하기 때문이다.
 *
 * 버튼별 책임 분담:
 *   [문서생성]     이 창이 직접 POST /documents/work-plan (기존 생성 경로와 같은 바디)
 *   [멘트복사]     부모가 POST /workflow/advance → 클립보드 (쓰기가 클릭의 사용자 제스처
 *                  안에 남아야 해서 부모의 지연 복사 경로를 그대로 쓴다)
 *   [AI 호출]      부모가 POST /ai-invoke/start — 공급자가 있을 때 이 창의 주버튼
 * 부모가 도는 동안의 busy/실패 사유는 busyAction·externalNotice 로 되돌아온다. 창은 요청 전에
 * 닫지 않는다 — 성공했을 때만 부모가 visible 을 내린다.
 */
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { extractApiErrorMessage, getRequest, postRequest } from '@shared/api'
import AppIcon from '@shared/AppIcon.vue'
import AiProviderSelect from './AiProviderSelect.vue'
import { useDocTypeStore, type WorkPlanCountableType } from '../stores/docTypeStore'
import { useAiProviderStore } from '../stores/aiProvider'
import { findSequenceHeadIndex } from '../composables/useSequenceStepNote'

/** P0004 [범위 페이로드] — 세 갈래가 함께 쓰는 한 가지 서식. 0405 T0011 rev1 에서
 *  step_keys 를 뺐다(사람이 단계를 고르는 칸이 없어졌다). rev2 에서 provider_ids 는
 *  등록된 공급자가 없는 프로젝트에서 빈 배열로 나간다. 0416 T0004 에서 note 를 더했다
 *  — 모든 단계에 공통으로 붙는 플래너 멘트, 문서생성·멘트복사·AI호출 세 갈래 모두 같은 값. */
export interface WorkPlanScope {
  quantity_type_codes: string[]
  provider_ids: string[]
  /** flowgate.default.0416 T0004 — the one-line planner note common to every step. */
  note: string
  /** flowgate.default.0416 TR0005 (반려: "[실행 프로바이더] 이거 어디갔냐고") — the provider
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
  /** 부모가 도는 중인 요청. 버튼을 없애지 않고 사유 한 줄만 바꾼다. */
  busyAction?: '' | 'copy' | 'ai'
  /** 부모 요청이 남긴 실패 사유(409 sequence_exhausted / head_in_progress / run_in_progress …). */
  externalNotice?: string
  /** 이 그룹에서 다른 AI 실행이 돌고 있다. [AI 호출]만 비활성으로 둔다. */
  aiActive?: boolean
}>(), { busyAction: '', externalNotice: '', aiActive: false })

const emit = defineEmits<{
  'update:visible': [value: boolean]
  'created': [payload: { docId: string; title: string; body: Record<string, unknown> }]
  'copy-mention': [scope: WorkPlanScope]
  'invoke-ai': [payload: { scope: WorkPlanScope; providerId: string }]
}>()

const { t } = useI18n()
const docTypeStore = useDocTypeStore()
const aiProviderStore = useAiProviderStore()

const overlayRef = ref<HTMLElement | null>(null)
const typesError = ref(false)
const providersError = ref(false)
/** 공급자 목록의 답이 도착했는가. 이 창의 모양(칸 개수·주버튼)이 이 값에 달렸다. */
const providersSettled = ref(false)
const selectedTypes = ref<Set<string>>(new Set())
const selectedProviders = ref<Set<string>>(new Set())
const creating = ref(false)
const createError = ref('')
/** flowgate.default.0416 T0004 — shared with WorkPlanEditor.vue's plan.defaults.note. */
const note = ref('')
/** flowgate.default.0421 NR0003 §1 — 시퀀스 머리 행의 note가 도착하기 전에 사용자가 이미
 *  입력했는지를 지킨다. true가 되면 늦게 오는 프리필 응답이 값을 덮지 않는다
 *  (선례: ContinuousWorkDialog.vue의 touchedSeqs). */
const noteTouched = ref(false)
/** 이번 열림에서 시퀀스 note로 자동 채워졌는가 — 안내 문구를 보일지에만 쓴다. */
const noteAutoFilled = ref(false)
/** 0406 T0022 계약: 한 줄 멘트의 상한은 서버가 정본이다. WorkPlanEditor.vue:541 과
 *  WorkflowDecisionModal.vue:1132 가 이미 서버가 준 값을 읽고 있고, 이 창만 1000 을 베껴
 *  들고 있으면 서버 상수가 바뀌는 순간 여기만 조용히 어긋난다(초과 입력을 통과시켜 422 를
 *  맞거나, 통과할 입력을 막는다). 답이 오기 전과 못 읽은 경우의 값만 상수로 남긴다. */
const noteMaxChars = ref(1000)
/** flowgate.default.0416 TR0005 rev2 — 이 창은 실행 프로바이더 값을 따로 들고 있지 않는다.
 *  앱 공통 선택(사용자가 저장한 선택 → 서버의 default_provider_id → 목록 첫 값 순으로
 *  aiProviderStore 가 정한다)이 정본이고, 헤더·AI 호출 창·연속 작업 창이 모두 같은 값을
 *  본다. rev1 의 로컬 ref 는 이 창에서만 다른 공급자를 보이게 하는 원인이었다. */
const defaultProviderId = computed(() => aiProviderStore.selectedProviderId)

const countableTypes = computed<WorkPlanCountableType[]>(() => docTypeStore.countableTypes)
const providersLoaded = computed(() => aiProviderStore.providers)

/**
 * 0405 T0011 rev2 — "고를 공급자가 하나도 없다". 목록을 못 읽은 것(providersError)과는
 * 구분한다: 못 읽은 것은 다시 시도할 일이지 없는 것이 아니다.
 */
const noProviders = computed(() =>
  providersSettled.value && !providersError.value && providersLoaded.value.length === 0,
)

/** 칸들이 함께 만드는 하나의 범위. 순서는 서버 등록 순서를 따르고 중복은 없다. */
const scope = computed<WorkPlanScope>(() => ({
  quantity_type_codes: countableTypes.value
    .filter((item) => selectedTypes.value.has(item.code)).map((item) => item.code),
  provider_ids: providersLoaded.value
    .filter((p) => selectedProviders.value.has(p.id)).map((p) => p.id),
  note: note.value,
  provider_id: defaultProviderId.value,
}))

/** T0004 — 서버 NOTE_MAX_CHARS(work_plan_service.py)와 같은 상한. 초과 입력은 세 실행
 *  경로 모두 막는다(서버도 defaults.note 검증에서 같은 상한으로 거절한다). */
const noteOverLimit = computed(() => note.value.length > noteMaxChars.value)
/** flowgate.default.0421 NR0003 §3 — 자동 채움 안내는 값이 자동으로 채워졌고 사용자가
 *  아직 직접 고치지 않았을 때만 보인다. */
const showNoteAutoFilled = computed(() => noteAutoFilled.value && !noteTouched.value)

const hasContext = computed(() => !!props.projectId && !!props.groupId && !!props.parentDocId)
const canRun = computed(() =>
  hasContext.value
  && !props.aiActive
  && !noteOverLimit.value
  && scope.value.quantity_type_codes.length > 0
  // 공급자가 없는 프로젝트에서는 ① 칸만으로 만든다.
  && (noProviders.value || scope.value.provider_ids.length > 0),
)

/** P0004 [비활성 사유] 표 — 위에서부터 처음 걸리는 한 줄만 적는다. */
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
    await docTypeStore.loadLabels()
    if (docTypeStore.countableTypes.length === 0) typesError.value = true
  } catch {
    typesError.value = true
  }
}

/** flowgate.default.0416 TR0005 rev2 — 실행 프로바이더를 채우는 코드는 이 창에 없다.
 *  loadForProject 가 저장된 선택 → 서버 기본 공급자 → 목록 첫 값 순으로 이미 정하고,
 *  이 창은 그 값을 읽기만 한다(다른 다이얼로그와 같은 계약). */
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

/** 0406 T0022 — 상한은 서버가 말해 준다. 이 창에는 아직 작업계획 문서가 없으므로
 *  WorkPlanEditor.vue 가 쓰는 GET /work-plan 대신, 부모 문서의 진행 순서 응답이 싣고 있는
 *  같은 값(STEP_NOTE_MAX_CHARS)을 WorkflowDecisionModal.vue:1132 와 똑같이 읽는다.
 *  못 읽으면 조용히 기본값 1000 으로 남는다 — 창이 열리지 못할 이유는 아니다. */
async function loadNoteLimit() {
  if (!props.parentDocId) return
  try {
    const res = await getRequest<any>('/api/v1/workflow/sequence', { doc_id: props.parentDocId })
    noteMaxChars.value = Number((res.data as any)?.note_max_chars) || 1000
    // flowgate.default.0421 NR0003 §1 — 같은 응답이 이미 모든 행의 note를 싣는다
    // (workflow_decision_service.py get_workflow_sequence). 새 GET을 추가하지 않고 머리 행의
    // note만 꺼내 쓴다. AiInvokeDialog.vue:loadSingleStepNote와 같은 추출 패턴이다.
    const items = ((res.data as any)?.items ?? []) as { status?: string | null; note?: string | null }[]
    const headIndex = findSequenceHeadIndex(items)
    const fetchedNote = headIndex < items.length ? String(items[headIndex]?.note ?? '').trim() : ''
    // 빈 문자열이면 덮을 것이 없으므로 항상 안전하고, 사용자가 이미 손댔다면 늦게 온 값이
    // 그 입력을 덮지 않는다.
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
    // P0004 [취소]: 다시 열면 칸은 모두 초기 상태(아무것도 고르지 않음)로 돌아간다.
    // T0004: 플래너 멘트도 선택값과 함께 초기화한다.
    selectedTypes.value = new Set()
    selectedProviders.value = new Set()
    note.value = ''
    noteTouched.value = false
    noteAutoFilled.value = false
    // flowgate.default.0416 TR0005 rev2: 실행 프로바이더는 여기서 초기화하지 않는다. 그 값은
    // 이 창의 것이 아니라 앱 공통 선택이고, 헤더·AI 호출 창에서 고른 값이 다시 열었다고
    // 사라지면 그 창들과 다시 어긋난다.
    createError.value = ''
    creating.value = false
    providersError.value = false
    // 0405 T0011 rev2: 이 프로젝트의 목록을 이미 받아 둔 상태라면 답을 아는 채로 여는
    // 것이므로 처음부터 최종 모양으로 그린다. 아니면 답이 올 때까지 기다린다.
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
 * [문서생성] — 기존 생성 경로를 그대로 쓴다. 범위의 타입 선택은 quantities 에서 골라
 * 넘긴다: 선택한 타입은 값을 아예 보내지 않아 서버가 workflow_type_counts 유도값(없으면
 * 0)을 채우게 하고, 선택하지 않은 타입만 0 을 명시해 강제한다(flowgate.default.0423
 * T0005 item 11 — 예전에는 선택=1/미선택=0 을 여기서 하드코딩했다). 공급자는
 * provider_candidates 로 옮긴다. 단계는 지금도 서버가 quantities 에서 만든다
 * (P0004 옮기기 표) — 화면이 단계를 고르지 않으니 보낼 것도 없다. 등록된 공급자가 없는
 * 프로젝트에서는 provider_candidates 가 빈 배열로 나간다(서버도 그때만 빈 배열을 받는다).
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
        // flowgate.default.0416 TR0005 rev2: defaults.provider_id 는 다시 null 이다.
        // 이 값은 initial_body 에서 만들어지는 모든 단계의 provider_id 로 그대로 번지는데,
        // T0004 작업 3 은 "개별 단계/기본 공급자 배정은 생성 후 WorkPlanEditor.vue 의 기존
        // 책임으로 남긴다"고 못 박았다. rev1 처럼 실행 프로바이더를 여기에 실으면 (1) 그
        // 배정이 사람 몰래 일어나고 (2) 박스에 빈 옵션이 없어 "배정하지 않음"을 고를 수단이
        // 없으며 (3) 후보 밖 공급자면 표시 이름 없는 단계가 조용히 만들어진다. 실행
        // 프로바이더는 AI 를 실제로 돌리는 두 경로([AI 호출]·[멘트복사])가 나른다.
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
  // flowgate.default.0416 TR0005: 이번 실행에 쓸 프로바이더는 새로 생긴 실행 프로바이더
  // 박스의 값이다. 목록이 비어 값이 아직 못 채워진 드문 경우에만 후보 첫 값으로 물러선다.
  emit('invoke-ai', {
    scope: scope.value,
    providerId: defaultProviderId.value || scope.value.provider_ids[0],
  })
}
</script>

<style scoped>
/* 칸 + 버튼 줄. 버튼 줄은 창이 그려진 뒤 어떤 상태에서도 개수와 자리를 바꾸지 않는다 (P0004). */
.modal-wpp { width: 1040px; max-width: 96vw; }
/* 0405 T0011 rev2 — ② 칸이 없는 창은 한 칸짜리다. 남은 한 칸이 1040px 를 혼자 쓰지 않게 한다. */
.modal-wpp--solo { width: 620px; }
.wpp-title-ico { color: var(--primary, #4f46e5); margin-right: 6px; }
/* 0405 T0011 — 이 창의 메인 작업 표시. 제목이 [작업계획 생성]이라는 것을 눈으로 못 박는다. */
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
/* flowgate.default.0416 TR0005 (반려 "박스 높낮이는 하나도 안맞고"): 실행 프로바이더 박스와
   전달 멘트 입력을 한 행에 나란히 두고, 두 컨트롤(select/입력창)의 높이를 30px 로 못박아
   맞춘다 — 라벨이 같은 높이·폰트라 정렬점도 같다(align-items: flex-start). */
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
/* 0405 T0011 rev1 — 공급자가 있는 창의 파란 주버튼은 [AI 호출] 하나뿐이다.
   rev2 — 공급자가 없는 창에는 [AI 호출]이 없고, [+ 문서생성]이 그 주버튼 자리를 넘겨받아
   맨 오른쪽으로 간다. DOM 순서는 그대로 두고 order 한 줄로 자리를 옮긴다. */
.wpp-ft { display: flex; gap: 8px; justify-content: flex-end; }
.wpp-main-btn { font-weight: 700; }
.wpp-ft-last { order: 9; }

@media (max-width: 1000px) {
  .wpp-cols { grid-template-columns: 1fr; }
  .wpp-scroll { height: 200px; }
}
</style>
