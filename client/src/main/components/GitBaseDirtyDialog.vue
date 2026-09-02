<template>
  <!-- flowgate.default.0177 0007-CH follow-up — the E3 base_dirty 409 must never
       be auto-resolved. Committing (keep the edits) and reverting (discard them)
       are opposite outcomes, so the operator always chooses. A merge that bounces
       off the guard opens this dialog; picking commit-then-merge or revert-then-
       merge clears the base and the caller retries the original finalize once. -->
  <teleport to="body">
    <div v-if="open" class="modal-bg">
      <div class="modal-box gbd-box">
        <div class="modal-hd">
          <span class="modal-title">
            <AppIcon name="warning" style="color:var(--danger);" />
            {{ t('main.git_finalize.base_dirty_dialog_title') }}
          </span>
          <button class="modal-close" type="button" :disabled="busy" @click="cancel">
            <AppIcon name="x" />
          </button>
        </div>
        <div class="modal-bd">
          <p class="gbd-body">{{ context === 'update' ? t('main.explorer.git_update_base_dirty') : t('main.git_finalize.base_dirty_dialog_body') }}</p>
          <ul v-if="files.length" class="gbd-files">
            <li v-for="f in files" :key="f">{{ f }}</li>
          </ul>
          <div class="gbd-commit">
            <label class="gbd-commit-label" for="gbd-commit-subject">
              {{ t('main.git_finalize.commit_message_label') }}
            </label>
            <input
              id="gbd-commit-subject"
              class="form-ctrl gbd-commit-input"
              type="text"
              maxlength="200"
              :value="commitMsg"
              :placeholder="suggested"
              :disabled="busy"
              @input="commitMsg = ($event.target as HTMLInputElement).value"
            />
            <p class="gbd-hint">{{ t('main.git_finalize.commit_message_hint') }}</p>
          </div>
          <p v-if="files.length" class="gbd-revert-note">
            <AppIcon name="warning" />
            {{ t('main.git_finalize.base_dirty_revert_note') }}
          </p>
          <p v-if="errorMsg" class="gbd-error">{{ errorMsg }}</p>
        </div>
        <div class="modal-ft gbd-ft">
          <button class="btn btn-secondary" type="button" :disabled="busy" @click="cancel">
            {{ t('common.cancel') }}
          </button>
          <button
            class="btn gbd-revert-btn"
            type="button"
            :disabled="busy || !files.length"
            @click="choose('revert')"
          >
            <AppIcon name="arrow-counter-clockwise" /> {{ t('main.git_finalize.base_dirty_revert_merge') }}
          </button>
          <button class="btn btn-primary" type="button" :disabled="busy" @click="choose('commit')">
            <AppIcon name="check" /> {{ t('main.git_finalize.base_dirty_commit_merge') }}
          </button>
        </div>
      </div>
    </div>
  </teleport>
</template>

<script setup lang="ts">
import AppIcon from '@shared/AppIcon.vue'
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { postRequest } from '@shared/api'
import { useExplorerStore } from '../stores/explorer'

defineProps<{ context?: 'finalize' | 'update' }>()
const { t } = useI18n()
const explorerStore = useExplorerStore()

// Callers watch this to keep the file-tree "modified" badges in sync after the
// commit/revert lands (mirrors the status-panel badge triggers).
const emit = defineEmits<{ 'dirty-updated': [remaining: string[]] }>()

interface GitResp {
  ok: boolean
  result?: { remaining?: string[] } | null
  error?: { message?: string } | null
}

const COMMIT_SUBJECT_MAX = 200

const open = ref(false)
const busy = ref(false)
const files = ref<string[]>([])
const commitMsg = ref('')
const errorMsg = ref('')

let projectId = ''
let resolver: ((v: 'proceed' | 'cancel') => void) | null = null

// Mirrors git_service.default_base_commit_message (L0002 §2.2): the seeded
// placeholder is exactly what a blank commit derives on the server.
function defaultMessage(list: string[]): string {
  if (!list.length) return ''
  const joined = 'fix: ' + list.join(', ')
  if (joined.length <= COMMIT_SUBJECT_MAX) return joined
  return `fix: ${list[0]} and ${list.length - 1} more`.slice(0, COMMIT_SUBJECT_MAX)
}
const suggested = computed(() => defaultMessage(files.value))

// Imperative entry point. Given the project id and the 409's dirty-file list,
// opens the dialog and resolves 'proceed' once the base is clean (the caller
// then retries the finalize) or 'cancel' if the operator backed out.
async function resolve(pid: string, dirtyFiles: string[]): Promise<'proceed' | 'cancel'> {
  projectId = pid
  errorMsg.value = ''
  commitMsg.value = ''
  let list = Array.isArray(dirtyFiles) ? dirtyFiles.filter(Boolean) : []
  // The 409 always names the files; if the payload somehow lacked them, ask the
  // server so the revert path (which requires explicit paths) still has targets.
  if (!list.length) list = await fetchDirty(pid)
  files.value = list
  open.value = true
  return new Promise((res) => {
    resolver = res
  })
}

async function fetchDirty(pid: string): Promise<string[]> {
  try {
    // 0282 NR0003 finding 3: shared store fetch instead of a private git/status GET.
    const status = await explorerStore.fetchGitStatus(pid)
    const f = status?.base_dirty?.files
    return Array.isArray(f) ? f : []
  } catch {
    return []
  }
}

function settle(v: 'proceed' | 'cancel') {
  open.value = false
  busy.value = false
  const r = resolver
  resolver = null
  if (r) r(v)
}

function cancel() {
  if (busy.value) return
  settle('cancel')
}
async function choose(mode: 'commit' | 'revert') {
  if (busy.value || !projectId) return
  if (mode === 'revert' && !files.value.length) return
  busy.value = true
  errorMsg.value = ''
  try {
    let data: GitResp
    if (mode === 'commit') {
      const msg = commitMsg.value.trim()
      // Blank → omit; the server derives the identical §2.2 default itself.
      ;({ data } = await postRequest<GitResp>(
        `/api/v1/projects/${projectId}/git/base-commit`,
        msg ? { message: msg } : {},
      ))
    } else {
      ;({ data } = await postRequest<GitResp>(
        `/api/v1/projects/${projectId}/git/base-revert`,
        { files: files.value },
      ))
    }
    if (data.ok === false) {
      errorMsg.value = data.error?.message || t('main.git_finalize.failed')
      busy.value = false
      return
    }
    const remaining: string[] = Array.isArray(data.result?.remaining) ? data.result.remaining : []
    explorerStore.setBaseDirtyFiles(projectId, remaining)
    emit('dirty-updated', remaining)
    if (remaining.length === 0) {
      settle('proceed')
    } else {
      // A raced/partial result left something dirty — keep the dialog open on the
      // reduced set so the operator acts again (never silently auto-decide).
      files.value = remaining
      errorMsg.value = t('main.git_finalize.base_dirty_still')
      busy.value = false
    }
  } catch (e: any) {
    errorMsg.value = e?.response?.data?.error?.message || t('main.git_finalize.failed')
    busy.value = false
  }
}

defineExpose({ resolve })
</script>

<style scoped>
.gbd-box {
  max-width: 480px;
  width: 100%;
}
.gbd-body {
  font-size: 0.85rem;
  line-height: 1.5;
  color: var(--text);
  margin: 0 0 10px;
}
.gbd-files {
  margin: 0 0 12px;
  padding: 8px 10px 8px 26px;
  max-height: 160px;
  overflow: auto;
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 8px;
  background: var(--bg-subtle, #f8fafc);
}
.gbd-files li {
  font: 0.76rem/1.6 var(--mono, ui-monospace, monospace);
  color: var(--text-m);
  word-break: break-all;
}
.gbd-commit {
  margin-bottom: 10px;
}
.gbd-commit-label {
  display: block;
  font-size: 0.74rem;
  font-weight: 700;
  color: var(--text-m);
  margin-bottom: 4px;
}
.gbd-commit-input {
  width: 100%;
  font-family: var(--mono, ui-monospace, monospace);
  font-size: 0.78rem;
  padding: 5px 8px;
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 6px;
  background: var(--bg, #fff);
  color: var(--text, #0f172a);
}
.gbd-hint {
  font-size: 0.7rem;
  color: var(--text-m);
  margin: 4px 0 0;
}
.gbd-revert-note {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  font-size: 0.74rem;
  color: #b45309;
  margin: 0 0 4px;
}
.gbd-error {
  font-size: 0.78rem;
  color: #b91c1c;
  margin: 6px 0 0;
}
.gbd-ft {
  flex-wrap: wrap;
  gap: 8px;
}
/* Revert discards edits — a distinct danger-outline treatment so it is never
   mistaken for the primary commit action. */
.gbd-revert-btn {
  background: #fff;
  color: #b91c1c;
  border: 1px solid #fca5a5;
}
.gbd-revert-btn:hover:not(:disabled) {
  background: #fef2f2;
}
</style>
