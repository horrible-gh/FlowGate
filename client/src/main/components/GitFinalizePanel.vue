<template>
  <!-- 0115 R0001-4: git finalize panel — visible only for git-integrated groups
       whose worktree exists (status !== 'none'); everyone else sees nothing. -->
  <div v-if="state && state.status !== 'none'" class="card git-fin-card">
    <div class="card-hd">
      <span class="card-title">
        <i class="fa-solid fa-code-branch" style="color:var(--text-m);"></i>
        {{ t('main.git_finalize.title') }}
      </span>
      <span class="git-branch-badge">
        <i class="fa-solid fa-code-commit"></i> {{ state.branch }}
      </span>
      <span class="badge" :class="statusBadgeClass">{{ statusLabel }}</span>
      <button class="git-refresh-btn" :disabled="busy" @click="fetchState" :title="t('main.git_finalize.refresh')">
        <i class="fa-solid fa-rotate"></i>
      </button>
    </div>
    <div class="card-bd pad">
      <p v-if="aheadBehindText" class="git-fin-meta">{{ aheadBehindText }}</p>

      <!-- awaiting_choice / waiting: pick merge / push / wait -->
      <template v-if="state.status === 'awaiting_choice' || state.status === 'waiting'">
        <div class="git-choice-row">
          <label v-for="c in state.choices" :key="c" class="git-choice" :class="{ sel: chosen === c }">
            <input type="radio" name="git-fin-action" :value="c" v-model="chosen" />
            <span class="git-choice-label">{{ actionLabel(c) }}</span>
            <span class="git-choice-desc">{{ actionDesc(c) }}</span>
          </label>
        </div>
        <div class="flex" style="justify-content:flex-end; margin-top:10px;">
          <button class="btn btn-primary" :disabled="busy || !chosen" @click="runFinalize">
            <i class="fa-solid fa-play"></i>
            {{ busy ? t('main.git_finalize.running') : t('main.git_finalize.execute') }}
          </button>
        </div>
      </template>

      <!-- merged / pushed: terminal states -->
      <template v-else-if="state.status === 'merged'">
        <p class="git-fin-done">
          <i class="fa-solid fa-circle-check"></i>
          {{ t('main.git_finalize.merged_msg', { base: state.base_branch, commit: mergeCommit || '-' }) }}
        </p>
      </template>
      <template v-else-if="state.status === 'pushed'">
        <p class="git-fin-done">
          <i class="fa-solid fa-circle-check"></i>
          {{ t('main.git_finalize.pushed_msg', { branch: state.branch }) }}
        </p>
      </template>
      <template v-else-if="state.status === 'merging'">
        <p class="git-fin-meta"><i class="fa-solid fa-spinner fa-spin"></i> {{ t('main.git_finalize.merging_msg') }}</p>
      </template>

      <!-- conflict: resolution editor (P0005 §6) -->
      <template v-else-if="state.status === 'conflict'">
        <p class="git-fin-conflict-msg">
          <i class="fa-solid fa-triangle-exclamation"></i>
          {{ t('main.git_finalize.conflict_msg', { n: conflictFiles.length }) }}
        </p>
        <div v-for="f in conflictFiles" :key="f.path" class="git-conflict-file">
          <div class="git-conflict-path">
            <i class="fa-solid fa-file-code"></i> {{ f.path }}
            <span class="git-conflict-count">{{ t('main.git_finalize.conflict_count', { n: f.conflict_count }) }}</span>
          </div>
          <textarea
            v-model="f.edited"
            class="git-conflict-editor"
            spellcheck="false"
            rows="14"
          ></textarea>
        </div>
        <p v-if="conflictError" class="git-fin-conflict-msg">{{ conflictError }}</p>
        <div class="flex" style="justify-content:flex-end; gap:10px; margin-top:10px;">
          <button class="btn btn-secondary" :disabled="busy" @click="abortMerge">
            <i class="fa-solid fa-ban"></i> {{ t('main.git_finalize.abort') }}
          </button>
          <button class="btn btn-primary" :disabled="busy || !conflictFiles.length" @click="submitResolve">
            <i class="fa-solid fa-check"></i> {{ t('main.git_finalize.resolve_submit') }}
          </button>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getRequest, postRequest } from '@shared/api'
import { useToast } from './common/useToast'

const props = defineProps<{ groupId: string }>()

const { t } = useI18n()
const { showToast } = useToast()

interface GitFinState {
  group_id: string
  branch: string | null
  base_branch: string | null
  status: string
  default_action: string | null
  choices: string[]
  ahead_count: number | null
  behind_count: number | null
  merge_id: number | null
}

const state = ref<GitFinState | null>(null)
const chosen = ref<string>('')
const busy = ref(false)
const mergeCommit = ref<string | null>(null)
const conflictFiles = ref<Array<{ path: string; conflict_count: number; edited: string }>>([])
const conflictError = ref('')

const statusLabel = computed(() =>
  state.value ? t(`main.git_finalize.status.${state.value.status}`) : '',
)
const statusBadgeClass = computed(() => {
  switch (state.value?.status) {
    case 'merged':
    case 'pushed':
      return 'badge-blue'
    case 'conflict':
      return 'badge-red'
    default:
      return 'badge-yellow'
  }
})
const aheadBehindText = computed(() => {
  const s = state.value
  if (!s || s.ahead_count == null || s.behind_count == null) return ''
  return t('main.git_finalize.ahead_behind', { ahead: s.ahead_count, behind: s.behind_count })
})

function actionLabel(c: string): string {
  return t(`main.git_finalize.action.${c}`)
}
function actionDesc(c: string): string {
  return t(`main.git_finalize.action_desc.${c}`)
}

async function fetchState() {
  if (!props.groupId) {
    state.value = null
    return
  }
  try {
    const { data } = await getRequest<{ ok: boolean; state: GitFinState }>(
      `/api/v1/groups/${props.groupId}/git/finalize`,
    )
    state.value = data.state
    chosen.value = data.state.default_action || 'wait'
    if (data.state.status === 'conflict' && data.state.merge_id != null) {
      await fetchConflicts(data.state.merge_id)
    }
  } catch {
    state.value = null // 403/404/500 — panel simply stays hidden
  }
}

async function fetchConflicts(mergeId: number) {
  conflictError.value = ''
  try {
    const { data } = await getRequest<{
      ok: boolean
      files: Array<{ path: string; content: string; conflict_count: number }>
    }>(`/api/v1/groups/${props.groupId}/git/merge/${mergeId}/conflicts`)
    conflictFiles.value = (data.files || []).map((f) => ({
      path: f.path,
      conflict_count: f.conflict_count,
      edited: f.content,
    }))
  } catch {
    conflictFiles.value = []
  }
}

async function runFinalize() {
  if (!props.groupId || !chosen.value) return
  busy.value = true
  try {
    const { data } = await postRequest<{ ok: boolean; result?: any; error?: any }>(
      `/api/v1/groups/${props.groupId}/git/finalize`,
      { action: chosen.value },
    )
    if (data.ok === false) {
      showToast(data.error?.message || t('main.git_finalize.failed'), 'danger')
    } else {
      const r = data.result
      mergeCommit.value = r?.merge_commit || null
      if (r?.status === 'conflict') {
        showToast(t('main.git_finalize.conflict_toast', { n: (r.conflict_files || []).length }), 'warning')
      } else if (r?.status === 'merged') {
        showToast(t('main.git_finalize.merged_toast', { commit: r.merge_commit || '' }), 'success')
      } else if (r?.status === 'pushed') {
        showToast(t('main.git_finalize.pushed_toast'), 'success')
      } else if (r?.status === 'waiting') {
        showToast(t('main.git_finalize.waiting_toast'), 'success')
      }
    }
  } catch (e: any) {
    showToast(e?.response?.data?.error?.message || t('main.git_finalize.failed'), 'danger')
  } finally {
    busy.value = false
    await fetchState()
  }
}

async function submitResolve() {
  const mergeId = state.value?.merge_id
  if (!props.groupId || mergeId == null) return
  busy.value = true
  conflictError.value = ''
  try {
    const { data } = await postRequest<{ ok: boolean; result?: any; error?: any }>(
      `/api/v1/groups/${props.groupId}/git/merge/${mergeId}/resolve`,
      {
        files: conflictFiles.value.map((f) => ({ path: f.path, content: f.edited })),
        complete: true,
      },
    )
    if (data.ok === false) {
      conflictError.value = data.error?.message || t('main.git_finalize.failed')
    } else if (data.result?.status === 'merged') {
      mergeCommit.value = data.result.merge_commit || null
      showToast(t('main.git_finalize.merged_toast', { commit: data.result.merge_commit || '' }), 'success')
    }
  } catch (e: any) {
    conflictError.value = e?.response?.data?.error?.message || t('main.git_finalize.failed')
  } finally {
    busy.value = false
    await fetchState()
  }
}

async function abortMerge() {
  const mergeId = state.value?.merge_id
  if (!props.groupId || mergeId == null) return
  busy.value = true
  try {
    await postRequest(`/api/v1/groups/${props.groupId}/git/merge/${mergeId}/abort`, {})
    showToast(t('main.git_finalize.aborted_toast'), 'success')
  } catch (e: any) {
    showToast(e?.response?.data?.error?.message || t('main.git_finalize.failed'), 'danger')
  } finally {
    busy.value = false
    await fetchState()
  }
}

watch(() => props.groupId, fetchState, { immediate: true })

defineExpose({ fetchState })
</script>

<style scoped>
.git-fin-card {
  margin-bottom: 12px;
}
.git-fin-card .card-hd {
  display: flex;
  align-items: center;
  gap: 8px;
}
.git-branch-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 8px;
  font-size: 0.72rem;
  font-family: var(--mono, ui-monospace, monospace);
  color: #0369a1;
  background: #e0f2fe;
  border: 1px solid #bae6fd;
  border-radius: 999px;
}
.badge-red {
  background: #fef2f2;
  color: #b91c1c;
}
.git-refresh-btn {
  margin-left: auto;
  border: none;
  background: none;
  color: var(--text-m);
  cursor: pointer;
  padding: 4px 6px;
}
.git-refresh-btn:hover {
  color: var(--primary);
}
.git-fin-meta {
  font-size: 0.78rem;
  color: var(--text-m);
  margin: 0 0 8px;
}
.git-choice-row {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}
.git-choice {
  flex: 1 1 160px;
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 10px 12px;
  border: 1px solid var(--border, #e2e8f0);
  border-radius: var(--r, 8px);
  cursor: pointer;
}
.git-choice.sel {
  border-color: var(--primary);
  background: var(--primary-l, #eff6ff);
}
.git-choice input {
  display: none;
}
.git-choice-label {
  font-weight: 700;
  font-size: 0.82rem;
}
.git-choice-desc {
  font-size: 0.72rem;
  color: var(--text-m);
}
.git-fin-done {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 0.85rem;
  color: #166534;
  margin: 0;
}
.git-fin-conflict-msg {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 0.8rem;
  color: #b91c1c;
  margin: 0 0 8px;
}
.git-conflict-file {
  margin-bottom: 10px;
}
.git-conflict-path {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.78rem;
  font-family: var(--mono, ui-monospace, monospace);
  margin-bottom: 4px;
}
.git-conflict-count {
  font-size: 0.7rem;
  color: var(--text-m);
}
.git-conflict-editor {
  width: 100%;
  font-family: var(--mono, ui-monospace, monospace);
  font-size: 0.75rem;
  line-height: 1.45;
  border: 1px solid var(--border, #e2e8f0);
  border-radius: var(--r, 8px);
  padding: 8px;
  background: var(--bg, #fff);
  color: var(--text, #0f172a);
  resize: vertical;
}
</style>
