<template>
  <section v-if="entries.length" class="unapproved-history" data-test="unapproved-history" aria-live="polite">
    <strong>{{ ko ? '미승인 진행 이력' : 'Progress without approval' }}</strong>
    <p v-if="entries.some(entry => entry.valid && entry.type_code === 'TR2')" class="warning" data-test="unapplied-summary">
      {{ ko ? '미승인 진행한 반영안은 소스에 적용되지 않았습니다.' : 'Proposals that proceeded without approval were not applied to source.' }}
    </p>
    <ul>
      <li v-for="entry in entries" :key="entry.decision_id" :class="{ invalid: !entry.valid }">
        <button type="button" class="source-link" @click="openDocument(entry)">{{ entry.result_doc_id }}</button>
        · r{{ entry.document_revision }} · {{ entry.actor_user_id }} · <time>{{ entry.decided_at }}</time>
        <small>{{ entry.decision_id }}</small>
        <span v-if="!entry.valid">{{ ko ? '과거 결정 · 현재 완료 근거에서 제외' : 'Historical decision · no longer completes the slot' }}</span>
        <span v-else class="warning" data-test="unapproved-warning">
          {{ entry.type_code === 'TR2' ? (ko ? '반영안은 소스에 적용되지 않았습니다.' : 'The proposal was not applied to source.')
            : (ko ? 'TSR 실제 판정: ' : 'Actual TSR verdict: ') + (entry.actual_result?.overall || entry.test_gate?.overall || 'NOT_RUN') }}
        </span>
        <span v-if="entry.actual_result?.error">{{ entry.actual_result.error }}</span>
        <span v-if="entry.approval_attempt?.error_code">{{ ko ? '기존 반영 실패: ' : 'Previous apply failure: ' }}{{ entry.approval_attempt.error_code }}</span>
        <span v-if="entry.valid && entry.continuation_status !== 'not_started'" data-test="continuation-state">
          {{ continuationLabel(entry.continuation_status) }}
        </span>
      </li>
    </ul>
  </section>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { getRequest } from '@shared/api'
import { useTabsStore } from '../stores/tabs'
import type { UnapprovedDecision } from '../workflow/unapprovedProgress'
import { useToast } from './common/useToast'
defineProps<{ entries: UnapprovedDecision[] }>()
const { locale } = useI18n()
const ko = computed(() => String(locale.value).startsWith('ko'))
const tabs = useTabsStore()
const { showToast } = useToast()
function continuationLabel(status: string): string {
  const labels: Record<string, [string, string]> = {
    pending: ['후속 실행 대기', 'Continuation pending'], running: ['후속 실행 중', 'Continuation running'],
    completed: ['후속 실행 시작 확인', 'Continuation start confirmed'],
    retryable_failed: ['슬롯 완료 · 후속 시작 실패, 재시도 대기', 'Slot complete · continuation failed, retry pending'],
    recovery_required: ['슬롯 완료 · 후속 실행 복구 필요', 'Slot complete · continuation recovery required'],
    cancelled: ['후속 실행 취소', 'Continuation cancelled'],
  }
  return labels[status]?.[ko.value ? 0 : 1] ?? (ko.value ? '후속 상태 확인 필요' : 'Continuation state unavailable')
}
async function openDocument(entry: UnapprovedDecision) {
  try {
    const response = await getRequest<any>(`/api/v1/documents/detail?doc_id=${encodeURIComponent(entry.result_doc_id)}`)
    const doc = response.data?.data ?? response.data
    if (!doc?.doc_id) return
    tabs.openTab({ id: doc.doc_id, title: doc.title ?? doc.doc_id, path: doc.file_path ?? '',
      type: 'md', mdPath: doc.file_path ?? null, typeCode: doc.type_code ?? null })
  } catch { showToast(ko.value ? '원문을 열 수 없습니다.' : 'Could not open the source document.', 'danger') }
}
</script>
<style scoped>
.unapproved-history { padding: 10px 16px; color: var(--text); background: var(--surface-h); border: 1px solid var(--border); border-radius: 6px; font-size: .8rem; text-align: left; }
ul { margin: 6px 0 0; padding-left: 18px; max-height: 160px; overflow: auto; }
li { margin: 6px 0; overflow-wrap: anywhere; }
small, .warning, li > span { display: block; }
.warning { color: var(--warning, #92400e); font-weight: 600; }
.invalid { color: var(--text-m); }
.source-link { border: 0; padding: 0; background: transparent; color: var(--primary); cursor: pointer; text-decoration: underline; }
</style>
