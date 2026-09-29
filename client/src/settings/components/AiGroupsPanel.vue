<template>
  <section class="card mb-4" data-test="ai-groups-panel">
    <div class="card-hd">
      <span class="card-title">{{ t('settings.project.ai_v02.groups') }}</span>
      <button class="btn btn-secondary btn-sm" style="margin-left:auto" @click="openCreate">{{ t('settings.project.ai_v02.add_group') }}</button>
    </div>
    <div class="card-bd pad">
      <p v-if="error" class="alert alert-danger">{{ error }}</p>
      <p v-if="!groups.length">{{ t('settings.project.ai_v02.no_groups') }}</p>
      <div v-for="group in groups" :key="group.ai_group_id" class="ai-group-row">
        <div><strong>{{ group.name }}</strong><p v-if="group.description">{{ group.description }}</p>
          <div v-for="role in roleNames" :key="role" class="ai-group-role">
            {{ t(`settings.project.ai_v02.role_${role}`) }}:
            <template v-if="group[role]">{{ group[role]?.provider_name || group[role]?.provider_id }}
              <span v-if="!group[role]?.is_available" class="badge badge-yellow">{{ t('settings.project.ai_v02.unavailable') }}</span>
            </template>
            <span v-else>{{ t('settings.project.ai_v02.none') }}</span>
          </div>
        </div>
        <div class="ai-group-actions">
          <button class="btn btn-secondary btn-sm" @click="openEdit(group)">{{ t('settings.project.ai_v02.edit') }}</button>
          <button class="btn btn-secondary btn-sm" @click="remove(group)">{{ t('settings.project.ai_v02.delete') }}</button>
        </div>
      </div>
    </div>
  </section>
  <div v-if="editing" class="ai-group-backdrop" @click.self="closeModal">
    <div class="ai-group-modal" role="dialog" aria-modal="true" :aria-label="t('settings.project.ai_v02.group_form')">
      <h2>{{ t('settings.project.ai_v02.group_form') }}</h2>
      <p v-if="modalError" class="alert alert-danger">{{ modalError }}</p>
      <label class="form-label">{{ t('settings.project.ai_v02.name') }}</label>
      <input v-model="form.name" class="form-ctrl" maxlength="200" />
      <label class="form-label">{{ t('settings.project.ai_v02.description') }}</label>
      <textarea v-model="form.description" class="form-ctrl" maxlength="2000" />
      <label v-for="role in roleNames" :key="role" class="form-label ai-group-field">
        {{ t(`settings.project.ai_v02.role_${role}`) }}
        <select v-model="form[role]" class="form-ctrl">
          <option v-if="role !== 'work_executor'" value="">{{ t('settings.project.ai_v02.none') }}</option>
          <option v-if="form[role] && !providers.some(p => p.id === form[role])" :value="form[role]">{{ form[role] }} — {{ t('settings.project.ai_v02.unavailable') }}</option>
          <option v-for="provider in providers" :key="provider.id" :value="provider.id">{{ provider.name }}</option>
        </select>
      </label>
      <div class="ai-group-actions">
        <button class="btn btn-secondary" @click="closeModal">{{ t('settings.project.ai_v02.close') }}</button>
        <button class="btn btn-primary" :disabled="saving" @click="save">{{ t('common.save') }}</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { deleteRequest, getRequest, postRequest, putRequest } from '@shared/api'
import { confirm } from '../../../main/composables/useDialogStack'

type Role = { provider_id: string; provider_name: string | null; is_available: boolean } | null
type Group = { ai_group_id: string; name: string; description: string | null; work_executor: Role; reviewer: Role; reworker: Role }
type RoleName = 'work_executor' | 'reviewer' | 'reworker'
type Form = { name: string; description: string; work_executor: string; reviewer: string; reworker: string }
const roleNames: RoleName[] = ['work_executor', 'reviewer', 'reworker']
const props = defineProps<{ projectId: string; providers: { id: string; name: string }[] }>()
const emit = defineEmits<{ changed: [] }>()
const { t } = useI18n()
const groups = ref<Group[]>([])
const error = ref('')
const modalError = ref('')
const editing = ref<string | null>(null)
const saving = ref(false)
const form = ref<Form>({ name: '', description: '', work_executor: '', reviewer: '', reworker: '' })
let generation = 0
function apiError(e: any): string { return e?.response?.data?.error?.code || t('settings.project.ai_v02.load_failed') }
async function load(): Promise<void> {
  const project = props.projectId, request = ++generation
  if (!project) return
  try {
    const { data } = await getRequest<{ items: Group[] }>(`/api/v1/projects/${encodeURIComponent(project)}/ai-groups`)
    if (generation === request && props.projectId === project) { groups.value = data.items; error.value = '' }
  } catch (e) { if (generation === request) error.value = apiError(e) }
}
function openCreate(): void {
  form.value = { name: '', description: '', work_executor: props.providers[0]?.id || '', reviewer: '', reworker: '' }
  modalError.value = ''; editing.value = 'new'
}
function openEdit(group: Group): void {
  form.value = { name: group.name, description: group.description || '',
    work_executor: group.work_executor?.provider_id || '', reviewer: group.reviewer?.provider_id || '',
    reworker: group.reworker?.provider_id || '' }
  modalError.value = ''; editing.value = group.ai_group_id
}
function closeModal(): void { editing.value = null }
async function save(): Promise<void> {
  const project = props.projectId, id = editing.value
  if (!project || !id) return
  saving.value = true; modalError.value = ''
  const body = { name: form.value.name, description: form.value.description || null,
    work_executor_provider_id: form.value.work_executor, reviewer_provider_id: form.value.reviewer || null,
    reworker_provider_id: form.value.reworker || null }
  try {
    const base = `/api/v1/projects/${encodeURIComponent(project)}/ai-groups`
    if (id === 'new') await postRequest(base, body)
    else await putRequest(`${base}/${encodeURIComponent(id)}`, body)
    if (props.projectId === project) { closeModal(); await load(); emit('changed') }
  } catch (e) { if (props.projectId === project) modalError.value = apiError(e) }
  finally { saving.value = false }
}
async function remove(group: Group): Promise<void> {
  if (!await confirm({ title: t('settings.project.ai_v02.delete_confirm', { name: group.name }) })) return
  const project = props.projectId
  try {
    await deleteRequest(`/api/v1/projects/${encodeURIComponent(project)}/ai-groups/${encodeURIComponent(group.ai_group_id)}`)
    if (props.projectId === project) { await load(); emit('changed') }
  } catch (e) { if (props.projectId === project) error.value = apiError(e) }
}
watch(() => props.projectId, () => { groups.value = []; editing.value = null; void load() }, { immediate: true })
</script>

<style scoped>
.ai-group-row { display: flex; justify-content: space-between; gap: 16px; border-bottom: 1px solid var(--border); padding: 12px 0; }
.ai-group-role { font-size: .82rem; margin-top: 3px; }
.ai-group-actions { display: flex; gap: 8px; align-items: flex-start; justify-content: flex-end; margin-top: 12px; }
.ai-group-backdrop { position: fixed; inset: 0; z-index: 2000; display: grid; place-items: center; background: #0008; }
.ai-group-modal { width: min(520px, calc(100vw - 32px)); max-height: 90vh; overflow: auto; background: var(--surface); color: var(--text); padding: 24px; border-radius: var(--r); }
.ai-group-field { display: block; margin-top: 12px; }
</style>
