<template>
  <!-- flowgate.default.0350 T0004 — the base_untracked_conflict 409's sibling to
       GitBaseDirtyDialog: a merge bounced off never-committed files in the base
       checkout that sit on a path the group branch wants to create. Commit (keep
       the file) and delete (discard it) are opposite, irreversible-in-different-
       ways outcomes, so — same as base_dirty — the operator always chooses; the
       caller retries the original finalize once the blocked paths are gone. -->
  <teleport to="body">
    <div v-if="open" class="modal-bg" @click.self="onBackdrop">
      <div class="modal-box guc-box">
        <div class="modal-hd">
          <span class="modal-title">
            <AppIcon name="warning" style="color:var(--danger);" />
            {{ t('main.git_finalize.untracked_conflict_dialog_title') }}
          </span>
          <button class="modal-close" type="button" :disabled="busy" @click="cancel">
            <AppIcon name="x" />
          </button>
        </div>
        <div class="modal-bd">
          <p class="guc-body">{{ t('main.git_finalize.untracked_conflict_dialog_body') }}</p>
          <ul v-if="files.length" class="guc-files">
            <li v-for="f in files" :key="f">{{ f }}</li>
          </ul>
          <div class="guc-commit">
            <label class="guc-commit-label" for="guc-commit-subject">
              {{ t('main.git_finalize.commit_message_label') }}
            </label>
            <input
              id="guc-commit-subject"
              class="form-ctrl guc-commit-input"
              type="text"
              maxlength="200"
              :value="commitMsg"
              :placeholder="suggested"
              :disabled="busy"
              @input="commitMsg = ($event.target as HTMLInputElement).value"
            />
            <p class="guc-hint">{{ t('main.git_finalize.commit_message_hint') }}</p>
          </div>
          <p class="guc-remove-note">
            <AppIcon name="warning" />
            {{ t('main.git_finalize.untracked_conflict_remove_note') }}
          </p>
          <p v-if="errorMsg" class="guc-error">{{ errorMsg }}</p>
        </div>
        <div class="modal-ft guc-ft">
          <button class="btn btn-secondary" type="button" :disabled="busy" @click="cancel">
            {{ t('common.cancel') }}
          </button>
          <button
            class="btn guc-remove-btn"
            type="button"
            :disabled="busy || !files.length"
            @click="choose('remove')"
          >
            <AppIcon name="trash" /> {{ t('main.git_finalize.untracked_conflict_remove') }}
          </button>
          <button class="btn btn-primary" type="button" :disabled="busy" @click="choose('commit')">
            <AppIcon name="check" /> {{ t('main.git_finalize.untracked_conflict_commit') }}
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

const { t } = useI18n()

// Emitted so a caller that tracks untracked-file badges can resync (parity with
// GitBaseDirtyDialog's dirty-updated event); GitFinalizePanel/GitActionMenu do
// not currently listen, since only GitStatusPanel surfaces that badge.
const emit = defineEmits<{ 'untracked-updated': [remaining: string[]] }>()

interface GitResp {
  ok: boolean
  result?: { remaining_untracked?: string[] } | null
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

// Mirrors git_service.default_base_commit_message — the seeded placeholder is
// exactly what a blank commit derives on the server.
function defaultMessage(list: string[]): string {
  if (!list.length) return ''
  const joined = 'fix: ' + list.join(', ')
  if (joined.length <= COMMIT_SUBJECT_MAX) return joined
  return `fix: ${list[0]} 외 ${list.length - 1}건`.slice(0, COMMIT_SUBJECT_MAX)
}
const suggested = computed(() => defaultMessage(files.value))

// Imperative entry point. Given the project id and the 409's blocked-file list,
// opens the dialog and resolves 'proceed' once every blocked path is gone (the
// caller then retries the finalize) or 'cancel' if the operator backed out.
async function resolve(pid: string, blockedFiles: string[]): Promise<'proceed' | 'cancel'> {
  projectId = pid
  errorMsg.value = ''
  commitMsg.value = ''
  files.value = Array.isArray(blockedFiles) ? blockedFiles.filter(Boolean) : []
  open.value = true
  return new Promise((res) => {
    resolver = res
  })
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
function onBackdrop() {
  if (!busy.value) settle('cancel')
}

async function choose(mode: 'commit' | 'remove') {
  if (busy.value || !projectId || !files.value.length) return
  busy.value = true
  errorMsg.value = ''
  try {
    let data: GitResp
    if (mode === 'commit') {
      const msg = commitMsg.value.trim()
      const body: { paths: string[]; message?: string } = { paths: [...files.value] }
      if (msg) body.message = msg
      ;({ data } = await postRequest<GitResp>(
        `/api/v1/projects/${projectId}/git/base-commit`,
        body,
      ))
    } else {
      ;({ data } = await postRequest<GitResp>(
        `/api/v1/projects/${projectId}/git/base-remove`,
        { files: files.value },
      ))
    }
    if (data.ok === false) {
      errorMsg.value = data.error?.message || t('main.git_finalize.failed')
      busy.value = false
      return
    }
    const remaining: string[] = Array.isArray(data.result?.remaining_untracked)
      ? data.result.remaining_untracked : []
    emit('untracked-updated', remaining)
    const stillBlocked = files.value.filter((f) => remaining.includes(f))
    if (stillBlocked.length === 0) {
      settle('proceed')
    } else {
      // A raced/partial result left something blocked — keep the dialog open on
      // the reduced set so the operator acts again (never silently auto-decide).
      files.value = stillBlocked
      errorMsg.value = t('main.git_finalize.untracked_conflict_still')
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
.guc-box {
  max-width: 480px;
  width: 100%;
}
.guc-body {
  font-size: 0.85rem;
  line-height: 1.5;
  color: var(--text);
  margin: 0 0 10px;
}
.guc-files {
  margin: 0 0 12px;
  padding: 8px 10px 8px 26px;
  max-height: 160px;
  overflow: auto;
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 8px;
  background: var(--bg-subtle, #f8fafc);
}
.guc-files li {
  font: 0.76rem/1.6 var(--mono, ui-monospace, monospace);
  color: var(--text-m);
  word-break: break-all;
}
.guc-commit {
  margin-bottom: 10px;
}
.guc-commit-label {
  display: block;
  font-size: 0.74rem;
  font-weight: 700;
  color: var(--text-m);
  margin-bottom: 4px;
}
.guc-commit-input {
  width: 100%;
  font-family: var(--mono, ui-monospace, monospace);
  font-size: 0.78rem;
  padding: 5px 8px;
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 6px;
  background: var(--bg, #fff);
  color: var(--text, #0f172a);
}
.guc-hint {
  font-size: 0.7rem;
  color: var(--text-m);
  margin: 4px 0 0;
}
.guc-remove-note {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  font-size: 0.74rem;
  color: #b45309;
  margin: 0 0 4px;
}
.guc-error {
  font-size: 0.78rem;
  color: #b91c1c;
  margin: 6px 0 0;
}
.guc-ft {
  flex-wrap: wrap;
  gap: 8px;
}
/* Delete discards the only copy — a distinct danger-outline treatment so it is
   never mistaken for the primary commit action. */
.guc-remove-btn {
  background: #fff;
  color: #b91c1c;
  border: 1px solid #fca5a5;
}
.guc-remove-btn:hover:not(:disabled) {
  background: #fef2f2;
}
</style>
