<template>
  <div class="answer-editor">
    <div class="answer-editor__label">{{ t('main.answer_editor.title') }}</div>

    <textarea
      v-model="answerBody"
      class="answer-editor__textarea"
      :placeholder="t('main.answer_editor.placeholder')"
      :disabled="submitting"
    />

    <!-- dispatch_mode selection -->
    <div>
      <div class="answer-editor__label" style="margin-bottom:8px;">{{ t('main.answer_editor.dispatch_mode') }}</div>
      <div class="answer-editor__dispatch">
        <div
          class="answer-editor__dispatch-opt"
          :class="{ selected: dispatchMode === 'command' }"
          @click="dispatchMode = 'command'"
        >
          <i class="fa-solid fa-terminal"></i> {{ t('main.answer_editor.auto_invoke_command') }}
        </div>
        <div
          class="answer-editor__dispatch-opt"
          :class="{ selected: dispatchMode === 'ment_copy' }"
          @click="dispatchMode = 'ment_copy'"
        >
          <i class="fa-regular fa-copy"></i> {{ t('main.answer_editor.copy_message') }}
        </div>
        <div
          class="answer-editor__dispatch-opt"
          :class="{ selected: dispatchMode === 'none' }"
          @click="dispatchMode = 'none'"
        >
          <i class="fa-solid fa-ban"></i> {{ t('main.answer_editor.no_dispatch') }}
        </div>
      </div>
    </div>

    <!-- command_id selection (when in command mode) -->
    <div v-if="dispatchMode === 'command'">
      <div class="answer-editor__label" style="margin-bottom:6px;">{{ t('main.answer_editor.select_command') }}</div>
      <div v-if="cmdLoading" style="font-size:.8rem; color:var(--text-m);">{{ t('main.answer_editor.loading_commands') }}</div>
      <div v-else-if="cmdError" style="font-size:.8rem; color:var(--danger);">{{ cmdError }}</div>
      <select
        v-else
        v-model="selectedCommandId"
        class="answer-editor__command-select"
        :disabled="submitting"
      >
        <option value="">{{ t('main.answer_editor.select_command_option') }}</option>
        <option
          v-for="cmd in commands"
          :key="cmd.command_id"
          :value="cmd.command_id"
        >
          {{ cmd.name }}
        </option>
      </select>
    </div>

    <!-- error message -->
    <div v-if="submitError" style="font-size:.8rem; color:var(--danger); padding:6px 10px; background:var(--danger-l,#fee2e2); border-radius:var(--r);">
      {{ submitError }}
    </div>

    <div class="answer-editor__footer">
      <button
        class="btn btn-primary btn-sm"
        :disabled="!canSubmit || submitting"
        @click="submit"
      >
        <i v-if="submitting" class="fa-solid fa-circle-notch fa-spin"></i>
        <i v-else class="fa-solid fa-paper-plane"></i>
        {{ submitting ? t('main.answer_editor.submitting') : t('main.answer_editor.submit') }}
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getRequest, postRequest } from '@shared/api'
import { useToast } from './common/useToast'
import { useMentionCopy } from '../composables/useMentionCopy'

const props = defineProps<{
  qDocId: string
  prevDocId: string | null
}>()

const emit = defineEmits<{
  submitted: [result: AnswerResult]
}>()

const { showToast } = useToast()
const { t } = useI18n()
// R0001 group 0015 / NR0003 rev4 — persist the "Q&A answer mention" copy for the header badge.
const { recordMentionCopy } = useMentionCopy()

interface Command {
  command_id: string
  name: string
}

interface AnswerResult {
  ok: boolean
  a_doc_id: string
  stored_path: string
  raw_token: string | null
  token_id: string | null
  scratch_dir: string | null
  expires_at: string | null
  dispatch_mode: string
  ment_text?: string | null
}

const answerBody = ref('')
const dispatchMode = ref<'command' | 'ment_copy' | 'none'>('ment_copy')
const selectedCommandId = ref('')
const submitting = ref(false)
const submitError = ref('')

const commands = ref<Command[]>([])
const cmdLoading = ref(false)
const cmdError = ref('')

const canSubmit = computed(() => {
  if (!answerBody.value.trim()) return false
  if (dispatchMode.value === 'command' && !selectedCommandId.value) return false
  return true
})

async function loadCommands() {
  cmdLoading.value = true
  cmdError.value = ''
  try {
    const res = await getRequest<{ commands: Command[] }>('/api/v1/commands')
    commands.value = (res.data as any)?.commands ?? []
  } catch (e: any) {
    cmdError.value = e?.response?.data?.detail ?? t('main.answer_editor.load_commands_failed')
  } finally {
    cmdLoading.value = false
  }
}

watch(() => dispatchMode.value, (mode) => {
  if (mode === 'command' && commands.value.length === 0) {
    loadCommands()
  }
})

async function submit() {
  if (!canSubmit.value || submitting.value) return
  submitting.value = true
  submitError.value = ''
  try {
    const body: Record<string, unknown> = {
      answer_body: answerBody.value,
      dispatch_mode: dispatchMode.value,
    }
    if (dispatchMode.value === 'command') {
      body.command_id = selectedCommandId.value
    }
    const res = await postRequest<AnswerResult>(
      `/api/v1/qa/${encodeURIComponent(props.qDocId)}/answer`,
      body,
    )
    const result = res.data as AnswerResult

    if (dispatchMode.value === 'ment_copy' && (result.ment_text || result.raw_token)) {
      await copyMentToClipboard(result)
      showToast(t('main.answer_editor.submitted_copied'), 'success')
      void recordMentionCopy(props.qDocId, 'qa_answer')
    } else if (dispatchMode.value === 'command') {
      showToast(t('main.answer_editor.submitted_invoked', { docId: result.a_doc_id }), 'success')
    } else {
      showToast(t('main.answer_editor.submitted', { docId: result.a_doc_id }), 'success')
    }

    answerBody.value = ''
    emit('submitted', result)
  } catch (e: any) {
    const msg = e?.response?.data?.error_message
      ?? e?.response?.data?.detail
      ?? t('main.answer_editor.submit_failed')
    submitError.value = msg
    showToast(msg, 'danger')
  } finally {
    submitting.value = false
  }
}

function buildQnaMentText(result: AnswerResult): string {
  if (result.ment_text?.trim()) return result.ment_text
  // Minimal fallback when server response has no ment_text (M020 excluded items not included)
  return [
    '[Q/A follow-up] An answer has been posted for the Q document.',
    `Q document ID: ${props.qDocId}`,
    `A document ID: ${result.a_doc_id}`,
  ].join('\n')
}

async function copyMentToClipboard(result: AnswerResult): Promise<void> {
  const text = buildQnaMentText(result)
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text)
      return
    } catch { /* fall through */ }
  }
  try {
    const el = document.createElement('textarea')
    el.value = text
    el.style.cssText = 'position:fixed;left:-9999px;top:-9999px;'
    document.body.appendChild(el)
    el.select()
    document.execCommand('copy')
    document.body.removeChild(el)
  } catch { /* ignore */ }
}

defineExpose({ answerBody, dispatchMode, submitting })
</script>
