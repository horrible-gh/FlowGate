<template>
  <div>
    <div class="flex justify-between items-center mb-4" style="margin-bottom:20px;">
      <div>
        <h1 class="s-page-title">{{ $t('settings.users.title') }}</h1>
        <p class="s-page-sub" style="margin-bottom:0;">{{ $t('settings.users.users_view.subtitle_6') }}</p>
      </div>
      <button class="btn btn-primary" @click="showCreate = true">
        <i class="fa-solid fa-plus"></i> {{ $t('settings.users.new_user') }}
      </button>
    </div>

    <!-- Filter bar -->
    <div class="card mb-4">
      <div class="card-bd pad" style="padding:12px 16px;">
        <div class="flex items-center gap-2">
          <div style="position:relative; flex:1; max-width:300px;">
            <i class="fa-solid fa-magnifying-glass" style="position:absolute; left:10px; top:50%; transform:translateY(-50%); color:var(--text-m); font-size:.8rem;"></i>
            <input type="text" class="form-ctrl" v-model="search" :placeholder="$t('settings.users.users_view.placeholder_19')" style="padding-left:32px;" @input="fetchUsers">
          </div>
          <select class="form-ctrl" style="width:140px;" v-model="roleFilter" @change="fetchUsers">
            <option value="">{{ $t('settings.users.users_view.option_22') }}</option>
            <option value="admin">{{ $t('settings.users.role_admin') }}</option>
            <option value="manager">{{ $t('settings.users.role_manager') }}</option>
            <option value="worker">{{ $t('settings.users.role_worker') }}</option>
            <option value="viewer">{{ $t('settings.users.role_viewer') }}</option>
          </select>
          <select class="form-ctrl" style="width:160px;" v-model="projectFilter">
            <option value="">{{ $t('settings.users.users_view.option_29') }}</option>
            <option value="FlowGate">FlowGate</option>
            <option value="Chorus">Chorus</option>
          </select>
          <select class="form-ctrl" style="width:120px;" v-model="statusFilter" @change="fetchUsers">
            <option value="">{{ $t('settings.users.users_view.option_34') }}</option>
            <option value="active">{{ $t('projects.filter_active') }}</option>
            <option value="inactive">{{ $t('common.inactive') }}</option>
          </select>
        </div>
      </div>
    </div>

    <!-- User table -->
    <div class="card">
      <div class="card-bd">
        <table class="tbl">
          <thead>
            <tr>
              <th style="width:36px;"><input type="checkbox" style="cursor:pointer;"></th>
              <th>{{ $t('settings.users.users_view.table_header_49') }}</th>
              <th>{{ $t('auth.login.username') }}</th>
              <th>{{ $t('settings.users.users_view.table_header_51') }}</th>
              <th>{{ $t('settings.nav.project') }}</th>
              <th>{{ $t('settings.users.status_filter') }}</th>
              <th>{{ $t('settings.users.status_filter') }}</th>
              <th>{{ $t('settings.users.users_view.table_header_55') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="u in filteredUsers" :key="u.id" :style="!u.is_active ? 'opacity:.6' : null">
              <td><input type="checkbox" style="cursor:pointer;"></td>
              <td>
                <div class="flex items-center gap-2">
                  <div :style="`width:28px;height:28px;background:${avatarColor(u)};border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:.65rem;font-weight:700;color:white;flex-shrink:0;`">
                    {{ (u.display_name || u.username || '?')[0].toUpperCase() }}
                  </div>
                  <div>
                    <div class="fw-5">{{ u.display_name || u.username }}</div>
                    <div class="text-xs text-m">{{ u.email }}</div>
                  </div>
                </div>
              </td>
              <td><span class="mono" style="font-size:.8rem;">{{ u.username }}</span></td>
              <td>
                <span v-if="u.roles?.includes('admin')" class="badge badge-red"><i class="fa-solid fa-crown"></i> {{ $t('settings.users.role_admin') }}</span>
                <span v-else-if="u.roles?.includes('manager')" class="badge badge-info"><i class="fa-solid fa-robot"></i> {{ $t('settings.users.role_manager') }}</span>
                <span v-else-if="u.roles?.includes('worker')" class="badge badge-gray"><i class="fa-solid fa-eye"></i> {{ $t('settings.users.role_worker') }}</span>
                <span v-else-if="u.roles?.includes('viewer')" class="badge badge-gray"><i class="fa-solid fa-eye"></i> {{ $t('settings.users.role_viewer') }}</span>
                <span v-else class="badge badge-gray">—</span>
              </td>
              <td>
                <div v-if="u.projects?.length" class="flex gap-1" style="flex-wrap:wrap;">
                  <span v-for="p in u.projects" :key="p" class="badge badge-blue">{{ p }}</span>
                </div>
                <span v-else class="text-xs text-m">—</span>
              </td>
              <td>
                <span v-if="u.totp_enabled" class="badge badge-green"><i class="fa-solid fa-check"></i> {{ $t('projects.filter_active') }}</span>
                <span v-else-if="u.is_active" class="badge badge-yellow"><i class="fa-solid fa-minus"></i> {{ $t('settings.users.totp_unset') }}</span>
                <span v-else class="badge badge-gray">—</span>
              </td>
              <td>
                <span v-if="u.locked_until" class="badge badge-red">{{ $t('settings.users.locked') }}</span>
                <span v-else-if="u.is_active" class="badge badge-green">{{ $t('projects.filter_active') }}</span>
                <span v-else class="badge badge-red">{{ $t('common.inactive') }}</span>
              </td>
              <td>
                <div class="tbl-actions">
                  <button class="btn btn-secondary btn-sm" @click="openEdit(u)"><i class="fa-solid fa-pen"></i></button>
                  <button
                    class="btn btn-ghost btn-sm"
                    :style="u.roles?.includes('admin') ? 'color:var(--text-m);' : 'color:var(--danger);'"
                    @click="handleTrash(u)"
                  ><i class="fa-solid fa-trash"></i></button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- RBAC note -->
    <div class="alert alert-warning mt-4" style="margin-top:16px;">
      <i class="fa-solid fa-triangle-exclamation"></i>
      <div>
        <strong>{{ $t('settings.users.users_view.text_116') }}</strong> Detailed definitions and mapping of permission items will be decided during the design phase (DS→D) after the database schema design is finalized. (R014 §3, §10 unresolved item No.2)
      </div>
    </div>

    <!-- Create modal -->
    <Transition name="modal-fade">
      <UserCreateModal v-if="showCreate" @close="showCreate = false" @created="onCreated" />
    </Transition>

    <!-- Edit modal -->
    <Transition name="modal-fade">
      <UserEditModal v-if="editingUser" :user="editingUser" @close="editingUser = null" @updated="fetchUsers" />
    </Transition>

    <!-- Activate/deactivate confirmation (shared ConfirmModal, no native confirm()) -->
    <ConfirmModal
      v-model:visible="confirmVisible"
      :title="confirmTitle"
      :message="confirmMessage"
      :confirm-label="confirmLabel || undefined"
      :danger="confirmDanger"
      @confirm="onConfirmAccept"
    />
  </div>
</template>

<script setup>
import { computed, ref, onMounted } from 'vue';
import { useI18n } from 'vue-i18n';
import { getRequest, patchRequest, postRequest } from '@shared/api';
import ConfirmModal from '@main/components/ConfirmModal.vue';
import { useToast } from '@main/components/common/useToast';
import { useAuthStore } from '../../stores/auth.js';
import UserCreateModal from './UserCreateModal.vue';
import UserEditModal from './UserEditModal.vue';

const { t } = useI18n();
const { showToast } = useToast();
const auth = useAuthStore();

// Custom confirm dialog state (replaces native confirm())
const confirmVisible = ref(false);
const confirmTitle = ref('');
const confirmMessage = ref('');
const confirmLabel = ref('');
const confirmDanger = ref(false);
let confirmAction = null;
function askConfirm(opts) {
  confirmTitle.value = opts.title;
  confirmMessage.value = opts.message;
  confirmLabel.value = opts.confirmLabel || '';
  confirmDanger.value = !!opts.danger;
  confirmAction = opts.action;
  confirmVisible.value = true;
}
function onConfirmAccept() {
  const action = confirmAction;
  confirmAction = null;
  if (action) action();
}
const users = ref([]);
const search = ref('');
const roleFilter = ref('');
const statusFilter = ref('');
const projectFilter = ref('');
const showCreate = ref(false);
const editingUser = ref(null);
const avatarColors = ['#2563eb', '#7c3aed', '#0891b2', '#94a3b8', '#16a34a'];

const filteredUsers = computed(() => {
  if (!projectFilter.value) return users.value;
  return users.value.filter((user) => user.projects?.includes(projectFilter.value));
});

async function fetchUsers() {
  const params = {
    search: search.value || undefined,
    role: roleFilter.value || undefined,
    is_active: statusFilter.value === 'active' ? true : statusFilter.value === 'inactive' ? false : undefined,
    per_page: 100,
  };
  const { data } = await getRequest('/api/v1/users', params);
  users.value = data.data?.items || [];
}

function openEdit(u) { editingUser.value = u; }
function onCreated() { showCreate.value = false; fetchUsers(); }

function avatarColor(user) {
  const index = users.value.findIndex((item) => item.id === user.id);
  return avatarColors[Math.max(index, 0) % avatarColors.length];
}

function handleTrash(u) {
  if (u.roles?.includes('admin')) {
    showToast(t('settings.users.users_view.alert_171'), 'warning');
    return;
  }
  if (u.locked_until) {
    unlockUser(u);
    return;
  }
  if (u.is_active) {
    askConfirm({
      title: t('settings.users.users_view.deactivate_confirm_title'),
      message: t('settings.users.users_view.confirm_176'),
      confirmLabel: t('common.confirm'),
      danger: true,
      action: () => setUserActive(u, false),
    });
  } else {
    askConfirm({
      title: t('settings.users.users_view.activate_confirm_title'),
      message: t('settings.users.users_view.confirm_181'),
      confirmLabel: t('common.confirm'),
      action: () => setUserActive(u, true),
    });
  }
}

async function unlockUser(u) {
  await postRequest(`/api/v1/users/${u.id}/unlock`);
  fetchUsers();
}

async function setUserActive(u, isActive) {
  await patchRequest(`/api/v1/users/${u.id}`, { is_active: isActive });
  fetchUsers();
}

onMounted(fetchUsers);
</script>

<style scoped>
.modal-fade-enter-active,
.modal-fade-leave-active {
  transition: opacity 0.15s ease;
}
.modal-fade-enter-from,
.modal-fade-leave-to {
  opacity: 0;
}
</style>
