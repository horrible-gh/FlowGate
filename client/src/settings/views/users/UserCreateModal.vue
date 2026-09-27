<template>
  <DialogShell :open="true" variant="form" surface-class="dialog-user-create-modal"  @request-close="$emit('close')">
    <template #header>
      <DialogHeader title="" :closeable="true" @close="$emit('close')">
        <template #title><AppIcon name="user-plus" style="color:var(--primary);" /> {{ $t('settings.users.new_user') }}</template>
      </DialogHeader>
    </template>

      
      <div class="dialog-feature-body">
        <div class="form-group">
          <label class="form-label req">{{ $t('auth.login.username') }}</label>
          <input type="text" class="form-ctrl" v-model="form.username" placeholder="username">
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
            <select class="form-ctrl" v-model="form.role_id">
              <option value="role_admin">{{ $t('settings.users.role_admin') }}</option>
              <option value="role_manager">{{ $t('settings.users.role_manager') }}</option>
              <option value="role_worker">{{ $t('settings.users.role_worker') }}</option>
              <option value="role_viewer">{{ $t('settings.users.role_viewer') }}</option>
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
            <span v-else class="text-xs text-m">—</span>
          </div>
        </div>
        <div v-if="errorMsg" class="alert alert-danger" style="margin-top:12px;">{{ errorMsg }}</div>
      </div>
      
    

    <template #footer>
      <DialogFooter :actions="[
        { id: 'action-0', role: 'cancel', label: $t('common.cancel'), onSelect: () => { $emit('close') } },
        { id: 'submit-1', role: 'primary', label: $t('common.add'), onSelect: () => submit(), disabled: submitting }
      ]">
        <template #action-action-0>{{ $t('common.cancel') }}</template>
        <template #action-submit-1>
          <AppIcon name="plus" /> {{ $t('common.add') }}
        </template>
      </DialogFooter>
    </template>
  </DialogShell>
</template>

<script setup>
import DialogShell from '../../../main/components/dialogs/DialogShell.vue'
import DialogHeader from '../../../main/components/dialogs/DialogHeader.vue'
import DialogFooter from '../../../main/components/dialogs/DialogFooter.vue'
import { ref, onMounted } from 'vue';
import { useI18n } from 'vue-i18n';
import { postRequest, getRequest } from '@shared/api';
import { resolveApiError } from '@shared/apiErrors';
import AppIcon from '@shared/AppIcon.vue';

const emit = defineEmits(['close', 'created']);
const { t } = useI18n();

const projects = ref([]);
const submitting = ref(false);
const errorMsg = ref('');
const passwordConfirm = ref('');
const form = ref({
  username: '', email: '', password: '',
  role_id: 'role_manager', is_active: true, project_roles: [],
});

onMounted(async () => {
  const { data } = await getRequest('/api/v1/projects');
  projects.value = (data.projects || []).filter(p => p.project_id !== '__SYSTEM__');
});

function toggleProject(projectId, checked) {
  if (checked) {
    if (!form.value.project_roles.some(r => r.project_id === projectId)) {
      form.value.project_roles.push({ project_id: projectId, role_id: 'role_worker' });
    }
  } else {
    form.value.project_roles = form.value.project_roles.filter(r => r.project_id !== projectId);
  }
}

async function submit() {
  if (!form.value.role_id) { errorMsg.value = t('settings.users.user_create_modal.error_110'); return; }
  if (form.value.password !== passwordConfirm.value) { errorMsg.value = t('settings.users.user_create_modal.error_111'); return; }
  submitting.value = true;
  errorMsg.value = '';
  try {
    const { data } = await postRequest('/api/v1/users', {
      username: form.value.username,
      email: form.value.email,
      password: form.value.password,
      is_active: form.value.is_active,
      role_id: form.value.role_id,
      project_roles: form.value.project_roles,
    });
    if (!data.user_id) throw new Error('Missing user_id in user creation response');
    emit('created');
  } catch (e) {
    errorMsg.value = resolveApiError(e, t, 'settings.users.user_create_modal.error_124');
  } finally {
    submitting.value = false;
  }
}
</script>
