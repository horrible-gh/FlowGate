<template>
  <!-- flowgate.default.0177 0007-CH follow-up — the E3 base_dirty 409 must never
       be auto-resolved. Committing (keep the edits) and reverting (discard them)
       are opposite outcomes, so the operator always chooses. A merge that bounces
       off the guard opens this dialog; picking commit-then-merge or revert-then-
       merge clears the base and the caller retries the original finalize once.

       flowgate.default.0560 T0016 §2.2 (2단계) — migrated onto the common dialog layer;
       D0008 "기존 instance 이관 목적지" maps this instance to `form-actions` (a commit
       message field plus three footer actions). The imperative `resolve()` contract is
       untouched: `open` is still this component's own ref, the shell never flips it. -->
  <DialogShell
    ref="shellRef"
    :open="open"
    variant="form-actions"
    :closeable="!busy"
    :busy="busy"
    @request-close="cancel"
  >
    <template #header>
      <DialogHeader
        :title="t('main.git_finalize.base_dirty_dialog_title')"
        :closeable="!busy"
        @close="onHeaderClose"
      >
        <template #icon>
          <AppIcon name="warning" style="color:var(--danger);" />
        </template>
      </DialogHeader>
    </template>

    <template #default="{ descriptionId }">
      <p :id="descriptionId" class="gbd-body">{{ context === 'update' ? t('main.explorer.git_update_base_dirty') : t('main.git_finalize.base_dirty_dialog_body') }}</p>
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
          data-dialog-autofocus
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
    </template>

    <template #footer>
      <DialogFooter :actions="actions" :busy="busy">
        <template #action-revert>
          <AppIcon name="arrow-counter-clockwise" /> {{ t('main.git_finalize.base_dirty_revert_merge') }}
        </template>
        <template #action-commit>
          <AppIcon name="check" /> {{ t('main.git_finalize.base_dirty_commit_merge') }}
        </template>
      </DialogFooter>
    </template>
  </DialogShell>
</template>

<script setup lang="ts">
import AppIcon from '@shared/AppIcon.vue'
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { postRequest } from '@shared/api'
import { useExplorerStore } from '../stores/explorer'

import DialogFooter from './dialogs/DialogFooter.vue'
import DialogHeader from './dialogs/DialogHeader.vue'
import DialogShell from './dialogs/DialogShell.vue'
import type { DialogAction } from './dialogs/dialogTypes'

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
const shellRef = ref<InstanceType<typeof DialogShell> | null>(null)

let projectId = ''
let resolver: ((v: 'proceed' | 'cancel') => void) | null = null

/**
 * T0016 §2.1 #3 found this footer in the order `[취소] [되돌리기] [커밋]` — the cancel
 * button separated from the primary by a destructive action, the exact inversion of the
 * contract. The 1단계 markup fix put it right, and these roles now make the order a
 * property of `footerRolePriority`: danger(20) → cancel(30) → primary(40), i.e.
 * `[되돌리기] [취소] [커밋]`.
 *
 * T0016 §3 asked whether `tone: 'danger'` restores the old `.gbd-revert-btn` look. It
 * does not, and it is not meant to: `fg-dialog-btn--tone-danger` carries visuals only in
 * combination with `--primary` (dialog.css), so a `danger` role renders as the common
 * filled-red danger button rather than this component's private red-outline variant.
 * That is the normalisation 3순위 exists for, and the tone is kept explicit so the
 * intent of the action is readable from the action object alone.
 */
const actions = computed<DialogAction[]>(() => [
  {
    id: 'revert',
    label: t('main.git_finalize.base_dirty_revert_merge'),
    role: 'danger',
    tone: 'danger',
    disabled: busy.value || files.value.length === 0,
    onSelect: () => choose('revert'),
  },
  {
    id: 'cancel',
    label: t('common.cancel'),
    role: 'cancel',
    disabled: busy.value,
    onSelect: cancel,
  },
  {
    id: 'commit',
    label: t('main.git_finalize.base_dirty_commit_merge'),
    role: 'primary',
    disabled: busy.value,
    onSelect: () => choose('commit'),
  },
])

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

function onHeaderClose() {
  shellRef.value?.requestClose('header')
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
</style>
