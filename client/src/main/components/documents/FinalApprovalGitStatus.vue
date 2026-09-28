<template>
  <div v-if="state && state.status !== 'none'" class="ac-git-status" data-test="ac-git-status">
    <div class="ac-git-status__head"><span><AppIcon name="git-branch" /> {{ state.branch || '-' }}</span><span class="badge">{{ state.status }}</span></div>
    <p v-if="aheadBehind" class="ac-git-status__meta">{{ aheadBehind }}</p>
    <p v-if="state.approval_pending" class="ac-git-status__pending"><AppIcon name="warning" /> Git 완료 · 승인 미완료 — 같은 승인 버튼으로 승인만 재시도합니다.</p>
    <p v-else-if="state.status === 'conflict'" class="ac-git-status__pending"><AppIcon name="warning" /> 충돌 해결 또는 병합 검수가 필요합니다.</p>
    <p v-else-if="terminal" class="ac-git-status__done"><AppIcon name="check-circle" /> {{ completed ? '최종승인 Git 처리 완료' : 'Git 처리가 완료되었습니다.' }}<span v-if="state.merge_commit"> · {{ state.merge_commit }}</span></p>
    <p v-else class="ac-git-status__meta">Git 작업은 최종승인에서 함께 처리됩니다.</p>
    <button v-if="state.status === 'conflict'" type="button" class="btn btn-secondary btn-sm" data-test="ac-git-open-recovery" @click="openRecovery"><AppIcon name="arrow-right" /> 충돌 해결로 이동</button>
  </div>
</template>
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { getRequest } from '@shared/api'
import AppIcon from '@shared/AppIcon.vue'
interface GitState { branch?: string | null; base_branch?: string | null; status: string; ahead_count?: number; behind_count?: number; merge_commit?: string | null; approval_pending?: boolean }
const props = defineProps<{ groupId: string; completed: boolean }>()
const state = ref<GitState | null>(null)
const terminal = computed(() => ['merged', 'pushed', 'discarded', 'archived', 'stashed'].includes(state.value?.status || ''))
const aheadBehind = computed(() => state.value ? `${state.value.base_branch || 'base'} 기준 ahead ${Number(state.value.ahead_count || 0)} · behind ${Number(state.value.behind_count || 0)}` : '')
async function fetchState() {
  if (!props.groupId) return
  try {
    const { data } = await getRequest<{ state: GitState }>(`/api/v1/groups/${props.groupId}/git/finalize?context=approval`)
    state.value = data.state
  } catch { state.value = null }
}
function matchesGroup(event: Event) { const detail = (event as CustomEvent).detail || {}; return !detail.group_id || detail.group_id === props.groupId }
function refresh(event: Event) { if (matchesGroup(event)) void fetchState() }
function openRecovery() { window.dispatchEvent(new CustomEvent('fg:git_status_open', { detail: { group_id: props.groupId, status: state.value?.status || 'conflict' } })) }
watch(() => props.groupId, fetchState, { immediate: true })
onMounted(() => { window.addEventListener('fg:git_pending_changed', refresh); window.addEventListener('fg:git_status_refresh', refresh); window.addEventListener('fg:git_finalize_done', refresh) })
onBeforeUnmount(() => { window.removeEventListener('fg:git_pending_changed', refresh); window.removeEventListener('fg:git_status_refresh', refresh); window.removeEventListener('fg:git_finalize_done', refresh) })
</script>
<style scoped>
.ac-git-status { width: min(100%, 560px); margin-top: 14px; padding: 14px 16px; border: 1px solid var(--border, #dbe3ec); border-radius: 10px; text-align: left; }
.ac-git-status__head { display: flex; justify-content: space-between; gap: 12px; font-weight: 600; }
.ac-git-status__meta, .ac-git-status__pending, .ac-git-status__done { margin: 9px 0 0; color: var(--text-m, #64748b); font-size: .84rem; }
.ac-git-status__pending { color: var(--warning-text, #92400e); }
.ac-git-status__done { color: var(--success, #15803d); }
.ac-git-status .btn { margin-top: 12px; }
</style>