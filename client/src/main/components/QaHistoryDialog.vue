<template>
  <teleport to="body">
    <div
      v-if="visible"
      class="modal-bg"
      tabindex="-1"
      @keydown.escape.prevent="onClose"
      @click.self="onClose"
    >
      <div class="modal-box modal-qhd" role="dialog" aria-modal="true" aria-labelledby="qhd-title">
        <!-- Header -->
        <div class="modal-hd">
          <div class="modal-title" id="qhd-title">
            <AppIcon name="question" style="color:var(--primary, #2563eb); margin-right:6px;" />{{ t('main.qa_history.title') }}
          </div>
          <button type="button" class="modal-close" @click="onClose">
            <AppIcon name="x" />
          </button>
        </div>

        <!-- Body -->
        <div class="modal-bd qhd-body">
          <p class="qhd-desc">{{ t('main.qa_history.desc') }}</p>

          <div v-if="items.length === 0" class="qhd-empty">{{ t('main.qa_history.empty') }}</div>

          <ul v-else class="qhd-list">
            <li
              v-for="item in items"
              :key="item.id"
              class="qhd-entry"
              :class="{ answered: answered(item) }"
              :ref="(el) => setEntryRef(item.id, el)"
            >
              <div class="qhd-head">
                <span class="qhd-badge" :class="answered(item) ? 'done' : 'pending'">
                  {{ answered(item) ? t('main.doc_info_panel.qa_answered') : t('main.doc_info_panel.qa_answering') }}
                </span>
                <span class="qhd-seq">Q{{ item.seq }}</span>
                <span v-if="item.title" class="qhd-title-text">{{ item.title }}</span>
                <span class="qhd-asker">
                  <AppIcon :name="item.asker_kind === 'ai' ? 'robot' : 'user'" />
                  {{ item.asker_kind === 'ai' ? t('main.doc_info_panel.qa_by_ai') : t('main.doc_info_panel.qa_by_human') }}
                </span>
              </div>
              <!-- Full content, no inner scroll: the dialog body is the single scroll
                   surface (mirrors ReviewHistoryDialog's full-content view). -->
              <div class="qhd-blabel">{{ t('main.doc_info_panel.qa_question') }}</div>
              <p class="qhd-box">{{ item.body }}</p>
              <!-- group 0243 R0001: an answered query shows its options read-only, marking
                   which one the answer picked (a free-form answer marks none). -->
              <template v-if="(item.options?.length ?? 0) > 0 && answered(item)">
                <div class="qhd-blabel">{{ t('main.doc_info_panel.qa_options') }}</div>
                <ul class="qhd-opts">
                  <li
                    v-for="opt in item.options"
                    :key="opt.id"
                    class="qhd-opt-read"
                    :class="{ picked: pickedIds(item).includes(opt.id) }"
                  >
                    <AppIcon v-if="pickedIds(item).includes(opt.id)" name="check-circle" />
                    {{ opt.label }}
                  </li>
                </ul>
              </template>
              <template v-if="(item.answers?.length ?? 0) > 0">
                <div class="qhd-blabel">{{ t('main.doc_info_panel.qa_answer') }}</div>
                <p v-for="(a, ai) in item.answers" :key="ai" class="qhd-box qhd-answer">
                  <AppIcon :name="a.author_kind === 'ai' ? 'robot' : 'user'" class="qhd-answer-icon" />
                  <span>{{ a.body }}</span>
                </p>
              </template>

              <!-- R0001 (group 0093): answer directly from the full view. The actions
                   call the parent's bound write functions (shared useQaAnswers state),
                   so a submitted answer refetches once and updates both this dialog and
                   the side panel in sync. Only shown when the dialog is given a docId. -->
              <template v-if="docId">
                <div v-if="answerOpenId === item.id" class="qhd-answer-form">
                  <!-- group 0243 R0001: options are reference only — nothing is preselected
                       and none is marked recommended (0022 rule, kept). Clicking a picked
                       option unpicks it, so a user who changes their mind can still answer
                       freely. The free-text box shows regardless of whether options exist. -->
                  <template v-if="(item.options?.length ?? 0) > 0">
                    <div class="qhd-opts">
                      <button
                        v-for="opt in item.options"
                        :key="opt.id"
                        type="button"
                        class="qhd-opt-btn"
                        :class="{ picked: picked === opt.id }"
                        :aria-pressed="picked === opt.id"
                        @click="togglePick(opt.id)"
                      >{{ opt.label }}</button>
                    </div>
                  </template>
                  <textarea
                    v-model="answerBody"
                    class="qhd-answer-textarea"
                    rows="3"
                    :placeholder="t('main.doc_info_panel.qa_answer_ph')"
                  ></textarea>
                  <div class="qhd-answer-actions">
                    <button type="button" class="btn btn-outline btn-sm" @click="closeAnswer">{{ t('common.cancel') }}</button>
                    <button
                      type="button"
                      class="btn btn-primary btn-sm"
                      :disabled="!canSubmit || busy"
                      @click="onSubmitAnswer(item.id)"
                    >{{ t('main.doc_info_panel.qa_answer_submit') }}</button>
                  </div>
                </div>
                <div v-else class="qhd-answer-actions">
                  <button type="button" class="btn btn-outline btn-sm qhd-act-write" @click="openAnswer(item.id)">
                    {{ t('main.doc_info_panel.qa_answer_write') }}
                  </button>
                </div>
                <!-- 0248 B0001 rework: the AI hand-off sits OUTSIDE the compose branch.
                     It used to live in the v-else above, so opening the compose box hid it —
                     and the panel card's [답변] opens this dialog with the box already open.
                     A user who registered a query therefore landed on a bare textarea with no
                     way to hand it to an AI, and could only answer their own question. Writing
                     an answer and asking an AI are not alternatives; both stay reachable.
                     Mirrors the legacy Q flow's ment_copy / ai pair (AnswerEditor.vue). -->
                <div class="qhd-answer-actions qhd-handoff">
                  <span class="qhd-handoff-label">{{ t('main.doc_info_panel.qa_answer_handoff') }}</span>
                  <button
                    type="button"
                    class="btn btn-outline btn-sm qhd-act-mention"
                    :disabled="busy"
                    @click="onCopyMention(item.id)"
                  >
                    <AppIcon name="copy" />
                    {{ t('main.doc_info_panel.qa_answer_mention_copy') }}
                  </button>
                  <button
                    type="button"
                    class="btn btn-outline btn-sm qhd-act-ai"
                    :disabled="busy || aiRunItemId !== null"
                    @click="onRequestAi(item.id)"
                  >
                    <AppIcon name="robot" />
                    {{ aiRunItemId === item.id
                      ? t('main.doc_info_panel.qa_answer_ai_running')
                      : t('main.doc_info_panel.qa_answer_ai') }}
                  </button>
                </div>
              </template>
            </li>
          </ul>
        </div>

        <!-- Footer -->
        <div class="modal-ft qhd-footer">
          <button type="button" class="btn btn-outline btn-sm" @click="onClose">{{ t('common.close') }}</button>
        </div>
      </div>
    </div>
  </teleport>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { QaItem } from '../composables/useQaAnswers'
import AppIcon from '@shared/AppIcon.vue'

const props = withDefaults(defineProps<{
  visible: boolean
  items: QaItem[]
  // R0001 (group 0093): when a docId and write actions are supplied, the full view
  // becomes answer-capable. Omitting them keeps the dialog read-only (back-compat).
  docId?: string
  busy?: boolean
  // group 0126 / C안: the panel cards open this dialog focused on one query — [Open]
  // scrolls it into view, [Answer] also opens its inline answer form.
  focusId?: number | null
  startAnswer?: boolean
  submitAnswer?: (itemId: number, body: string, selectedOptionIds?: string[]) => Promise<boolean>
  requestAiAnswer?: (itemId: number) => Promise<boolean>
  // [멘트 복사]. The parent owns the clipboard write + toast (it is the same copy the panel
  // does); this must be called straight from the click with nothing awaited in front of it,
  // or the clipboard loses the gesture's transient activation (group 0133 NR0003).
  copyAnswerMention?: (itemId: number) => Promise<boolean>
  // 0248 B0001: the AI answer run is async, so the item it is answering stays marked
  // until the run finishes. null = no run in flight.
  aiRunItemId?: number | null
}>(), {
  docId: '',
  busy: false,
  focusId: null,
  startAnswer: false,
  submitAnswer: undefined,
  requestAiAnswer: undefined,
  copyAnswerMention: undefined,
  aiRunItemId: null,
})

const emit = defineEmits<{ 'update:visible': [value: boolean] }>()

const { t } = useI18n()

const answerOpenId = ref<number | null>(null)
const answerBody = ref('')
// v1 is single-select (L0008 §1 MAX_SELECTED), so one id — not a set — is enough.
const picked = ref<string | null>(null)

// Picking alone is a complete answer: the server fills the body with the label.
const canSubmit = computed(() => !!answerBody.value.trim() || picked.value !== null)

// group 0126 / C안: per-entry element refs so an opened-with-focus query can be
// scrolled into view (the panel cards open this dialog targeting one query).
const entryRefs = ref<Record<number, HTMLElement>>({})
function setEntryRef(id: number, el: Element | null | { $el?: Element }) {
  const node = (el && '$el' in el ? el.$el : el) as HTMLElement | null
  if (node) entryRefs.value[id] = node
  else delete entryRefs.value[id]
}

function answered(item: QaItem): boolean {
  return (item.answer_count ?? item.answers?.length ?? 0) > 0
}

// Option ids the answers on this item picked — used to mark them in the read-only view.
function pickedIds(item: QaItem): string[] {
  return (item.answers ?? []).flatMap((a) => a.selected_options ?? [])
}

function togglePick(id: string) {
  picked.value = picked.value === id ? null : id
}

function openAnswer(id: number) {
  answerOpenId.value = id
  answerBody.value = ''
  picked.value = null
}
function closeAnswer() {
  answerOpenId.value = null
  answerBody.value = ''
  picked.value = null
}

async function onSubmitAnswer(itemId: number) {
  if (!props.submitAnswer || !canSubmit.value || props.busy) return
  if (await props.submitAnswer(itemId, answerBody.value, picked.value ? [picked.value] : [])) {
    closeAnswer()
  }
}

async function onRequestAi(itemId: number) {
  if (!props.requestAiAnswer || props.busy) return
  await props.requestAiAnswer(itemId)
}

// No await before the parent's call — see the copyAnswerMention prop note.
async function onCopyMention(itemId: number) {
  if (!props.copyAnswerMention || props.busy) return
  await props.copyAnswerMention(itemId)
}

// Reset any open answer form when the dialog is closed so it reopens clean. When opened
// with a focus target (group 0126 / C안), scroll it into view and — for [Answer] — open
// its inline answer form so the user lands directly on the compose box.
watch(() => props.visible, async (v) => {
  if (!v) { closeAnswer(); return }
  const target = props.focusId
  if (target == null) return
  if (props.startAnswer && props.docId && !answered(props.items.find((q) => q.id === target) ?? ({} as QaItem))) {
    openAnswer(target)
  }
  await nextTick()
  const node = entryRefs.value[target]
  if (node && typeof node.scrollIntoView === 'function') node.scrollIntoView({ block: 'nearest' })
})

function onClose() {
  emit('update:visible', false)
}
</script>

<style scoped>
.modal-qhd { width: 560px; max-width: 92vw; max-height: 80vh; display: flex; flex-direction: column; }
.qhd-body { overflow-y: auto; }
.qhd-desc { font-size: .78rem; color: var(--text-m); margin-bottom: 12px; }
.qhd-empty { padding: 24px; text-align: center; color: var(--text-m); font-size: .85rem; }
.qhd-list { display: flex; flex-direction: column; gap: 12px; }
/* group 0126 / C안: full-view entries use the prototype's amber query card; an answered
   query switches its accent to green. */
.qhd-entry {
  border: 1px solid var(--border); border-left: 3px solid #f59e0b;
  border-radius: 8px; padding: 10px 12px; background: #fffdfa;
}
.qhd-entry.answered { border-left-color: #22c55e; background: #f7fdf9; }
.qhd-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
.qhd-badge { display: inline-flex; align-items: center; font-size: .62rem; font-weight: 700; padding: 1px 8px; border-radius: 999px; }
.qhd-badge.done { background: #dcfce7; color: #15803d; border: 1px solid #bbf7d0; }
.qhd-badge.pending { background: #fef3c7; color: #b45309; border: 1px solid #fde68a; }
.qhd-seq { font-size: .72rem; font-weight: 700; color: var(--text); }
.qhd-title-text { font-size: .76rem; color: var(--text-s); }
.qhd-asker { margin-left: auto; font-size: .62rem; color: var(--text-m); display: inline-flex; align-items: center; gap: 4px; }
.qhd-blabel { font-size: .62rem; font-weight: 700; color: #6b7280; margin: 8px 0 3px; }
/* Full text takes its natural height — the dialog body (.qhd-body, max-height 80vh)
   is the single scroll surface, so a long question/answer scrolls the dialog rather
   than being clipped (mirrors ReviewHistoryDialog's .rhd-comment). */
.qhd-box {
  font-size: .8rem; color: var(--text); white-space: pre-wrap; line-height: 1.55;
  margin: 0; overflow-wrap: anywhere;
  background: #f8fafc; border: 1px solid var(--border); border-radius: 5px; padding: 6px 8px;
}
.qhd-answer { display: flex; gap: 6px; align-items: flex-start; border-left: 3px solid #22c55e; margin-top: 4px; }
.qhd-answer-icon { color: #15803d; margin-top: 2px; }
.qhd-footer { display: flex; justify-content: flex-end; }

/* R0001 (group 0093): inline answer form within the full view (mirrors the
   DocInfoPanel .dip-qa-form idiom). */
.qhd-answer-form { display: flex; flex-direction: column; gap: 6px; margin-top: 8px; }
.qhd-answer-textarea {
  width: 100%; box-sizing: border-box; font-size: .78rem; font-family: inherit;
  padding: 5px 7px; border: 1px solid var(--border); border-radius: 4px; resize: vertical;
}
.qhd-answer-actions { display: flex; justify-content: flex-end; gap: 6px; margin-top: 8px; }
/* The hand-off row reads as a separate offer from [답변 작성], not another submit button. */
.qhd-handoff { align-items: center; border-top: 1px dashed var(--border, #e5e7eb); padding-top: 8px; }
.qhd-handoff-label { margin-right: auto; font-size: .72rem; color: #6b7280; }

/* group 0243 R0001: reference options — a plain vertical stack of neutral buttons, with no
   recommendation accent and nothing preselected (0022 rule). Only the user's own pick is
   accented, and only after they click it. */
.qhd-opts { display: flex; flex-direction: column; gap: 4px; margin: 0 0 2px; padding: 0; list-style: none; }
.qhd-opt-btn {
  text-align: left; font-size: .78rem; font-family: inherit; cursor: pointer;
  padding: 6px 9px; border: 1px solid var(--border); border-radius: 5px;
  background: #fff; color: var(--text);
}
.qhd-opt-btn:hover { border-color: #94a3b8; background: #f8fafc; }
.qhd-opt-btn.picked { border-color: var(--primary, #2563eb); background: #eff6ff; color: #1d4ed8; font-weight: 600; }
.qhd-opt-read {
  display: flex; align-items: center; gap: 5px;
  font-size: .76rem; color: var(--text-m);
  padding: 4px 8px; border: 1px solid var(--border); border-radius: 5px; background: #f8fafc;
}
.qhd-opt-read.picked { border-color: #86efac; background: #f0fdf4; color: #15803d; font-weight: 600; }

/* Theme the single scroll surface to match ReviewHistoryDialog (14px, tinted). */
.qhd-body { scrollbar-width: thin; scrollbar-color: #b8c4d6 #eef2f8; }
@supports selector(::-webkit-scrollbar) {
  .qhd-body { scrollbar-width: auto; scrollbar-color: auto; }
  .qhd-body::-webkit-scrollbar { width: 14px; }
  .qhd-body::-webkit-scrollbar-track { border-radius: 999px; background: #eef2f8; }
  .qhd-body::-webkit-scrollbar-thumb { border: 3px solid #eef2f8; border-radius: 999px; background: #b8c4d6; }
  .qhd-body::-webkit-scrollbar-thumb:hover { background: #94a3b8; }
}
</style>
