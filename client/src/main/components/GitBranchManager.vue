<template>
  <section class="branch-manager" data-test="branch-manager">
    <header>
      <strong><AppIcon name="git-branch" /> Branch Manager</strong>
      <button class="btn btn-sm btn-secondary" :disabled="busy" @click="load">Refresh</button>
    </header>
    <p v-if="error" class="branch-error">{{ error }}</p>
    <div class="branch-list">
      <div v-for="branch in catalog.branches" :key="branch.name" class="branch-row">
        <span class="branch-name">{{ branch.name }}</span>
        <span class="badge">{{ kindLabel(branch) }}</span>
        <span v-if="branch.connected_group_id" class="branch-meta">{{ branch.connected_group_id }}</span>
        <button
          v-if="branch.kind !== 'remote_only'"
          class="btn btn-sm btn-danger"
          :disabled="busy || !branch.can_delete"
          :title="branch.delete_blocked_reason || undefined"
          @click="remove(branch)"
        >Delete</button>
      </div>
    </div>
    <div class="branch-actions">
      <form @submit.prevent="create">
        <strong>Create branch</strong>
        <input v-model.trim="newName" aria-label="New branch name" placeholder="feature/name" />
        <select v-model="createSource" aria-label="Create source">
          <option v-for="branch in createCandidates" :key="branch.name" :value="branch.name">{{ branch.name }}</option>
        </select>
        <button class="btn btn-sm btn-primary" :disabled="busy || !newName || !createSource">Create</button>
      </form>
      <form @submit.prevent="merge">
        <strong>Merge branches</strong>
        <select v-model="mergeSource" aria-label="Merge source">
          <option v-for="branch in sourceCandidates" :key="branch.name" :value="branch.name">{{ branch.name }}</option>
        </select>
        <span aria-hidden="true">→</span>
        <select v-model="mergeTarget" aria-label="Merge target">
          <option v-for="branch in targetCandidates" :key="branch.name" :value="branch.name">{{ branch.name }}</option>
        </select>
        <button class="btn btn-sm btn-primary" :disabled="busy || !mergeSource || !mergeTarget">Merge</button>
      </form>
    </div>
    <div v-if="mergeResult" class="branch-result" :class="{ conflict: mergeResult.code === 'branch_merge_conflict' }">
      <strong>{{ mergeResult.code === 'branch_merge_conflict' ? 'Merge failed' : 'Merge complete' }}</strong>
      <span>{{ mergeResult.source }} → {{ mergeResult.target }}</span>
      <span v-if="mergeResult.code">{{ mergeResult.code }}</span>
      <ul v-if="mergeResult.files?.length"><li v-for="file in mergeResult.files" :key="file">{{ file }}</li></ul>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { deleteRequest, getRequest, postRequest } from '@shared/api'
import AppIcon from '@shared/AppIcon.vue'

interface BranchRow {
  name: string
  kind: 'local' | 'base' | 'internal_slot' | 'remote_only'
  can_delete: boolean
  delete_blocked_reason?: string | null
  can_be_create_source: boolean
  connected_group_id?: string
}
const props = defineProps<{ projectId: string }>()
const catalog = ref<{ base_branch: string | null; branches: BranchRow[] }>({ base_branch: null, branches: [] })
const busy = ref(false)
const error = ref('')
const newName = ref('')
const createSource = ref('')
const mergeSource = ref('')
const mergeTarget = ref('')
const mergeResult = ref<{ source: string; target: string; code?: string; files?: string[] } | null>(null)
const ordinary = computed(() => (catalog.value.branches || []).filter(b => b.kind === 'local' || b.kind === 'base'))
const createCandidates = computed(() => (catalog.value.branches || []).filter(b => b.can_be_create_source && b.kind !== 'remote_only'))
const sourceCandidates = computed(() => ordinary.value.filter(b => b.name !== mergeTarget.value))
const targetCandidates = computed(() => ordinary.value.filter(b => b.name !== mergeSource.value))
function kindLabel(branch: BranchRow) {
  return branch.kind === 'remote_only' ? 'Remote only · read-only'
    : branch.kind === 'internal_slot' ? 'Internal · in use'
      : branch.kind === 'base' ? 'Base' : 'Local'
}
function syncSelections() {
  const first = ordinary.value[0]?.name || ''
  if (!createCandidates.value.some(b => b.name === createSource.value)) createSource.value = catalog.value.base_branch || first
  if (!ordinary.value.some(b => b.name === mergeSource.value)) mergeSource.value = ordinary.value.find(b => b.name !== catalog.value.base_branch)?.name || first
  if (!targetCandidates.value.some(b => b.name === mergeTarget.value)) mergeTarget.value = catalog.value.base_branch || targetCandidates.value[0]?.name || ''
}
async function load() {
  if (!props.projectId) return
  error.value = ''
  const { data } = await getRequest<any>(`/api/v1/projects/${props.projectId}/git/branches`)
  catalog.value = { base_branch: data?.base_branch || null, branches: Array.isArray(data?.branches) ? data.branches : [] }
  syncSelections()
}
async function run(fn: () => Promise<void>) {
  busy.value = true
  error.value = ''
  try { await fn() } catch (e: any) { error.value = e?.response?.data?.error?.message || 'Git operation failed' } finally { busy.value = false }
}
async function create() {
  await run(async () => {
    await postRequest(`/api/v1/projects/${props.projectId}/git/branches`, { name: newName.value, source_branch: createSource.value })
    newName.value = ''
    await load()
  })
}
async function remove(branch: BranchRow) {
  if (!branch.can_delete) return
  await run(async () => {
    await deleteRequest(`/api/v1/projects/${props.projectId}/git/branches/${encodeURIComponent(branch.name)}`)
    await load()
  })
}
async function merge() {
  const source = mergeSource.value
  const target = mergeTarget.value
  if (!source || !target || source === target) return
  mergeResult.value = null
  await run(async () => {
    try {
      await postRequest(`/api/v1/projects/${props.projectId}/git/branches/merge`, { source_branch: source, target_branch: target })
      mergeResult.value = { source, target }
      await load()
    } catch (e: any) {
      const apiError = e?.response?.data?.error
      if (apiError?.code === 'branch_merge_conflict') {
        mergeResult.value = { source, target, code: apiError.code, files: apiError.details?.conflict_files || [] }
      }
      throw e
    }
  })
}
function loadQuietly() { void load().catch(() => { catalog.value = { base_branch: null, branches: [] } }) }
watch(() => props.projectId, loadQuietly)
onMounted(loadQuietly)
</script>

<style scoped>
.branch-manager{border-top:1px solid var(--border);padding:14px 16px;display:grid;gap:12px}.branch-manager header,.branch-row,.branch-actions form{display:flex;align-items:center;gap:8px}.branch-manager header{justify-content:space-between}.branch-list{display:grid;gap:6px}.branch-row{padding:7px 9px;border:1px solid var(--border);border-radius:7px}.branch-name{font-family:monospace;font-weight:700}.branch-meta{color:var(--text-m);font-size:.75rem}.branch-row button{margin-left:auto}.branch-actions{display:grid;grid-template-columns:1fr 1fr;gap:10px}.branch-actions form{flex-wrap:wrap;border:1px solid var(--border);border-radius:7px;padding:10px}.branch-actions form strong{width:100%}.branch-actions input,.branch-actions select{min-width:0;flex:1}.branch-result{display:flex;gap:8px;flex-wrap:wrap;padding:9px;border-radius:7px;background:#ecfdf5}.branch-result.conflict,.branch-error{color:#b91c1c;background:#fef2f2}.branch-result ul{width:100%;margin:0}@media(max-width:800px){.branch-actions{grid-template-columns:1fr}}
</style>
