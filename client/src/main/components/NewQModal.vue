<template>
  <div class="modal-bg" role="dialog" aria-modal="true" @keydown.escape="$emit('close')">
    <div class="modal-box modal-lg">
      <div class="modal-hd">
        <span class="modal-title">
          <span class="doc-tag c-Q" style="font-size:.7rem; padding:2px 6px; margin-right:4px;">Q</span>
          {{ t('main.new_q_modal.title') }}
        </span>
        <button class="modal-close" type="button" @click="$emit('close')">
          <i class="fa-solid fa-xmark"></i>
        </button>
      </div>

      <div class="modal-bd">
        <!-- Title -->
        <div class="form-group">
          <label class="form-label req">{{ t('main.new_q_modal.query_title') }}</label>
          <input
            v-model="form.title"
            class="form-ctrl"
            type="text"
            maxlength="120"
            :placeholder="t('main.new_q_modal.title_placeholder')"
          />
        </div>

        <!-- Related document -->
        <div class="form-group">
          <label class="form-label">{{ t('main.new_q_modal.related_document') }} <span style="color:var(--text-m); font-weight:400;">{{ t('main.new_q_modal.optional') }}</span></label>
          <input
            v-model="form.related_doc"
            class="form-ctrl"
            type="text"
            :placeholder="t('main.new_q_modal.related_placeholder')"
          />
        </div>

        <!-- Question list -->
        <div class="form-section">
          <div class="form-section-title">
            <i class="fa-solid fa-list-ol"></i>
            {{ t('main.new_q_modal.question_list') }}
            <span style="font-size:.72rem; font-weight:400; color:var(--text-m); margin-left:4px;">{{ t('main.new_q_modal.one_or_more') }}</span>
          </div>

          <div v-for="(_, idx) in form.questions" :key="idx" class="q-field-row">
            <span class="q-field-num">{{ idx + 1 }}</span>
            <textarea
              v-model="form.questions[idx]"
              class="form-ctrl"
              rows="3"
              style="resize:vertical;"
              :placeholder="t('main.new_q_modal.question_placeholder', { n: idx + 1 })"
            ></textarea>
            <button
              v-if="form.questions.length > 1"
              class="q-field-del"
              type="button"
              @click="removeQuestion(idx)"
            >
              <i class="fa-solid fa-xmark"></i>
            </button>
          </div>

          <button class="btn btn-outline btn-sm" type="button" style="margin-top:4px;" @click="addQuestion">
            <i class="fa-solid fa-plus"></i> {{ t('main.new_q_modal.add_question') }}
          </button>
        </div>
      </div>

      <div class="modal-ft">
        <div v-if="flashMessage" :class="['alert', flashOk ? 'alert-success' : 'alert-danger']" style="width:100%; margin-bottom:12px;">
          <i :class="flashOk ? 'fa-solid fa-check' : 'fa-solid fa-triangle-exclamation'"></i>
          <span>{{ flashMessage }}</span>
        </div>
        <button class="btn btn-secondary" type="button" @click="$emit('close')">{{ t('common.cancel') }}</button>
        <button class="btn btn-primary" type="button" :disabled="submitting" @click="submit">
          <i v-if="submitting" class="fa-solid fa-spinner fa-spin"></i>
          <i v-else class="fa-solid fa-circle-question"></i>
          {{ submitting ? t('main.new_q_modal.submitting') : t('main.new_q_modal.submit') }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { postRequest } from '@shared/api'
import { useProjectStore } from '../stores/project'

const emit = defineEmits<{
  close: []
  created: [payload: { qId: string }]
}>()

const projectStore = useProjectStore()
const { t } = useI18n()

const submitting = ref(false)
const flashMessage = ref('')
const flashOk = ref(false)

const form = ref({
  title: '',
  related_doc: '',
  questions: [''],
})

function addQuestion() {
  form.value.questions.push('')
}

function removeQuestion(idx: number) {
  form.value.questions.splice(idx, 1)
}

async function submit() {
  flashMessage.value = ''

  if (!form.value.title.trim()) {
    flashOk.value = false
    flashMessage.value = t('main.new_q_modal.error_title_required')
    return
  }

  const questions = form.value.questions.map((q) => q.trim()).filter((q) => q.length > 0)
  if (questions.length === 0) {
    flashOk.value = false
    flashMessage.value = t('main.new_q_modal.error_question_required')
    return
  }

  const projectId = projectStore.currentProjectId
  if (!projectId) {
    flashOk.value = false
    flashMessage.value = t('main.new_q_modal.error_project_required')
    return
  }

  submitting.value = true
  try {
    const payload: Record<string, unknown> = {
      project_id: projectId,
      title: form.value.title.trim(),
      questions,
    }
    if (form.value.related_doc.trim()) {
      payload.related_doc = form.value.related_doc.trim()
    }
    const res = await postRequest<any>('/api/v1/q', payload)
    const qId: string = (res.data as any)?.q_id ?? ''
    flashOk.value = true
    flashMessage.value = t('main.new_q_modal.created', { id: qId })
    emit('created', { qId })
  } catch (e: any) {
    flashOk.value = false
    flashMessage.value = e?.response?.data?.error_message ?? t('main.new_q_modal.create_failed')
  } finally {
    submitting.value = false
  }
}

defineExpose({ form, submitting })
</script>
