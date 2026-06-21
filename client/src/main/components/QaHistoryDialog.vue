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
            <i class="fa-solid fa-circle-question" style="color:var(--primary, #2563eb); margin-right:6px;"></i>{{ t('main.qa_history.title') }}
          </div>
          <button type="button" class="modal-close" @click="onClose">
            <i class="fa-solid fa-xmark"></i>
          </button>
        </div>

        <!-- Body -->
        <div class="modal-bd qhd-body">
          <p class="qhd-desc">{{ t('main.qa_history.desc') }}</p>

          <div v-if="items.length === 0" class="qhd-empty">{{ t('main.qa_history.empty') }}</div>

          <ul v-else class="qhd-list">
            <li v-for="item in items" :key="item.id" class="qhd-entry">
              <div class="qhd-head">
                <span class="qhd-badge" :class="answered(item) ? 'done' : 'pending'">
                  {{ answered(item) ? t('main.doc_info_panel.qa_answered') : t('main.doc_info_panel.qa_answering') }}
                </span>
                <span class="qhd-seq">Q{{ item.seq }}</span>
                <span v-if="item.title" class="qhd-title-text">{{ item.title }}</span>
                <span class="qhd-asker">
                  <i :class="item.asker_kind === 'ai' ? 'fa-solid fa-robot' : 'fa-solid fa-user'"></i>
                  {{ item.asker_kind === 'ai' ? t('main.doc_info_panel.qa_by_ai') : t('main.doc_info_panel.qa_by_human') }}
                </span>
              </div>
              <!-- Full content, no inner scroll: the dialog body is the single scroll
                   surface (mirrors ReviewHistoryDialog's full-content view). -->
              <div class="qhd-blabel">{{ t('main.doc_info_panel.qa_question') }}</div>
              <p class="qhd-box">{{ item.body }}</p>
              <template v-if="(item.answers?.length ?? 0) > 0">
                <div class="qhd-blabel">{{ t('main.doc_info_panel.qa_answer') }}</div>
                <p v-for="(a, ai) in item.answers" :key="ai" class="qhd-box qhd-answer">
                  <i :class="a.author_kind === 'ai' ? 'fa-solid fa-robot' : 'fa-solid fa-user'" class="qhd-answer-icon"></i>
                  <span>{{ a.body }}</span>
                </p>
              </template>

              <!-- R0001 (group 0093): answer directly from the full view. The actions
                   call the parent's bound write functions (shared useQaAnswers state),
                   so a submitted answer refetches once and updates both this dialog and
                   the side panel in sync. Only shown when the dialog is given a docId. -->
              <template v-if="docId">
                <div v-if="answerOpenId === item.id" class="qhd-answer-form">
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
                      :disabled="!answerBody.trim() || busy"
                      @click="onSubmitAnswer(item.id)"
                    >{{ t('main.doc_info_panel.qa_answer_submit') }}</button>
                  </div>
                </div>
                <div v-else class="qhd-answer-actions">
                  <button type="button" class="btn btn-outline btn-sm" @click="openAnswer(item.id)">
                    {{ t('main.doc_info_panel.qa_answer_write') }}
                  </button>
                  <button type="button" class="btn btn-outline btn-sm" :disabled="busy" @click="onRequestAi(item.id)">
                    <i class="fa-solid fa-robot"></i> {{ t('main.doc_info_panel.qa_answer_ai') }}
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
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { QaItem } from '../composables/useQaAnswers'

const props = withDefaults(defineProps<{
  visible: boolean
  items: QaItem[]
  // R0001 (group 0093): when a docId and write actions are supplied, the full view
  // becomes answer-capable. Omitting them keeps the dialog read-only (back-compat).
  docId?: string
  busy?: boolean
  submitAnswer?: (itemId: number, body: string) => Promise<boolean>
  requestAiAnswer?: (itemId: number) => Promise<boolean>
}>(), {
  docId: '',
  busy: false,
  submitAnswer: undefined,
  requestAiAnswer: undefined,
})

const emit = defineEmits<{ 'update:visible': [value: boolean] }>()

const { t } = useI18n()

const answerOpenId = ref<number | null>(null)
const answerBody = ref('')

function answered(item: QaItem): boolean {
  return (item.answer_count ?? item.answers?.length ?? 0) > 0
}

function openAnswer(id: number) {
  answerOpenId.value = id
  answerBody.value = ''
}
function closeAnswer() {
  answerOpenId.value = null
  answerBody.value = ''
}

async function onSubmitAnswer(itemId: number) {
  if (!props.submitAnswer || !answerBody.value.trim() || props.busy) return
  if (await props.submitAnswer(itemId, answerBody.value)) {
    closeAnswer()
  }
}

async function onRequestAi(itemId: number) {
  if (!props.requestAiAnswer || props.busy) return
  await props.requestAiAnswer(itemId)
}

// Reset any open answer form when the dialog is closed so it reopens clean.
watch(() => props.visible, (v) => { if (!v) closeAnswer() })

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
.qhd-entry { border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; }
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
