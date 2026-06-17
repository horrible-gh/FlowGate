<template>
  <div class="modal-bg" role="dialog" aria-modal="true">
    <div class="modal-box">
      <div class="modal-hd">
        <span class="modal-title"><i class="fa-solid fa-pen" style="color:var(--primary);"></i> {{ $t('settings.users.user_edit_modal.modal_title_5') }}</span>
        <button class="modal-close" type="button" @click="$emit('close')"><i class="fa-solid fa-xmark"></i></button>
      </div>
      <div class="modal-bd">
        <div class="form-row">
          <div class="form-group">
            <label class="form-label req">{{ $t('settings.users.user_edit_modal.label_11') }}</label>
            <input type="text" class="form-ctrl" v-model="form.display_name">
          </div>
          <div class="form-group">
            <label class="form-label req">{{ $t('auth.login.username') }}</label>
            <input type="text" class="form-ctrl" v-model="form.username">
          </div>
        </div>
        <div class="form-group">
          <label class="form-label req">{{ $t('settings.users.user_edit_modal.label_20') }}</label>
          <input type="email" class="form-ctrl" v-model="form.email">
        </div>
        <div class="form-row">
          <div class="form-group">
            <label class="form-label req">{{ $t('settings.users.user_edit_modal.label_25') }}</label>
            <select class="form-ctrl" :value="form.roles[0] || 'worker'" @change="e => form.roles = [e.target.value]">
              <option value="admin">{{ $t('settings.users.role_admin') }}</option>
              <option value="manager">{{ $t('settings.users.role_manager') }}</option>
              <option value="worker">{{ $t('settings.users.role_worker') }}</option>
              <option value="viewer">{{ $t('settings.users.role_viewer') }}</option>
            </select>
          </div>
          <div class="form-group">
            <label class="form-label">{{ $t('settings.users.status_filter') }}</label>
            <select class="form-ctrl" v-model="form.is_active">
              <option :value="true">{{ $t('projects.filter_active') }}</option>
              <option :value="false">{{ $t('common.inactive') }}</option>
            </select>
          </div>
        </div>
        <div class="form-group">
          <label class="form-label">{{ $t('settings.users.user_edit_modal.label_42') }}</label>
          <div style="display:flex;flex-wrap:wrap;gap:8px;padding:10px 12px;border:1px solid var(--border);border-radius:var(--r);background:var(--bg);">
            <template v-if="projects.length">
              <label v-for="p in projects" :key="p.project_id" style="display:flex;align-items:center;gap:6px;font-size:.8125rem;cursor:pointer;">
                <input type="checkbox"
                  :checked="projectRoles.some(r => r.project_id === p.project_id || r.project_name === p.project_name)"
                  @change="e => onProjectCheck(p, e.target.checked)"> {{ p.project_name }}
              </label>
            </template>
            <template v-else>
              <label style="display:flex;align-items:center;gap:6px;font-size:.8125rem;cursor:pointer;"><input type="checkbox"> FlowGate</label>
              <label style="display:flex;align-items:center;gap:6px;font-size:.8125rem;cursor:pointer;"><input type="checkbox"> Chorus</label>
              <label style="display:flex;align-items:center;gap:6px;font-size:.8125rem;cursor:pointer;"><input type="checkbox"> FileForge</label>
            </template>
          </div>
        </div>
        <hr class="divider">
        <div class="form-group">
          <label class="form-label">{{ $t('settings.users.user_edit_modal.label_60') }}</label>
          <input type="password" class="form-ctrl" v-model="form.new_password" :placeholder="$t('settings.users.user_edit_modal.placeholder_61')">
        </div>
      </div>
      <div class="modal-ft">
        <button class="btn btn-secondary" type="button" @click="$emit('close')">{{ $t('common.cancel') }}</button>
        <button class="btn btn-primary" type="button" :disabled="submitting" @click="save">
          <i class="fa-solid fa-floppy-disk"></i> {{ $t('common.save') }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue';
import { getRequest, patchRequest, postRequest, deleteRequest } from '@shared/api';

const props = defineProps({ user: Object });
const emit = defineEmits(['close', 'updated']);

const projectRoles = ref([]);
const projects = ref([]);
const submitting = ref(false);

const form = ref({
  username: props.user.username,
  display_name: props.user.display_name || '',
  email: props.user.email,
  roles: props.user.roles ? [...props.user.roles] : ['worker'],
  is_active: props.user.is_active ?? true,
  new_password: '',
});

onMounted(async () => {
  const [prData, pData] = await Promise.all([
    getRequest(`/api/v1/users/${props.user.id}/project-roles`),
    getRequest('/api/v1/projects'),
  ]);
  projectRoles.value = prData.data || [];
  projects.value = pData.data || [];
});

async function save() {
  submitting.value = true;
  try {
    const payload = {
      username: form.value.username,
      display_name: form.value.display_name,
      email: form.value.email,
      roles: form.value.roles,
      is_active: form.value.is_active,
    };
    if (form.value.new_password) payload.password = form.value.new_password;
    await patchRequest(`/api/v1/users/${props.user.id}`, payload);
    emit('updated');
    emit('close');
  } finally {
    submitting.value = false;
  }
}

async function onProjectCheck(project, checked) {
  if (checked) {
    const { data } = await postRequest(`/api/v1/users/${props.user.id}/project-roles`, { project_id: project.project_id, role: 'worker' });
    projectRoles.value.push({ ...data.data, project_name: project.project_name });
  } else {
    const pr = projectRoles.value.find(r => r.project_id === project.project_id || r.project_name === project.project_name);
    if (pr) {
      await deleteRequest(`/api/v1/users/${props.user.id}/project-roles/${pr.id}`);
      projectRoles.value = projectRoles.value.filter(r => r.id !== pr.id);
    }
  }
}
</script>
