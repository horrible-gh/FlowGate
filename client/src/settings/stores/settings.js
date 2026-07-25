import { defineStore } from 'pinia';
import { computed, ref } from 'vue';
import { getRequest, patchRequest } from '@shared/api';

function normalizeSettings(payload) {
  const raw = payload?.settings || payload || {};
  if (!Array.isArray(raw)) return raw;
  return raw.reduce((acc, row) => {
    if (row?.setting_key) {
      acc[row.setting_key] = row.setting_value;
    }
    return acc;
  }, {});
}

export const useSettingsStore = defineStore('settings', () => {
  const systemSettings = ref({});
  const systemInfo = ref(null);
  const projects = ref([]);
  const currentProjectId = ref(localStorage.getItem('fg_current_project_id') || null);
  const loading = ref(false);
  const error = ref(null);

  const activeProjects = computed(() => projects.value.filter((p) => p.is_active === 1));

  async function fetchSystemSettings() {
    loading.value = true;
    error.value = null;
    try {
      const { data } = await getRequest('/api/v1/system/settings');
      systemSettings.value = normalizeSettings(data);
    } catch (e) {
      error.value = e;
      throw e;
    } finally {
      loading.value = false;
    }
  }

  async function updateSystemSettings(patch) {
    const { data } = await patchRequest('/api/v1/system/settings', { updates: patch });
    Object.assign(systemSettings.value, patch);
    return data.updated || data;
  }

  async function fetchSystemInfo() {
    const { data } = await getRequest('/api/v1/system/info');
    systemInfo.value = data || {};
  }

  async function fetchProjects() {
    const { data } = await getRequest('/api/v1/projects', { status: 'all' });
    const raw = Array.isArray(data) ? data : data.projects || [];
    projects.value = raw.filter((p) => p.project_id !== '__SYSTEM__');
    if (activeProjects.value.length && !currentProjectId.value) {
      setCurrentProject(activeProjects.value[0].project_id);
    }
  }

  function setCurrentProject(pid) {
    currentProjectId.value = pid;
    if (pid) {
      localStorage.setItem('fg_current_project_id', pid);
    } else {
      localStorage.removeItem('fg_current_project_id');
    }
  }

  return {
    systemSettings,
    systemInfo,
    projects,
    activeProjects,
    currentProjectId,
    loading,
    error,
    fetchSystemSettings,
    updateSystemSettings,
    fetchSystemInfo,
    fetchProjects,
    setCurrentProject,
  };
});
