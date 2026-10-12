import { onBeforeUnmount, onMounted, ref, watch, type Ref } from 'vue'
import { getRequest } from '@shared/api'

export interface UnapprovedDecision {
  decision_id: string; result_doc_id: string; document_revision: number
  actor_user_id: string; decided_at: string; type_code: string; valid: boolean
  invalidated_at?: string | null; invalidation_reason?: string | null
  test_gate?: { overall?: string | null; passed?: boolean; applies?: boolean } | null
  actual_result?: { overall?: string | null; status?: string; error?: string | null } | null
  approval_attempt?: { state?: string; result_code?: string; error_code?: string | null } | null
  source_applied?: boolean | null; continuation_status: string; continuation_error?: string | null
}
export interface UnapprovedProgress {
  allowed: boolean; reason?: string | null; document_revision: number
  slot_id: number; slot_generation: number; history: UnapprovedDecision[]
  actual_result?: { overall?: string | null; status?: string; error?: string | null } | null
  approval_attempt?: { state?: string; result_code?: string; error_code?: string | null } | null
  test_gate?: { overall?: string | null; passed?: boolean; applies?: boolean } | null
}

export function useUnapprovedProgress(url: Ref<string>) {
  const state = ref<UnapprovedProgress | null>(null)
  const unavailable = ref(false)
  let generation = 0
  async function refresh() {
    const current = ++generation
    try {
      const response = await getRequest<UnapprovedProgress>(url.value)
      if (current !== generation) return
      state.value = response.data
      unavailable.value = false
    } catch {
      if (current !== generation) return
      state.value = null
      unavailable.value = true
    }
  }
  watch(url, () => { state.value = null; void refresh() }, { immediate: true })
  const pull = () => { void refresh() }
  onMounted(() => {
    window.addEventListener('fg:workflow_completion_changed', pull)
    window.addEventListener('fg:open_docs_refresh', pull)
    window.addEventListener('focus', pull)
    window.addEventListener('online', pull)
  })
  onBeforeUnmount(() => {
    generation++
    window.removeEventListener('fg:workflow_completion_changed', pull)
    window.removeEventListener('fg:open_docs_refresh', pull)
    window.removeEventListener('focus', pull)
    window.removeEventListener('online', pull)
  })
  return { state, unavailable, refresh }
}

export function progressReason(code: string | null | undefined, ko: boolean): string {
  if (code === 'completion_permission_denied' || code === 'completion_human_confirmation_required')
    return ko ? '기존 승인 권한이 필요합니다.' : 'Existing approval permission is required.'
  if (code === 'completion_already_completed') return ko ? '이 슬롯은 미승인 진행되었습니다.' : 'This slot already proceeded without approval.'
  if (code === 'workflow_not_head') return ko ? '현재 단계에서만 진행할 수 있습니다.' : 'Only the current step can proceed.'
  if (code === 'completion_test_run_active' || code === 'completion_test_not_settled') return ko ? '시험이 진행 중입니다.' : 'A test is still in progress.'
  if (code === 'completion_ai_run_active') return ko ? '기존 실행이 진행 중입니다.' : 'An existing run is active.'
  if (code === 'group_disposed') return ko ? '폐기된 그룹입니다.' : 'This group is disposed.'
  if (code === 'completion_source_locked') return ko ? '소스 작업이 진행 중입니다.' : 'Source work is in progress.'
  if (code?.includes('recovery') || code?.includes('revert') || code === 'tr2_in_progress') return ko ? '반영·복구 작업을 먼저 완료해야 합니다.' : 'Apply or recovery work must finish first.'
  return ko ? '진행 가능 여부를 확인할 수 없습니다. 새로고침해 주세요.' : 'Progress is unavailable. Please refresh.'
}
