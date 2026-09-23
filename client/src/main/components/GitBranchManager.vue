<template>
  <!-- flowgate.default.0594 T0016 §3/§4 — Branch Control Center. This section owns
       ALL branch lifecycle (create/merge/delete/current-target); it never runs a
       group finalize itself (that stays ReviewActionBar/GitStatusPanel's job, §4.2),
       and a failure loading its own catalog is caught locally (§4.1) so it can
       never blank out the surrounding Git finalize UI.
       T0018 — the host panel splits it across tabs through `view`: 'branches' is the
       read/select side (list + current target + create), 'manage' holds merge and the
       separate danger zone (delete). 'hidden' renders nothing but stays mounted so the
       catalog (and the `catalog` summary the host shows up top) survives tab switches.
       'all' is the standalone stack of every zone. -->
  <section v-if="view !== 'hidden'" class="branch-manager" data-test="branch-manager">
    <header class="branch-manager-hd">
      <strong><AppIcon name="git-branch" /> {{ t('main.git_branch_manager.title') }}</strong>
      <button class="btn btn-sm btn-secondary" type="button" :disabled="busy" data-test="branch-refresh" @click="load">
        <AppIcon name="arrow-clockwise" />{{ t('main.git_branch_manager.refresh') }}
      </button>
    </header>

    <p v-if="error" class="branch-error" role="alert">{{ error }}</p>

    <!-- §3.1 — the current target must be identifiable before anything else. Inside the
         tabbed panel the host's overview already carries it, so only the standalone
         stack repeats it here. -->
    <div v-if="view === 'all'" class="branch-summary">
      <div class="branch-summary-item">
        <span class="branch-summary-label">{{ t('main.git_branch_manager.current_target_label') }}</span>
        <span class="branch-summary-value" data-test="current-target-value">
          {{ catalog.default_merge_target || t('main.git_branch_manager.current_target_unset') }}
        </span>
      </div>
      <div class="branch-summary-item">
        <span class="branch-summary-label">{{ t('main.git_branch_manager.base_branch_label') }}</span>
        <span class="branch-summary-value branch-summary-value--mono">{{ catalog.base_branch || '-' }}</span>
      </div>
    </div>

    <!-- Read/select zone: no destructive control lives in these rows (T0018 §3.3). -->
    <section v-if="showBranches" class="branch-zone" data-test="branch-zone-list">
      <h4 class="branch-zone-title">{{ t('main.git_branch_manager.list_title') }}</h4>
      <div class="branch-list">
        <div v-for="branch in catalog.branches" :key="branch.name" class="branch-row">
          <span class="branch-name">{{ branch.name }}</span>
          <span class="badge" :class="kindBadgeClass(branch)">{{ kindLabel(branch) }}</span>
          <span v-if="branch.name === catalog.default_merge_target" class="badge badge-blue">
            {{ t('main.git_branch_manager.current_target_badge') }}
          </span>
          <span v-if="branch.connected_group_id" class="branch-meta">{{ branch.connected_group_id }}</span>
          <span v-if="branch.kind !== 'remote_only' && branch.has_remote_counterpart" class="branch-meta">
            {{ t('main.git_branch_manager.has_remote_badge') }}
          </span>
          <span class="branch-spacer"></span>
          <!-- §3.2 "merge target 지정/변경" — its own action, never a side effect of merge. -->
          <button
            v-if="canBeTarget(branch)"
            class="btn btn-sm btn-secondary"
            type="button"
            :disabled="busy"
            @click="setDefaultTarget(branch.name)"
          >{{ t('main.git_branch_manager.set_target_btn') }}</button>
          <button
            v-if="branch.name === catalog.default_merge_target"
            class="btn btn-sm btn-secondary"
            type="button"
            :disabled="busy"
            @click="setDefaultTarget(null)"
          >{{ t('main.git_branch_manager.clear_target_btn') }}</button>
        </div>
      </div>
    </section>

    <form v-if="showBranches" class="branch-zone branch-action-card" data-test="branch-zone-create" @submit.prevent="create">
      <h4 class="branch-zone-title">{{ t('main.git_branch_manager.create_title') }}</h4>
      <div class="branch-form-grid">
        <label class="branch-field">
          <span class="branch-field-label">{{ t('main.git_branch_manager.create_name_label') }}</span>
          <input
            v-model.trim="newName"
            class="branch-control"
            :aria-label="t('main.git_branch_manager.create_name_aria')"
            :placeholder="t('main.git_branch_manager.create_name_placeholder')"
          />
          <small>{{ t('main.git_branch_manager.create_name_hint') }}</small>
        </label>
        <label class="branch-field">
          <span class="branch-field-label">{{ t('main.git_branch_manager.create_source_label') }}</span>
          <select v-model="createSource" class="branch-control" :aria-label="t('main.git_branch_manager.create_source_aria')">
            <option v-for="branch in createCandidates" :key="branch.name" :value="branch.name">{{ branch.name }}</option>
          </select>
          <small>{{ t('main.git_branch_manager.create_source_hint') }}</small>
        </label>
        <button class="btn btn-sm btn-primary branch-create-btn" :disabled="busy || !newName || !createSource">
          {{ t('main.git_branch_manager.create_btn') }}
        </button>
      </div>
    </form>

    <form v-if="showManage" class="branch-zone branch-action-card" data-test="branch-zone-merge" @submit.prevent="confirmMerge">
      <h4 class="branch-zone-title">{{ t('main.git_branch_manager.merge_title') }}</h4>
      <p v-if="!hasMergeCandidates" class="branch-empty-state" data-test="merge-empty">
        {{ t('main.git_branch_manager.merge_empty') }}
      </p>
      <template v-else>
        <label class="branch-field">
          <span class="branch-field-label">{{ t('main.git_branch_manager.source_label') }}</span>
          <select v-model="mergeSource" class="branch-control" :aria-label="t('main.git_branch_manager.source_label')">
            <option v-for="branch in sourceCandidates" :key="branch.name" :value="branch.name">{{ branch.name }}</option>
          </select>
          <small>{{ t('main.git_branch_manager.source_hint') }}</small>
        </label>
        <label class="branch-field">
          <span class="branch-field-label">{{ t('main.git_branch_manager.target_label') }}</span>
          <select v-model="mergeTarget" class="branch-control" :aria-label="t('main.git_branch_manager.target_label')">
            <option v-for="branch in targetCandidates" :key="branch.name" :value="branch.name">{{ branch.name }}</option>
          </select>
          <small>{{ t('main.git_branch_manager.target_hint') }}</small>
        </label>
      </template>
      <!-- §3.3 — the current selection must also read as a plain sentence. -->
      <p v-if="hasMergeCandidates && mergeSource && mergeTarget" class="branch-merge-summary" data-test="merge-summary">
        {{ t('main.git_branch_manager.merge_summary', { source: mergeSource, target: mergeTarget }) }}
      </p>
      <div class="branch-form-actions">
        <button
          class="btn btn-sm btn-primary"
          data-test="merge-btn"
          :disabled="busy || !mergeSource || !mergeTarget || mergeSource === mergeTarget"
        >{{ t('main.git_branch_manager.merge_btn') }}</button>
      </div>

      <div
        v-if="mergeResult"
        class="branch-result"
        :class="{ 'branch-result--error': !!mergeResult.code }"
        role="status"
      >
        <strong>
          {{ mergeResult.code
            ? t('main.git_branch_manager.merge_failed_title')
            : t('main.git_branch_manager.merge_done_title') }}
        </strong>
        <span>{{ mergeResult.source }} → {{ mergeResult.target }}</span>
        <span v-if="mergeResult.code" class="branch-result-code">{{ mergeResult.code }}</span>
        <span v-if="mergeResult.pushFailed" class="branch-result-push" data-test="merge-push-failed">
          {{ t('main.git_branch_manager.merge_push_failed') }}
        </span>
        <span
          v-else-if="mergeResult.code && mergeResult.code !== 'branch_merge_conflict' && mergeResult.message"
          class="branch-result-message"
          data-test="merge-error-message"
        >{{ mergeResult.message }}</span>
        <ul v-if="mergeResult.files?.length">
          <li v-for="file in mergeResult.files" :key="file">{{ file }}</li>
        </ul>
      </div>
    </form>

    <!-- T0018 §3.3 — delete is the one irreversible action here, so it never shares a
         row with the list; it gets its own zone, one explicit pick, and the confirm. -->
    <section v-if="showManage" class="branch-zone branch-zone--danger" data-test="branch-zone-danger">
      <h4 class="branch-zone-title">{{ t('main.git_branch_manager.danger_title') }}</h4>
      <p class="branch-zone-hint">{{ t('main.git_branch_manager.danger_hint') }}</p>
      <p v-if="!deleteCandidates.length" class="branch-zone-hint">{{ t('main.git_branch_manager.delete_none') }}</p>
      <div v-else class="branch-form-row">
        <select
          v-model="deleteTarget"
          class="branch-control"
          data-test="delete-select"
          :aria-label="t('main.git_branch_manager.delete_select_aria')"
        >
          <option v-for="branch in deleteCandidates" :key="branch.name" :value="branch.name">
            {{ branch.can_delete ? branch.name : `${branch.name} · ${t('main.git_branch_manager.delete_protected_suffix')}` }}
          </option>
        </select>
        <button
          class="btn btn-sm btn-danger"
          type="button"
          data-test="delete-btn"
          :disabled="busy || !selectedDelete?.can_delete"
          :title="selectedDelete ? deleteReasonText(selectedDelete) : undefined"
          @click="selectedDelete && confirmDelete(selectedDelete)"
        >{{ t('main.git_branch_manager.delete_btn') }}</button>
      </div>
      <p v-if="selectedDelete && !selectedDelete.can_delete" class="branch-zone-hint" data-test="delete-blocked-reason">
        {{ deleteReasonText(selectedDelete) }}
      </p>

      <div
        v-if="deleteResult"
        class="branch-result branch-result--error"
        role="status"
        data-test="delete-result"
      >
        <strong>{{ t('main.git_branch_manager.delete_failed_title') }}</strong>
        <span>{{ deleteResult.branch }}</span>
        <span v-if="deleteResult.code" class="branch-result-code">{{ deleteResult.code }}</span>
        <span class="branch-result-message">{{ deleteResultReasonText(deleteResult) }}</span>
      </div>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { deleteRequest, getRequest, postRequest, putRequest } from '@shared/api'
import AppIcon from '@shared/AppIcon.vue'
import { confirm } from '../composables/useDialogStack'
import { useToast } from './common/useToast'

interface BranchRow {
  name: string
  kind: 'local' | 'base' | 'internal_slot' | 'remote_only'
  can_delete: boolean
  delete_blocked_reason?: string | null
  can_be_create_source: boolean
  connected_group_id?: string
  has_remote_counterpart?: boolean
}
/** What the host panel's overview needs from this catalog — nothing that could
 *  make a catalog failure reach the host's own finalize UI (§4.1). */
interface BranchCatalogSummary {
  state: 'ready' | 'error'
  base_branch: string | null
  default_merge_target: string | null
}
const props = withDefaults(defineProps<{
  projectId: string
  view?: 'all' | 'branches' | 'manage' | 'hidden'
}>(), { view: 'all' })
const emit = defineEmits<{ catalog: [summary: BranchCatalogSummary] }>()
const { t } = useI18n()
const { showToast } = useToast()
const catalog = ref<{ base_branch: string | null; default_merge_target: string | null; branches: BranchRow[] }>({
  base_branch: null, default_merge_target: null, branches: [],
})
const busy = ref(false)
const error = ref('')
const newName = ref('')
const createSource = ref('')
const mergeSource = ref('')
const mergeTarget = ref('')
const deleteTarget = ref('')
const mergeResult = ref<{ source: string; target: string; code?: string; message?: string; files?: string[]; pushFailed?: boolean } | null>(null)
const deleteResult = ref<{ branch: string; code?: string; message?: string } | null>(null)
const showBranches = computed(() => props.view === 'all' || props.view === 'branches')
const showManage = computed(() => props.view === 'all' || props.view === 'manage')
const ordinary = computed(() => (catalog.value.branches || []).filter(b => b.kind === 'local' || b.kind === 'base'))
const createCandidates = computed(() => (catalog.value.branches || []).filter(b => b.can_be_create_source && b.kind !== 'remote_only'))
const sourceCandidates = computed(() => ordinary.value.filter(b => b.name !== mergeTarget.value))
const targetCandidates = computed(() => ordinary.value.filter(b => b.name !== mergeSource.value))
const hasMergeCandidates = computed(() => ordinary.value.length >= 2)
// Protected branches stay pickable so the zone can say WHY they cannot go; the
// delete button itself follows the server's can_delete verdict only.
const deleteCandidates = computed(() => (catalog.value.branches || []).filter(b => b.kind !== 'remote_only'))
const selectedDelete = computed(() => deleteCandidates.value.find(b => b.name === deleteTarget.value) || null)

function canBeTarget(branch: BranchRow): boolean {
  return branch.kind !== 'remote_only' && branch.kind !== 'internal_slot' && branch.name !== catalog.value.default_merge_target
}
function kindLabel(branch: BranchRow): string {
  return t(`main.git_branch_manager.kind.${branch.kind}`)
}
function kindBadgeClass(branch: BranchRow): string {
  if (branch.kind === 'internal_slot') return 'badge-yellow'
  if (branch.kind === 'remote_only') return 'badge-muted'
  if (branch.kind === 'base') return 'badge-blue'
  return ''
}
function deleteReasonText(branch: BranchRow): string | undefined {
  if (branch.can_delete || !branch.delete_blocked_reason) return undefined
  return t(`main.git_branch_manager.delete_blocked.${branch.delete_blocked_reason}`, branch.delete_blocked_reason)
}
// §7 delete 실패 — reuses the same protected/in-use phrasing shown on the
// disabled-button tooltip so a delete that fails server-side (racing another
// client, or a reason not yet reflected in this catalog snapshot) reads the
// same way as a delete blocked up front.
function deleteResultReasonText(result: { code?: string; message?: string }): string {
  if (result.code) {
    return t(`main.git_branch_manager.delete_blocked.${result.code}`, result.message || result.code)
  }
  return result.message || t('main.git_branch_manager.op_failed')
}
function syncSelections() {
  const first = ordinary.value[0]?.name || ''
  if (!createCandidates.value.some(b => b.name === createSource.value)) createSource.value = catalog.value.base_branch || first
  if (!ordinary.value.some(b => b.name === mergeSource.value)) mergeSource.value = ordinary.value.find(b => b.name !== catalog.value.base_branch)?.name || first
  if (!targetCandidates.value.some(b => b.name === mergeTarget.value)) mergeTarget.value = catalog.value.default_merge_target || catalog.value.base_branch || targetCandidates.value[0]?.name || ''
  if (!deleteCandidates.value.some(b => b.name === deleteTarget.value)) {
    deleteTarget.value = deleteCandidates.value.find(b => b.can_delete)?.name || deleteCandidates.value[0]?.name || ''
  }
}
async function load() {
  if (!props.projectId) return
  error.value = ''
  try {
    const { data } = await getRequest<any>(`/api/v1/projects/${props.projectId}/git/branches`)
    catalog.value = {
      base_branch: data?.base_branch || null,
      default_merge_target: data?.default_merge_target || null,
      branches: Array.isArray(data?.branches) ? data.branches : [],
    }
    syncSelections()
    emit('catalog', { state: 'ready', base_branch: catalog.value.base_branch, default_merge_target: catalog.value.default_merge_target })
  } catch (e: any) {
    // §4.1 — this catalog's own read failure never reaches (or blanks) the
    // surrounding Git finalize UI; it only shows up in this section.
    error.value = e?.response?.data?.error?.message || t('main.git_branch_manager.load_failed')
    emit('catalog', { state: 'error', base_branch: catalog.value.base_branch, default_merge_target: catalog.value.default_merge_target })
  }
}
async function run(fn: () => Promise<void>) {
  busy.value = true
  error.value = ''
  try { await fn() } catch (e: any) { error.value = e?.response?.data?.error?.message || t('main.git_branch_manager.op_failed') } finally { busy.value = false }
}
async function create() {
  await run(async () => {
    await postRequest(`/api/v1/projects/${props.projectId}/git/branches`, { name: newName.value, source_branch: createSource.value })
    newName.value = ''
    await load()
  })
}
async function setDefaultTarget(branch: string | null) {
  await run(async () => {
    await putRequest(`/api/v1/projects/${props.projectId}/git/branches/default-target`, { branch })
    showToast(
      branch
        ? t('main.git_branch_manager.target_set_toast', { branch })
        : t('main.git_branch_manager.target_cleared_toast'),
      'success',
    )
    await load()
  })
}
async function confirmDelete(branch: BranchRow) {
  if (!branch.can_delete || busy.value) return
  const ok = await confirm({
    title: t('main.git_branch_manager.delete_confirm_title', { branch: branch.name }),
    message: t('main.git_branch_manager.delete_confirm_message', { branch: branch.name }),
    danger: true,
    confirmLabel: t('main.git_branch_manager.delete_btn'),
  })
  if (!ok) return
  deleteResult.value = null
  await run(async () => {
    try {
      await deleteRequest(`/api/v1/projects/${props.projectId}/git/branches/${encodeURIComponent(branch.name)}`)
      await load()
    } catch (e: any) {
      // §7 delete 실패 — target and the server's protected/in-use reason are
      // kept structurally (not just folded into the generic `error` line) so
      // the branch-manager surfaces which branch and why, not just "실패했다".
      const apiError = e?.response?.data?.error
      deleteResult.value = { branch: branch.name, code: apiError?.code, message: apiError?.message }
      throw e
    }
  })
}
async function confirmMerge() {
  const source = mergeSource.value
  const target = mergeTarget.value
  if (!source || !target || source === target || busy.value) return
  // §3.4 — one click both merges and pushes; the confirm names source, target
  // AND that a push is included, so nothing here is a surprise afterward.
  const ok = await confirm({
    title: t('main.git_branch_manager.merge_confirm_title'),
    message: t('main.git_branch_manager.merge_confirm_message', { source, target }),
    danger: true,
    confirmLabel: t('main.git_branch_manager.merge_btn'),
  })
  if (!ok) return
  mergeResult.value = null
  deleteResult.value = null
  await run(async () => {
    try {
      await postRequest(`/api/v1/projects/${props.projectId}/git/branches/merge`, { source_branch: source, target_branch: target })
      mergeResult.value = { source, target }
      await load()
    } catch (e: any) {
      // §7 merge 실패 — source/target stay attached to EVERY failure (not just
      // conflict), plus the server's code/message and whether this was
      // specifically a push failure, so a diverged-target or push rejection
      // is never flattened into the same one-line "Merge failed" as a conflict.
      const apiError = e?.response?.data?.error
      mergeResult.value = {
        source, target,
        code: apiError?.code,
        message: apiError?.message,
        files: apiError?.code === 'branch_merge_conflict' ? (apiError.details?.conflict_files || []) : undefined,
        pushFailed: apiError?.code === 'branch_merge_push_failed',
      }
      throw e
    }
  })
}
// §7 — a failed refresh keeps whatever catalog was last loaded successfully
// (load() never rejects; it records the failure in `error` instead), so a
// transient read failure never blanks a list that was already showing.
function loadQuietly() { void load() }
watch(() => props.projectId, loadQuietly)
onMounted(loadQuietly)
</script>

<style scoped>
/* T0018 — v0.2 density: zones are separated by spacing and a hairline, not by
   nested boxes; only the two action forms get a soft surface, and only the danger
   zone gets a colored edge. */
.branch-manager { display: grid; gap: 18px; }
.branch-manager-hd { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.branch-summary { display: flex; gap: 20px; flex-wrap: wrap; padding: 8px 10px; border-radius: var(--r); background: var(--surface-h); }
.branch-summary-item { display: flex; flex-direction: column; gap: 2px; }
.branch-summary-label { font-size: .72rem; color: var(--text-m); }
.branch-summary-value { font-weight: 700; }
.branch-summary-value--mono { font-family: monospace; }
.branch-zone { display: grid; gap: 8px; min-width: 0; }
.branch-zone-title { margin: 0; font-size: .72rem; font-weight: 700; letter-spacing: .03em; color: var(--text-s); }
.branch-zone-hint { margin: 0; font-size: .78rem; color: var(--text-s); }
.branch-list { display: grid; }
.branch-row { display: flex; align-items: center; gap: 8px; min-width: 0; padding: 7px 2px; border-bottom: 1px solid var(--border); font-size: .82rem; line-height: 1.4; }
.branch-row:last-child { border-bottom: none; }
.branch-name { font-family: inherit; font-size: inherit; font-weight: 600; line-height: inherit; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.branch-meta { color: var(--text-m); font-size: .75rem; font-weight: 400; }
.branch-spacer { flex: 1 1 auto; }
.badge-muted { background: var(--surface-h); color: var(--text-m); }
.branch-action-card { padding: 12px; border-radius: var(--r-lg); background: var(--surface-h); }
.branch-form-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto; align-items: end; gap: 10px; }
.branch-form-row { display: flex; align-items: center; gap: 8px; }
.branch-field { display: grid; gap: 4px; min-width: 0; }
.branch-field-label { color: var(--text-s); font-size: .78rem; font-weight: 600; line-height: 1.35; }
.branch-field small { color: var(--text-m); font-size: .72rem; line-height: 1.35; }
.branch-control { box-sizing: border-box; width: 100%; min-width: 0; height: 32px; padding: 0 10px; border: 1px solid var(--border); border-radius: var(--r); background: var(--surface); color: var(--text); font: inherit; font-size: .82rem; line-height: 1.35; }
.branch-control:focus { border-color: var(--primary); outline: 2px solid var(--primary-l); outline-offset: 0; }
.branch-form-row .branch-control { flex: 1; }
.branch-create-btn { min-height: 32px; }
.branch-form-actions { display: flex; justify-content: flex-end; }
.branch-merge-summary { margin: 0; font-size: .82rem; color: var(--text-s); }
.branch-empty-state { margin: 0; padding: 10px; border-radius: var(--r); background: var(--surface); color: var(--text-s); font-size: .82rem; line-height: 1.4; }
.branch-zone--danger { padding: 12px; border: 1px solid var(--danger-l); border-left: 3px solid var(--danger); border-radius: var(--r-lg); }
.branch-zone--danger .branch-zone-title { color: var(--danger); }
.branch-result { display: flex; gap: 8px; flex-wrap: wrap; padding: 9px; border-radius: var(--r); background: var(--success-l); color: var(--success); }
.branch-result--error, .branch-error { background: var(--danger-l); color: var(--danger); }
.branch-error { margin: 0; padding: 8px 10px; border-radius: var(--r); }
.branch-result ul { width: 100%; margin: 0; }
</style>
