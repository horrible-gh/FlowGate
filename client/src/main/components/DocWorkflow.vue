<template>
  <div
    v-if="stepStates.length > 0 || isWorkPlan || (isWorkflowRoot && (workflowDecided === false || decidedEmpty))"
    class="wf-section"
    :class="{ collapsed: sequenceCollapsed }"
  >
    <div class="sec-title">
      <AppIcon name="flow-arrow" /> {{ t('main.doc_workflow.title') }}
      <button
        v-if="workflowDecided && !readOnly"
        type="button"
        class="wf-edit-btn"
        @click="showEditModal = true"
      >
        <AppIcon name="note-pencil" />
        {{ t('main.doc_workflow.edit_btn') }}
      </button>
      <button
        v-if="!readOnly && isWorkflowRoot && workflowDecided === false"
        type="button"
        class="wf-edit-btn"
        @click="emit('decide-workflow')"
      >
        <AppIcon name="sliders-horizontal" />
        {{ t('main.review_action_bar.btn_manual_decision') }}
      </button>
      <!-- 0399 D0010 §3.1 / §6.1 — the one thing this design adds to any screen. It exists
           only on a work plan; every other document looks exactly as it did. Why it lives
           here and not in the action list below the document: the action list is unchanged
           by decision (D0010 §6.1), and this button acts on the sequence strip it sits on. -->
      <span v-if="isWorkPlan && !readOnly" class="wf-apply-wrap">
        <!-- M0020: the button never changes shape. It is not disabled, it grows no label
             beside itself, and nothing about it depends on a request that is still in the
             air — that is what made it flicker through three different states on load.
             Whatever the plan turns out to be is said inside the menu, after a click. -->
        <button
          type="button"
          class="wf-apply-btn"
          :class="{ 'is-open': applyMenuOpen }"
          :aria-expanded="applyMenuOpen"
          @click.stop="toggleApplyMenu"
        >
          <AppIcon name="clipboard-text" />
          {{ t('main.work_plan_pour.button') }}
          <AppIcon name="caret-down" />
        </button>
        <div v-if="applyMenuOpen" class="wf-apply-menu" @click.stop>
          <div class="wf-apply-hd">
            {{ applyState === 'ready'
              ? t('main.work_plan_pour.menu_title', { doc: wpShortCode, n: planStepCount })
              : t('main.work_plan_pour.menu_title_plain', { doc: wpShortCode }) }}
          </div>
          <template v-if="applyState === 'ready'">
            <button
              v-for="opt in applyOptions"
              :key="opt.mode"
              type="button"
              class="wf-apply-item"
              @click="choosePourMode(opt.mode)"
            >
              <AppIcon :name="opt.icon" />
              <span class="wf-apply-body">
                <span class="wf-apply-name">{{ t(`main.work_plan_pour.mode_${opt.mode}`) }}</span>
                <span class="wf-apply-desc">
                  {{ t(`main.work_plan_pour.mode_${opt.mode}_desc`, { n: planStepCount }) }}
                </span>
                <span class="wf-apply-delta">
                  {{ t('main.work_plan_pour.delta', {
                    before: opt.change.before, after: opt.change.after,
                  }) }}
                  <span v-if="opt.change.deleted > 0" class="minus">−{{ opt.change.deleted }}</span>
                  <span class="plus">+{{ opt.change.added }}</span>
                </span>
              </span>
            </button>
          </template>
          <!-- Loading and failure live here, inside the opened menu, for the same reason:
               a person only sees them because they asked, so nothing moves on its own. -->
          <div v-else class="wf-apply-msg" :class="{ 'is-warn': applyState !== 'loading' }">
            <AppIcon :name="applyState === 'loading' ? 'spinner' : 'warning'" :spin="applyState === 'loading'" />
            <span class="wf-apply-msg-text">
              {{ t(`main.work_plan_pour.blocked_${applyState}`) }}
            </span>
            <button
              v-if="applyState === 'error' || applyState === 'unreadable'"
              type="button"
              class="wf-apply-retry"
              @click="fetchCandidates(true)"
            >
              {{ t('main.work_plan_pour.retry') }}
            </button>
          </div>
          <div class="wf-apply-foot">
            <AppIcon name="info" />
            <span>{{ t('main.work_plan_pour.menu_foot') }}</span>
          </div>
        </div>
      </span>
      <button
        type="button"
        class="wf-collapse-btn"
        :aria-expanded="!sequenceCollapsed"
        :title="sequenceCollapsed ? t('main.doc_workflow.expand') : t('main.doc_workflow.collapse')"
        @click.stop="toggleSequenceCollapsed"
      >
        <AppIcon name="caret-down" class="wf-caret" />
      </button>
    </div>
    <div class="wf-flow">
      <!-- Workflow root undecided → placeholder -->
      <template v-if="isWorkflowRoot && workflowDecided === false">
        <div class="wf-unit">
          <div class="wf-step wf-undecided current">
            <AppIcon name="question" />
            <span class="s-lbl">{{ docTypeStore.getLabel(tab.typeCode ?? 'R') }}</span>
          </div>
          <span class="wf-arrow"><AppIcon name="caret-right" /></span>
        </div>
        <div class="wf-unit">
          <div class="wf-step wf-undecided">
            <AppIcon name="question" />
            <span class="s-lbl">{{ t('main.doc_workflow.undecided') }}</span>
          </div>
        </div>
      </template>
      <!-- 0119 B0001 (NR0003 §6-B): a decided workflow whose every step was deleted
           (decided-but-empty). The normal strip would be blank and the section was
           previously hidden entirely — stranding the [Edit] affordance and leaving the
           workflow unrecoverable. Show a recovery hint; the [Edit] button above re-adds
           steps (edit_workflow_pending inserts pending items into the existing sequence). -->
      <template v-else-if="decidedEmpty">
        <div class="wf-empty-recover">
          <AppIcon name="warning-circle" />
          <span>{{ t('main.doc_workflow.decided_empty') }}</span>
        </div>
      </template>
      <!-- 0403 NR0004 F4 — the work-plan tab for a group that has no workflow yet. Until now
           this cell did not draw at all, so there was not even an [작업계획 적용] button, and
           "계획을 먼저 세우고 그것으로 워크플로를 구성한다" was blocked on screen. Write what
           can be done here instead of an empty strip. -->
      <template v-else-if="stepStates.length === 0">
        <div class="wf-empty-recover">
          <AppIcon name="info" />
          <span>{{ t('main.doc_workflow.no_sequence_yet') }}</span>
        </div>
      </template>
      <!-- Normal: v-for over stepStates -->
      <template v-else>
        <div v-for="(s, idx) in stepStates" :key="s.code + idx" class="wf-unit">
          <div
            class="wf-step"
            :class="[
              s.className,
              !readOnly && (s.visual === 'highlight' || s.visual === 'current') && canNextAction ? 'wf-current-clickable' : '',
              !readOnly && s.visual === 'done' ? 'wf-done-clickable' : '',
              !readOnly && isReturnTarget(idx) ? 'wf-return-clickable' : '',
            ]"
            :title="stepHint(s, idx)"
            @click="onStepClick(s, idx)"
          >
            <AppIcon :name="s.iconClass" />
            <span class="s-lbl">{{ docTypeStore.getLabel(s.code) }}</span>
            <!-- 0332 D0005 §6.1 — 칸의 모양(색·아이콘·클릭 동작)은 그대로 두고 표식 하나만
                 오른쪽 위에 얹는다. 커밋은 진행 상태가 아니라 그 칸에 딸린 부가 사실이라
                 진행을 나타내는 표현을 빼앗으면 안 되고, 칸 클릭은 이미 세 가지 뜻을
                 갖고 있어 네 번째를 얹을 자리가 없다. -->
            <span
              v-if="commitOf(idx)"
              class="wf-commit-mark"
              :class="commitOf(idx)?.state === 'canceled' ? 'is-canceled' : 'is-live'"
            >
              <AppIcon :name="commitOf(idx)?.state === 'canceled' ? 'arrow-counter-clockwise' : 'git-commit'" />
            </span>
          </div>
          <span v-if="idx < stepStates.length - 1" class="wf-arrow">
            <AppIcon name="caret-right" />
          </span>
        </div>
      </template>
    </div>
  </div>

  <!-- 0403 NR0004 F4: the dialog opened by pouring the plan saves to the sequence owner the
       candidate response told it to. In a group with no workflow yet, the parent document the
       screen is holding may be empty, and if it created the sequence on the work plan itself
       instead, that group would be permanently broken. -->
  <WorkflowDecisionModal
    mode="edit"
    :visible="!readOnly && showEditModal"
    :doc-id="pouredPayload?.workflowDocId ?? parentRDocId ?? tab.id"
    :poured="pouredPayload"
    @update:visible="onEditModalVisible"
    @saved="onSequenceSaved"
  />
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { postRequest } from '@shared/api'
import type { Tab } from '../stores/tabs'
import type { SlotCommitMark } from '../workflow/timeMachineSlot'
import type { StepState } from '../workflow/workflowViewState'
import { useDocTypeStore } from '../stores/docTypeStore'
import WorkflowDecisionModal, { type PourPayload, type PourRow } from './WorkflowDecisionModal.vue'
import AppIcon from '@shared/AppIcon.vue'

const props = withDefaults(defineProps<{
  tab: Tab
  workflowDecided?: boolean
  parentRDocId?: string | null
  stepStates: StepState[]
  /** workflowViewState output: whether "proceed to next step" action is available (enables click). */
  canNextAction?: boolean
  /** 0142 R0001 — reverse time-machine: strip indices that are "return targets". These are the
   *  rewound steps sitting AHEAD of the current head; hovering makes them clickable to roll the
   *  workflow forward (restore) to that step. Empty/absent when no active return point. */
  returnTargets?: number[]
  /** 0332 D0005 §6.1 — 칸마다의 소스 커밋 표식. stepStates 와 같은 순서이고, 표식이 없는
   *  칸은 null 이다. 배열 자체가 비어 있거나 없으면(시퀀스 조회 실패 포함) 표식을 하나도
   *  그리지 않는다 — 그때 칸은 이 기능이 있기 전과 똑같이 보인다. */
  slotCommits?: (SlotCommitMark | null)[]
  /** Disable every document/workflow mutation affordance while keeping the sequence visible. */
  readOnly?: boolean
}>(), {
  readOnly: false,
})

const { t } = useI18n()
const docTypeStore = useDocTypeStore()
const isWorkflowRoot = computed(() => props.tab.typeCode === 'R' || props.tab.typeCode === 'B')

// 0395 D0007 §7: the work plan is "요건정의 다음에 오는 일반 칸" — a sequence step like any
// other, so it shows up in the strip and must be clickable there.
const WORK_PLAN_TYPE = 'WP'

// 0119 B0001 (NR0003 §6-B): decided workflow root whose steps were all deleted. Used to
// keep the section + [Edit] button visible (recovery) instead of collapsing to nothing.
const decidedEmpty = computed(() =>
  isWorkflowRoot.value && props.workflowDecided === true && props.stepStates.length === 0,
)

const emit = defineEmits<{
  'sequence-updated': []
  'decide-workflow': []
  'next-action': []
  // 0395 T0021 — open the work-plan create dialog. Carries the sequence-owning root
  // (R/B) because the strip is also drawn on member documents, and a work plan may
  // only attach to the root; sending the viewed document would 422.
  'create-work-plan': [payload: { docId: string }]
  // 0018 R0001 — workflow-strip time-machine: a completed ('done') step cell was clicked.
  // Emits the strip index + step type code so the parent can resolve the slot's realised
  // document (by slot identity) and reopen the workflow there.
  'time-machine': [payload: { index: number; code: string }]
  // 0142 R0001 — reverse time-machine: a return-target cell (a rewound step ahead of the head)
  // was clicked. Emits the same index+code so the parent restores the workflow forward to that
  // step and navigates there. Mirror of 'time-machine' in the opposite direction.
  'return-to': [payload: { index: number; code: string }]
}>()

// 0142 R0001 — a strip cell is a return target when the parent lists its index in returnTargets.
function isReturnTarget(idx: number): boolean {
  return props.returnTargets?.includes(idx) ?? false
}

// 0332 D0005 §6.1 — 그 칸의 소스 커밋. 없으면 null 이고, 그때 표식도 호버 줄도 없다.
function commitOf(idx: number): SlotCommitMark | null {
  return props.slotCommits?.[idx] ?? null
}

// Hover tooltip: a rewound step ahead of the head hints "return here"; a completed step behind
// the head hints "roll back here". (A cell is only ever one of the two.)
// 0332 D0005 §6.1: 커밋이 달린 칸은 여기에 한 줄이 더 붙는다 — 짧은 해시와 상태. 읽기
// 전용이라 되돌리기 힌트가 없는 칸에서도 이 줄은 보인다(사실을 말할 뿐 동작이 아니다).
function stepHint(s: StepState, idx: number): string | undefined {
  const lines: string[] = []
  if (!props.readOnly) {
    if (isReturnTarget(idx)) lines.push(t('main.doc_workflow.time_machine_return_hint'))
    else if (s.visual === 'done') lines.push(t('main.doc_workflow.time_machine_hint'))
  }
  const commit = commitOf(idx)
  if (commit) {
    // 0332 T0018 K11 — three states, not two. A restored commit is live and must show a
    // marker (it IS in the tree), but saying only "소스 커밋 abc1234" hides the round
    // trip the person just made and makes the log look like it never happened.
    const key = commit.state === 'canceled'
      ? 'main.doc_workflow.tr_commit_canceled_hint'
      : commit.restored
        ? 'main.doc_workflow.tr_commit_restored_hint'
        : 'main.doc_workflow.tr_commit_hint'
    const sha = commit.state === 'canceled' ? commit.cancel_commit : commit.commit
    lines.push(t(key, { commit: sha ?? '' }))
  }
  return lines.length > 0 ? lines.join('\n') : undefined
}

// 0018 R0001 / 0142 R0001 — head step keeps its "proceed to next step" action; a completed
// (done) step behind the head opens the time-machine (roll back); a return-target step ahead of
// the head restores forward (reverse time-machine); other future steps are inert.
function onStepClick(s: StepState, idx: number) {
  if (props.readOnly) return
  if ((s.visual === 'highlight' || s.visual === 'current') && props.canNextAction) {
    // 0395 T0021 / D0007 §3.1 decision 3: a work plan is NOT created through the generic
    // related-document path the next-step action uses — that path builds a Markdown
    // body and touches the parent's status. It has its own dialog and route.
    if (s.code === WORK_PLAN_TYPE) {
      emitCreateWorkPlan()
      return
    }
    emit('next-action')
    return
  }
  if (isReturnTarget(idx)) {
    emit('return-to', { index: idx, code: s.code })
    return
  }
  if (s.visual === 'done') {
    emit('time-machine', { index: idx, code: s.code })
  }
}

function emitCreateWorkPlan() {
  emit('create-work-plan', { docId: props.parentRDocId ?? props.tab.id })
}

const showEditModal = ref(false)

// ── 0399 [작업계획 적용] (D0010 §3.1~§3.3 / L0011 §4.1·§4.3) ────────────────────
//
// The two candidate sets are fetched when the section appears, not when the menu opens.
// L0011 §4.1 requires the menu to say how many rows each mode adds and removes BEFORE it
// is pressed, and D0010 §3.1 requires a blocked button to explain itself in place — both
// need the server's answer already in hand. The calls read and write nothing (P0013 ①).

interface RowCountChange {
  before: number
  after: number
  deleted: number
  added: number
}

interface CandidateResponse {
  wp_doc_id: string
  // 0403 NR0004 F2 — the plan revision echoed back unchanged on save. The server needs
  // it to judge whether the plan changed since this dialog was opened.
  wp_revision_no: number
  workflow_doc_id: string | null
  mode: 'append' | 'replace_after'
  plan_step_count: number
  rows: PourRow[]
  row_count_change: RowCountChange
  notifications: Array<{ code: string; severity: string; count: number; [k: string]: unknown }>
  workflow_tag: string
}

const POUR_MODES = ['append', 'replace_after'] as const
type PourMode = (typeof POUR_MODES)[number]

const isWorkPlan = computed(() => props.tab.typeCode === WORK_PLAN_TYPE)
const candidates = ref<Partial<Record<PourMode, CandidateResponse>>>({})
const applyMenuOpen = ref(false)
const pouredPayload = ref<PourPayload | null>(null)
const applyLoadFailed = ref(false)
const applyUnreadable = ref(false)
const applyLoaded = ref(false)

watch(() => props.readOnly, (readOnly) => {
  if (!readOnly) return
  applyMenuOpen.value = false
  showEditModal.value = false
  pouredPayload.value = null
})

// M0020 — one flight at a time, and a generation number so a slow answer from an earlier
// flight can never overwrite a newer one. Both of those were missing, which is why the
// state under the button changed several times a second while the page settled.
let applyFlight: Promise<void> | null = null
let applyGeneration = 0

// Either mode's answer carries the same plan, so whichever one came back is the one that
// can say how many steps it has — [이후 단계 교체] alone still knows.
const planStepCount = computed(() =>
  candidates.value.append?.plan_step_count
  ?? candidates.value.replace_after?.plan_step_count
  ?? 0,
)

// "flowgate.default.0399.0004-WP" → "WP0004": the short code the group view and the mockup
// both use, so the menu names the plan the way the rest of the screen already does.
const wpShortCode = computed(() => {
  const tail = props.tab.id.split('.').pop() ?? props.tab.id
  const [seq, code] = tail.split('-')
  return code ? `${code}${seq}` : tail
})

// M0020 — what the OPEN MENU shows. Never what the button shows: the button is always the
// same button. Approval is not consulted at all any more ("승인체크같은거 안해도
// 되니까") — a plan that is still in review pours exactly like an approved one, and the
// save at the end of the dialog is where a person decides whether that was a good idea.
// plan_unreadable and plan_has_no_step stay apart because what the person does next is
// different: one is a broken file, the other is an empty plan.
type ApplyState = 'ready' | 'loading' | 'unreadable' | 'error' | 'no_step'
const applyState = computed<ApplyState>(() => {
  if (applyOptions.value.length > 0) return planStepCount.value < 1 ? 'no_step' : 'ready'
  if (!applyLoaded.value) return 'loading'
  if (applyUnreadable.value) return 'unreadable'
  if (applyLoadFailed.value) return 'error'
  return 'no_step'
})

const applyOptions = computed(() =>
  POUR_MODES
    .map(mode => ({
      // Mockup fgh29xnk v3 · screen 1 used ph-arrow-elbow-down-right. iconData.ts is a
      // generated file marked "do not hand-edit" and lacks that name, so arrow-down is
      // used as the closest match in meaning.
      mode,
      icon: mode === 'append' ? 'arrow-down' : 'arrows-clockwise',
      change: candidates.value[mode]?.row_count_change,
    }))
    .filter((opt): opt is { mode: PourMode; icon: string; change: RowCountChange } => !!opt.change),
)

async function fetchCandidates(force = false): Promise<void> {
  if (!isWorkPlan.value) return
  // Already asking. Joining the flight in progress is what keeps a burst of re-renders
  // from turning into a burst of requests whose answers race each other.
  if (applyFlight && !force) return applyFlight
  const generation = ++applyGeneration
  const flight = (async () => {
    let unreadable = false
    let failed = false
    const results = await Promise.all(POUR_MODES.map(async (mode) => {
      try {
        const res = await postRequest<CandidateResponse>(
          `/api/v1/documents/${encodeURIComponent(props.tab.id)}/work-plan/sequence-candidates`,
          { mode },
        )
        return [mode, res.data] as const
      } catch (e: any) {
        // 409 wp_unreadable is the plan-file reader's own refusal (L0011 §4.1-2).
        if (e?.response?.status === 409) unreadable = true
        else failed = true
        return [mode, null] as const
      }
    }))
    // A late answer from a superseded flight is dropped whole rather than half-applied.
    if (generation !== applyGeneration) return
    const next: Partial<Record<PourMode, CandidateResponse>> = {}
    for (const [mode, data] of results) if (data) next[mode] = data
    // One assignment, one render. The previous version cleared this at the start of every
    // fetch, so every refresh emptied the menu before refilling it.
    candidates.value = next
    // A mode that answered is a mode that works; only a total loss is a failure worth
    // naming. [이후 단계 교체] refusing on its own must not hide [뒤에 이어 붙이기].
    const anyData = Object.keys(next).length > 0
    applyUnreadable.value = !anyData && unreadable
    applyLoadFailed.value = !anyData && failed && !unreadable
    applyLoaded.value = true
  })()
  applyFlight = flight
  try {
    await flight
  } finally {
    if (applyFlight === flight) applyFlight = null
  }
}

function toggleApplyMenu() {
  applyMenuOpen.value = !applyMenuOpen.value
  // 0406 T0017 "저장도 안되고 멘트도 이상하고" — right below this cell sits the plan
  // editor for the same document, and editing and saving the plan bumps that document's
  // revision. But previously this poured the candidate fetched once at mount time,
  // unchanged. So (1) the mention poured was the old revision's, not the one just edited,
  // (2) the save was rejected with wp_changed, and (3) closing and reopening the dialog as
  // instructed left this cache untouched, so the same failure repeated endlessly — there
  // was no way out short of closing the document tab. Opening the menu is a human action,
  // so it re-reads at that moment. Until the answer arrives the current value is kept
  // as-is, so neither the button nor the open menu goes blank and then refills (M0020).
  if (applyMenuOpen.value) void fetchCandidates(true)
}

function closeApplyMenu() {
  applyMenuOpen.value = false
}

// D0010 §3.3 — the promise this module is built on: choosing a mode opens the edit dialog in
// that state and nothing else happens. The sequence changes when [저장] is pressed, there.
async function choosePourMode(mode: PourMode) {
  // 0406 T0017 — the re-read that started when the menu opened may not be done yet.
  // Wait for that answer before pouring: the wp_revision_no it carries is what the save
  // check is based on, and the mention on the row belongs to that same revision — waiting
  // one beat here is the difference between that and pouring a stale plan that then gets
  // rejected on save.
  if (applyFlight) await applyFlight
  const data = candidates.value[mode]
  // If the re-read finds this branch gone (the plan is empty or unreadable), leave the
  // menu open — the reason is already written right there, and closing it silently would
  // leave the person who clicked with no explanation at all.
  if (!data) return
  applyMenuOpen.value = false
  pouredPayload.value = {
    wpDocId: data.wp_doc_id,
    wpRevisionNo: data.wp_revision_no,
    wpShortCode: wpShortCode.value,
    workflowDocId: data.workflow_doc_id,
    mode: data.mode,
    planStepCount: data.plan_step_count,
    rows: data.rows,
    rowCountChange: data.row_count_change,
    notifications: data.notifications,
    workflowTag: data.workflow_tag,
  }
  showEditModal.value = true
}

function onEditModalVisible(value: boolean) {
  showEditModal.value = value
  // Closing without saving leaves the sequence untouched (D0010 §3.3), so the poured state
  // is dropped here rather than kept for the next time the dialog opens.
  if (!value) pouredPayload.value = null
}

// M0020 — a save is the one event that really does change the row counts the menu quotes,
// so it is the one event that refetches them. Nothing else does.
function onSequenceSaved() {
  pouredPayload.value = null
  void fetchCandidates(true)
  emit('sequence-updated')
}

function onDocumentClick() {
  if (applyMenuOpen.value) applyMenuOpen.value = false
}

onMounted(() => {
  document.addEventListener('click', onDocumentClick)
  void fetchCandidates()
})
onBeforeUnmount(() => document.removeEventListener('click', onDocumentClick))

// M0020 — only the document. stepStates.length used to be in here too, and it changes
// several times while a page loads, so the menu refetched several times while a person was
// looking at it. A save refreshes through onSequenceSaved() instead.
watch(() => props.tab.id, () => {
  closeApplyMenu()
  candidates.value = {}
  applyLoaded.value = false
  applyLoadFailed.value = false
  applyUnreadable.value = false
  void fetchCandidates(true)
})

// ── Sequence accordion (R0001 group 0244) — persisted for the same reason as the
// document header: the tablet constraint does not go away on reload.
const SEQ_COLLAPSED_KEY = 'flowgate:doc-workflow:collapsed'

function readSequenceCollapsed(): boolean {
  try {
    return localStorage.getItem(SEQ_COLLAPSED_KEY) === '1'
  } catch {
    return false
  }
}

const sequenceCollapsed = ref(readSequenceCollapsed())
watch(sequenceCollapsed, (val) => {
  try {
    localStorage.setItem(SEQ_COLLAPSED_KEY, val ? '1' : '0')
  } catch { /* ignore — e.g. private mode quota */ }
})

function toggleSequenceCollapsed() {
  sequenceCollapsed.value = !sequenceCollapsed.value
}
</script>

<style scoped>
/* 0332 D0005 §6.1 — 커밋 표식. 칸의 크기와 배치는 그대로 두고 오른쪽 위 모서리에만
   얹으므로 칸을 감싸는 relative 만 더한다. 표식은 클릭 대상이 아니다 — pointer-events 를
   꺼서 표식 위를 눌러도 칸 클릭(진행 / 되돌리기 / 복귀)이 그대로 간다. */
.wf-step {
  position: relative;
}
.wf-commit-mark {
  position: absolute;
  top: 2px;
  right: 3px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: .62rem;
  line-height: 1;
  pointer-events: none;
}
.wf-commit-mark.is-live i {
  color: var(--success, #16a34a);
}
.wf-commit-mark.is-canceled i {
  color: var(--text-m, #64748b);
}

/* 0119 B0001: decided-but-empty recovery hint */
.wf-empty-recover {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  font-size: .78rem;
  color: #92400e;
  background: var(--warning-l, #fffbeb);
  border: 1px dashed #fde68a;
  border-radius: var(--r, 8px);
}
.wf-empty-recover i {
  color: var(--warning, #d97706);
  flex-shrink: 0;
}

.wf-step.wf-undecided {
  border: 2px dashed var(--border-d, #94a3b8);
  opacity: 1;
  color: var(--text-m);
  background: none;
}
.wf-step.wf-undecided i {
  color: var(--text-m);
}

/* Current undecided step (requirements definition); blue emphasis. The "undecided" placeholder
   (wf-undecided without .current) stays gray */
.wf-step.wf-undecided.current {
  border-color: var(--primary);
  background: var(--primary-l);
  color: var(--primary);
  box-shadow: 0 0 0 2px rgba(37, 99, 235, .15);
}
.wf-step.wf-undecided.current i {
  color: var(--primary);
}
.wf-step.wf-undecided.current .s-lbl {
  font-weight: 700;
}

.wf-step.wf-current-clickable {
  cursor: pointer;
}
.wf-step.wf-current-clickable:hover {
  box-shadow: 0 0 0 3px rgba(37, 99, 235, .3);
}

/* 0018 R0001 — completed step is clickable to time-travel (roll the workflow back to it).
   Warning-tinted focus ring on hover signals the destructive (cascade) rollback intent. */
.wf-step.wf-done-clickable {
  cursor: pointer;
}
.wf-step.wf-done-clickable:hover {
  box-shadow: 0 0 0 3px rgba(217, 119, 6, .3);
}

/* 0142 R0001 — reverse time-machine: a rewound step AHEAD of the head is clickable to return
   forward (restore) to it. It renders as a normal (grey/future) cell until hovered; the green
   focus ring + forward cursor signal the non-destructive "go back to where I was" intent,
   deliberately mirroring the amber backward ring above so the two directions read as one strip. */
.wf-step.wf-return-clickable {
  cursor: pointer;
  border-style: dashed;
  border-color: var(--success, #16a34a);
  color: var(--success, #16a34a);
  opacity: 1;
}
.wf-step.wf-return-clickable i {
  color: var(--success, #16a34a);
}
.wf-step.wf-return-clickable:hover {
  background: var(--success-l, #f0fdf4);
  box-shadow: 0 0 0 3px rgba(22, 163, 74, .3);
}

/* ── 0399 [작업계획 적용] button and the two-branch list (mockup fgh29xnk v3 · screen 1) ──
   The section needs a positioning context because the menu is absolutely placed against
   this button, and .wf-section is the nearest box that never scrolls under the strip. */
.wf-section {
  position: relative;
}
.wf-apply-wrap {
  order: 3;        /* after ::after (1) and [시퀀스 수정] (2), before the collapse caret */
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-left: 6px;
}
.wf-apply-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 10px;
  font-size: .72rem;
  font-weight: 700;
  color: #166534;
  background: #dcfce7;
  border: 1px solid #86efac;
  border-radius: 6px;
  cursor: pointer;
  transition: background .15s, border-color .15s, color .15s;
}
.wf-apply-btn:hover:not(:disabled) {
  background: #bbf7d0;
  border-color: #4ade80;
}
.wf-apply-btn.is-open {
  background: #16a34a;
  color: #fff;
  border-color: #16a34a;
}
/* M0020 — removed the blocked-state and reason text from beside the button. The reason is now stated inside the open menu. */
.wf-apply-msg {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 13px;
  font-size: .72rem;
  color: var(--text-s);
  border-bottom: 1px solid var(--border);
}
.wf-apply-msg.is-warn {
  background: #fffbeb;
  color: #92400e;
}
.wf-apply-msg-text {
  flex: 1;
  min-width: 0;
}
.wf-apply-retry {
  flex-shrink: 0;
  padding: 2px 9px;
  font-size: .68rem;
  font-weight: 700;
  color: var(--text-m);
  background: var(--surface);
  border: 1px solid var(--border-d);
  border-radius: var(--r-sm, 6px);
  cursor: pointer;
}
.wf-apply-retry:hover {
  background: var(--surface-h);
}
.wf-apply-menu {
  position: absolute;
  top: 26px;
  right: 0;
  z-index: 40;
  width: 430px;
  max-width: 86vw;
  text-align: left;
  background: var(--surface);
  border: 1px solid var(--border-d);
  border-radius: var(--r-lg, 12px);
  box-shadow: var(--sh-lg, 0 12px 28px rgba(15, 23, 42, .18));
  overflow: hidden;
}
.wf-apply-hd {
  padding: 8px 12px;
  font-size: .68rem;
  font-weight: 700;
  letter-spacing: .04em;
  color: var(--text-m);
  background: var(--surface-h);
  border-bottom: 1px solid var(--border);
}
.wf-apply-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  width: 100%;
  padding: 11px 13px;
  text-align: left;
  background: none;
  border: none;
  border-bottom: 1px solid var(--border);
  cursor: pointer;
}
.wf-apply-item:hover {
  background: #f0fdf4;
}
.wf-apply-item > i {
  font-size: 1rem;
  color: #16a34a;
  margin-top: 2px;
}
.wf-apply-body {
  flex: 1;
  min-width: 0;
}
.wf-apply-name {
  display: block;
  font-size: .8rem;
  font-weight: 700;
  color: var(--text);
}
.wf-apply-desc {
  display: block;
  margin-top: 2px;
  font-size: .7rem;
  color: var(--text-s);
  line-height: 1.45;
}
.wf-apply-delta {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-top: 6px;
  padding: 2px 8px;
  border-radius: 4px;
  background: var(--bg);
  border: 1px solid var(--border);
  font-size: .67rem;
  color: var(--text-s);
}
.wf-apply-delta .plus {
  color: #16a34a;
  font-weight: 600;
}
.wf-apply-delta .minus {
  color: #dc2626;
  font-weight: 600;
}
.wf-apply-foot {
  display: flex;
  gap: 7px;
  padding: 9px 13px;
  background: #fffbeb;
  color: #92400e;
  font-size: .69rem;
  line-height: 1.5;
}
.wf-apply-foot i {
  margin-top: 2px;
}

.wf-edit-btn {
  order: 2;        /* render after ::after decorative line (order: 1) */
  margin-left: 0;  /* ::after flex:1 fills the gap; no auto-margin needed */
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 10px;
  font-size: .72rem;
  font-weight: 600;
  color: #0284c7;
  background: #e0f2fe;
  border: 1px solid #bae6fd;
  border-radius: 6px;
  cursor: pointer;
  transition: background .15s, border-color .15s;
}
.wf-edit-btn:hover {
  background: #bae6fd;
  border-color: #7dd3fc;
}

.sec-title {
  display: flex;
  align-items: center;
}
/* global .sec-title::after has flex:1 which consumed all free space before
   margin-left:auto could take effect, pushing the button against the title text.
   Reorder so the decorative line (::after) sits between the title and the button. */
.sec-title::after {
  order: 1;
}

/* ── Sequence accordion (R0001 group 0244 / NR0003 §8) — .wf-flow wraps and has
   no height cap, so a long sequence is the worst vertical offender on tablet.
   order: 3 keeps this at the far right, past .wf-edit-btn (order: 2). It is a
   sibling of that button, not a wrapper: nesting would both be invalid HTML and
   make [시퀀스 수정] fold the section. ── */
.wf-collapse-btn {
  order: 3;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--text-s);
  background: var(--surface);
  cursor: pointer;
  transition: background .15s, border-color .15s, color .15s;
}
.wf-collapse-btn:hover {
  color: var(--primary);
  border-color: var(--primary);
  background: var(--primary-l);
}
.wf-collapse-btn:focus-visible {
  outline: 2px solid var(--info);
  outline-offset: 1px;
}
.wf-caret {
  font-size: .7rem;
  transition: transform .18s ease;
}
.wf-section.collapsed .wf-caret {
  transform: rotate(-90deg);
}
.wf-section.collapsed .wf-flow {
  display: none;
}
.wf-section.collapsed .sec-title {
  margin-bottom: 0;
}
@media (prefers-reduced-motion: reduce) {
  .wf-caret {
    transition-duration: .1s;
  }
}
</style>
