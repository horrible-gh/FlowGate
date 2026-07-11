<template>
  <div class="modal-bg" role="dialog" aria-modal="true">
    <div class="modal-box">
      <div class="modal-hd">
        <span class="modal-title"><AppIcon name="user-plus" style="color:var(--primary);" /> {{ $t('settings.users.new_user') }}</span>
        <button class="modal-close" type="button" @click="$emit('close')"><AppIcon name="x" /></button>
      </div>
      <div class="modal-bd">
        <div class="form-row">
          <div class="form-group">
            <label class="form-label req">{{ $t('settings.users.user_create_modal.label_11') }}</label>
            <input type="text" class="form-ctrl" v-model="form.display_name" :placeholder="$t('settings.users.user_create_modal.placeholder_12')">
          </div>
          <div class="form-group">
            <label class="form-label req">{{ $t('auth.login.username') }}</label>
            <input type="text" class="form-ctrl" v-model="form.username" placeholder="username">
          </div>
        </div>
        <div class="form-group">
          <label class="form-label req">{{ $t('settings.users.user_create_modal.label_20') }}</label>
          <input type="email" class="form-ctrl" v-model="form.email" placeholder="user@flowgate.local">
        </div>
        <div class="form-row">
          <div class="form-group">
            <label class="form-label req">{{ $t('auth.login.password') }}</label>
            <input type="password" class="form-ctrl" v-model="form.password" placeholder="••••••••">
          </div>
          <div class="form-group">
            <label class="form-label req">{{ $t('auth.password.confirm_label') }}</label>
            <input type="password" class="form-ctrl" v-model="passwordConfirm" placeholder="••••••••">
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label class="form-label req">{{ $t('settings.users.user_create_modal.label_35') }}</label>
            <select class="form-ctrl" :value="form.roles[0] || 'manager'" @change="e => form.roles = [e.target.value]">
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
          <label class="form-label">{{ $t('settings.users.user_create_modal.label_52') }}</label>
          <div style="display:flex; flex-wrap:wrap; gap:8px; padding:10px 12px; border:1px solid var(--border); border-radius:var(--r); background:var(--bg);">
            <template v-if="projects.length">
              <label v-for="p in projects" :key="p.project_id" style="display:flex;align-items:center;gap:6px;font-size:.8125rem;cursor:pointer;">
                <input type="checkbox" :checked="form.project_roles.some(r => r.project_id === p.project_id)"
                  @change="e => toggleProject(p.project_id, e.target.checked)"> {{ p.project_name }}
              </label>
            </template>
            <template v-else>
              <label style="display:flex;align-items:center;gap:6px;font-size:.8125rem;cursor:pointer;"><input type="checkbox"> FlowGate</label>
              <label style="display:flex;align-items:center;gap:6px;font-size:.8125rem;cursor:pointer;"><input type="checkbox"> Chorus</label>
              <label style="display:flex;align-items:center;gap:6px;font-size:.8125rem;cursor:pointer;"><input type="checkbox"> FileForge</label>
            </template>
          </div>
        </div>
        <div v-if="errorMsg" class="alert alert-danger" style="margin-top:12px;">{{ errorMsg }}</div>
      </div>
      <div class="modal-ft">
        <button class="btn btn-secondary" type="button" @click="$emit('close')">{{ $t('common.cancel') }}</button>
        <button class="btn btn-primary" type="button" :disabled="submitting" @click="submit">
          <AppIcon name="plus" /> {{ $t('common.add') }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue';
import { postRequest, getRequest } from '@shared/api';
import AppIcon from '@shared/AppIcon.vue';

const emit = defineEmits(['close', 'created']);

const projects = ref([]);
const submitting = ref(false);
const errorMsg = ref('');
const passwordConfirm = ref('');
const form = ref({
  username: '', display_name: '', email: '', password: '',
  roles: ['manager'], is_active: true, project_roles: [],
});

onMounted(async () => {
  const { data } = await getRequest('/api/v1/projects');
  projects.value = data.data || [];
});

function toggleProject(projectId, checked) {
  if (checked) {
    if (!form.value.project_roles.some(r => r.project_id === projectId)) {
      form.value.project_roles.push({ project_id: projectId, role: 'worker' });
    }
  } else {
    form.value.project_roles = form.value.project_roles.filter(r => r.project_id !== projectId);
  }
}

async function submit() {
  if (!form.value.roles.length) { errorMsg.value = $t('settings.users.user_create_modal.error_110'); return; }
  if (form.value.password !== passwordConfirm.value) { errorMsg.value = $t('settings.users.user_create_modal.error_111'); return; }
  submitting.value = true;
  errorMsg.value = '';
  try {
    const { data } = await postRequest('/api/v1/users', form.value);
    const userId = data.data.id;
    for (const pm of form.value.project_roles) {
      if (pm.project_id) {
        await postRequest(`/api/v1/users/${userId}/project-roles`, pm);
      }
    }
    emit('created');
  } catch (e) {
    errorMsg.value = e.response?.data?.detail || $t('settings.users.user_create_modal.error_124');
  } finally {
    submitting.value = false;
  }
}
</script>
