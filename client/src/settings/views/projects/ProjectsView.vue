<template>
  <div style="max-width:1100px; margin:0 auto;">
    <div class="flex justify-between items-center" style="margin-bottom:24px;">
      <div>
        <h1 class="s-page-title">
          <i class="fa-solid fa-table-cells-large" style="color:var(--primary); margin-right:8px;"></i>
          {{ t('projects.title') }}
        </h1>
        <p class="s-page-sub" style="margin-bottom:0;">{{ t('projects.sub') }}</p>
      </div>
      <button class="btn btn-primary" @click="showModal = true">
        <i class="fa-solid fa-plus"></i> {{ t('projects.new') }}
      </button>
    </div>

    <div style="display:flex; gap:10px; margin-bottom:22px; flex-wrap:wrap;">
      <div style="position:relative; flex:1; max-width:300px;">
        <i
          class="fa-solid fa-magnifying-glass"
          style="position:absolute; left:10px; top:50%; transform:translateY(-50%); color:var(--text-m); font-size:.8rem; pointer-events:none;"
        ></i>
        <input
          v-model="searchQuery"
          type="text"
          class="form-ctrl"
          :placeholder="t('projects.search_placeholder')"
          style="padding-left:32px;"
        >
      </div>
      <div style="display:flex; gap:6px;">
        <button
          class="btn btn-secondary btn-sm proj-filter-btn"
          :class="{ active: statusFilter === 'all' }"
          @click="statusFilter = 'all'"
        >{{ t('projects.filter_all') }} ({{ projects.length }})</button>
        <button
          class="btn btn-secondary btn-sm proj-filter-btn"
          :class="{ active: statusFilter === 'active' }"
          @click="statusFilter = 'active'"
        >{{ t('projects.filter_active') }} ({{ activeCount }})</button>
        <button
          class="btn btn-secondary btn-sm proj-filter-btn"
          :class="{ active: statusFilter === 'archive' }"
          @click="statusFilter = 'archive'"
        >{{ t('projects.filter_archive') }} ({{ archivedCount }})</button>
      </div>
    </div>

    <div style="display:grid; grid-template-columns:repeat(auto-fill, minmax(330px, 1fr)); gap:16px;">
      <div v-if="loading" style="grid-column:1/-1; text-align:center; padding:48px; color:var(--text-m);">
        <i class="fa-solid fa-spinner fa-spin" style="font-size:1.5rem; margin-bottom:12px; display:block;"></i>
        {{ t('common.loading') }}
      </div>

      <template v-else>
        <div
          v-for="p in filteredProjects"
          :key="p.project_id"
          class="proj-card"
          :class="{ 'proj-card-active': p.is_active === 1 }"
          :style="p.is_active === 0 ? 'opacity:.6;' : ''"
        >
          <div class="proj-card-bar" :style="`background:${projectColor(p.project_id)};`"></div>
          <div class="proj-card-body">
            <div class="proj-card-hd">
              <div class="proj-card-icon" :style="`background:${projectColor(p.project_id)};`">
                {{ projectInitials(p.project_name) }}
              </div>
              <div class="proj-card-info">
                <div class="proj-card-name">
                  {{ p.project_name }}
                  <span
                    v-if="p.project_id === settings.currentProjectId"
                    class="badge badge-blue"
                    style="margin-left:6px; font-size:.62rem; vertical-align:middle;"
                  >{{ t('projects.current') }}</span>
                  <span
                    v-if="p.is_active === 0"
                    class="badge badge-gray"
                    style="margin-left:6px; font-size:.62rem; vertical-align:middle;"
                  >{{ t('projects.archived') }}</span>
                </div>
                <div class="proj-card-desc">{{ p.description || '&nbsp;' }}</div>
              </div>
            </div>
            <div class="proj-card-stats">
              <div class="proj-stat">
                <i class="fa-regular fa-clock" style="color:var(--text-m);"></i>
                <span>{{ formatUpdatedAt(p.updated_at) }}</span>
              </div>
            </div>
            <div class="proj-card-ft">
              <button
                v-if="p.is_active === 1"
                class="btn btn-primary btn-sm"
                @click="openProject(p)"
              >
                <i class="fa-solid fa-arrow-right-to-bracket"></i> {{ t('projects.open') }}
              </button>
              <button
                v-else
                class="btn btn-secondary btn-sm"
                @click="openProject(p)"
              >
                <i class="fa-solid fa-box-archive"></i> {{ t('projects.open_readonly') }}
              </button>
              <button
                v-if="p.is_active === 0"
                class="btn btn-ghost btn-sm"
                style="color:var(--primary);"
                @click="showToast(t('projects.toast_restored'), 'success')"
              >
                <i class="fa-solid fa-rotate-left"></i> {{ t('projects.restore') }}
              </button>
              <button
                v-if="p.is_active === 1"
                class="btn btn-ghost btn-sm"
                style="margin-left:auto; color:var(--text-m);"
                :title="t('projects.archive')"
                @click="showToast(t('projects.toast_archived'), 'info')"
              >
                <i class="fa-solid fa-box-archive"></i>
              </button>
            </div>
          </div>
        </div>

        <div class="proj-card proj-card-new" @click="showModal = true">
          <div class="proj-card-new-inner">
            <i class="fa-solid fa-plus"></i>
            <span>{{ t('projects.new_card') }}</span>
          </div>
        </div>
      </template>
    </div>
  </div>

  <div class="modal-bg" :class="{ hidden: !showModal }">
    <div class="modal-box">
      <div class="modal-hd">
        <span class="modal-title">
          <i class="fa-solid fa-plus" style="color:var(--primary);"></i>
          {{ t('projects.modal_title') }}
        </span>
        <button class="modal-close" @click="showModal = false">
          <i class="fa-solid fa-xmark"></i>
        </button>
      </div>
      <div class="modal-bd">
        <div class="form-group">
          <label class="form-label req">{{ t('projects.label_display_name') }}</label>
          <input
            v-model="newProjDisplayName"
            type="text"
            class="form-ctrl"
            :placeholder="t('projects.form_name_placeholder')"
          >
          <p class="form-hint">{{ t('projects.form_name_hint') }}</p>
        </div>
        <div class="form-group">
          <label class="form-label req">{{ t('projects.label_id') }}</label>
          <div style="display:flex; gap:8px;">
            <input
              v-model="newProjSlug"
              @input="onSlugInput"
              type="text"
              class="form-ctrl"
              :class="{ 'input-slug-error': newProjSlug && !slugValid }"
              :placeholder="t('projects.label_id')"
              style="flex:1;"
            >
            <button class="btn btn-secondary btn-sm" type="button" @click="suggestSlug">
              {{ t('projects.button_suggest_id') }}
            </button>
          </div>
          <p v-if="newProjSlug && !slugValid" class="form-slug-error-text">
            {{ t('projects.error_id_format') }}
          </p>
        </div>
        <div class="form-group">
          <label class="form-label">
            {{ t('projects.form_desc') }}
            <span style="color:var(--text-m); font-weight:400;">({{ t('projects.optional') }})</span>
          </label>
          <textarea
            v-model="newProjDesc"
            class="form-ctrl"
            rows="2"
            :placeholder="t('projects.form_desc_placeholder')"
            style="resize:vertical;"
          ></textarea>
        </div>
        <div class="form-row">
          <div class="form-group" style="margin-bottom:0;">
            <label class="form-label req">{{ t('projects.form_color') }}</label>
            <div style="display:flex; gap:8px; flex-wrap:wrap; margin-top:6px;">
              <div
                v-for="c in colorOptions"
                :key="c"
                class="color-opt"
                :style="{
                  width:'28px', height:'28px', borderRadius:'50%',
                  background: c, cursor:'pointer',
                  outline: selectedColor === c ? `2px solid ${c}` : 'none',
                  border: selectedColor === c ? '3px solid white' : 'none',
                }"
                @click="selectedColor = c"
              ></div>
            </div>
          </div>
          <div class="form-group" style="margin-bottom:0;">
            <label class="form-label">{{ t('projects.form_structure') }}</label>
            <select class="form-ctrl">
              <option>{{ t('projects.structure_3') }}</option>
              <option>{{ t('projects.structure_4') }}</option>
              <option>{{ t('projects.structure_1') }}</option>
              <option>{{ t('projects.structure_2') }}</option>
            </select>
          </div>
        </div>
        <hr class="divider">
        <div class="form-row">
          <div class="form-group" style="margin-bottom:0;">
            <label class="form-label">{{ t('projects.form_storage') }}</label>
            <select v-model="storageType" class="form-ctrl">
              <option value="default">{{ t('projects.storage_default') }}</option>
              <option value="custom">{{ t('projects.storage_custom') }}</option>
            </select>
          </div>
          <div class="form-group" style="margin-bottom:0;">
            <label class="form-label">{{ t('projects.form_member') }}</label>
            <select class="form-ctrl">
              <option selected>admin ({{ t('projects.me') }})</option>
            </select>
          </div>
        </div>
        <div
          v-if="storageType === 'custom'"
          class="form-group"
          style="margin-top:12px; margin-bottom:0;"
        >
          <label class="form-label">{{ t('projects.custom_path') }}</label>
          <input type="text" class="form-ctrl" placeholder="/mnt/storage/projects/myproject/">
        </div>
      </div>
      <div class="modal-ft">
        <button class="btn btn-secondary" @click="showModal = false">{{ t('common.cancel') }}</button>
        <button class="btn btn-primary" :disabled="!slugValid" @click="createProject">
          <i class="fa-solid fa-plus"></i> {{ t('projects.create_btn') }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue';
import { useRoute } from 'vue-router';
import { useI18n } from 'vue-i18n';
import { postRequest } from '@shared/api';
import { useToast } from '../../../main/components/common/useToast';
import { useSettingsStore } from '../../stores/settings.js';

const { t } = useI18n();
const route = useRoute();
const settings = useSettingsStore();
const { showToast } = useToast();

const loading = ref(false);
const searchQuery = ref('');
const statusFilter = ref('all');
const showModal = ref(false);
const newProjDisplayName = ref('');
const newProjSlug = ref('');
const newProjDesc = ref('');
const selectedColor = ref('#2563eb');
const storageType = ref('default');

const slugManuallyEdited = ref(false);
let _slugDebounceTimer = null;
let _autoFilling = false;

const slugValid = computed(() => /^[a-z0-9_\-]+$/.test(newProjSlug.value) && newProjSlug.value.trim() !== '');

watch(newProjDisplayName, (val) => {
  if (slugManuallyEdited.value) return;
  if (_slugDebounceTimer) clearTimeout(_slugDebounceTimer);
  const text = (val || '').trim();
  if (!text) {
    _autoFilling = true;
    newProjSlug.value = '';
    _autoFilling = false;
    return;
  }
  _slugDebounceTimer = setTimeout(async () => {
    if (slugManuallyEdited.value) return;
    try {
      const res = await postRequest('/api/v1/slug/romanize', { text });
      const suggested = res.data?.suggested;
      if (suggested && !slugManuallyEdited.value) {
        _autoFilling = true;
        newProjSlug.value = suggested;
        _autoFilling = false;
      }
    } catch { /* Silently ignore auto-fill failure — user can type manually */ }
  }, 400);
});

function onSlugInput() {
  if (_autoFilling) return;
  slugManuallyEdited.value = true;
}

const colorOptions = [
  '#2563eb', '#7c3aed', '#db2777', '#0891b2',
  '#16a34a', '#d97706', '#ea580c', '#64748b',
];

const colorMap = {};
const projects = computed(() => settings.projects);
const activeCount = computed(() => projects.value.filter((p) => p.is_active === 1).length);
const archivedCount = computed(() => projects.value.filter((p) => p.is_active === 0).length);

const filteredProjects = computed(() => {
  let list = projects.value;
  if (statusFilter.value === 'active') list = list.filter((p) => p.is_active === 1);
  else if (statusFilter.value === 'archive') list = list.filter((p) => p.is_active === 0);
  if (searchQuery.value.trim()) {
    const q = searchQuery.value.toLowerCase();
    list = list.filter((p) =>
      p.project_name.toLowerCase().includes(q) ||
      (p.description || '').toLowerCase().includes(q),
    );
  }
  return list;
});

function projectColor(projectId) {
  if (!colorMap[projectId]) {
    const idx = Object.keys(colorMap).length % colorOptions.length;
    colorMap[projectId] = colorOptions[idx];
  }
  return colorMap[projectId];
}

function projectInitials(name) {
  return name
    .split(/[\s_-]+/)
    .map((word) => word.charAt(0).toUpperCase())
    .slice(0, 2)
    .join('');
}

function formatUpdatedAt(updatedAt) {
  if (!updatedAt) return '-';
  const d = new Date(updatedAt);
  const now = new Date();
  const diffMins = Math.floor((now.getTime() - d.getTime()) / 60000);
  if (diffMins < 60) return t('projects.time_minutes', { n: diffMins || 1 });
  const diffHrs = Math.floor(diffMins / 60);
  if (diffHrs < 24) return t('projects.time_hours', { n: diffHrs });
  const diffDays = Math.floor(diffHrs / 24);
  if (diffDays < 30) return t('projects.time_days', { n: diffDays });
  return d.toLocaleDateString();
}

function openProject(project) {
  settings.setCurrentProject(project.project_id);
  window.location.href = '/main';
}

async function suggestSlug() {
  const text = newProjDisplayName.value.trim();
  if (!text) return;
  slugManuallyEdited.value = false;
  try {
    const res = await postRequest('/api/v1/slug/romanize', { text });
    const suggested = res.data?.suggested;
    if (suggested) {
      _autoFilling = true;
      newProjSlug.value = suggested;
      _autoFilling = false;
    } else {
      showToast(t('projects.toast_romanize_empty'), 'warning');
    }
  } catch { showToast(t('projects.toast_romanize_empty'), 'warning'); }
}

async function createProject() {
  const displayName = newProjDisplayName.value.trim();
  if (!displayName) {
    showToast(t('projects.toast_name_required'), 'warning');
    return;
  }
  if (!slugValid.value) return;
  try {
    await postRequest('/api/v1/projects', {
      project_name: displayName,
      project_id: newProjSlug.value,
      description: newProjDesc.value || null,
      color: selectedColor.value ?? null,
    });
    showModal.value = false;
    showToast(t('projects.toast_created', { name: displayName }), 'success');
    await settings.fetchProjects();
    newProjDisplayName.value = '';
    newProjSlug.value = '';
    newProjDesc.value = '';
    slugManuallyEdited.value = false;
  } catch {
    showToast(t('projects.toast_error'), 'danger');
  }
}

onMounted(async () => {
  loading.value = true;
  try {
    await settings.fetchProjects();
  } finally {
    loading.value = false;
  }

  if (route.query.new === '1') showModal.value = true;
});
</script>

<style scoped>
.input-slug-error {
  border-color: #ef4444 !important;
  box-shadow: 0 0 0 2px rgba(239, 68, 68, 0.2);
}
.form-slug-error-text {
  font-size: .75rem;
  color: #ef4444;
  margin-top: 4px;
  margin-bottom: 0;
}
</style>
