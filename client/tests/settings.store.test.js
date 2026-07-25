import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';

const { getRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(),
}));

vi.mock('@shared/api', () => ({
  getRequest,
  patchRequest: vi.fn(),
}));

describe('settings store', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.clear();
    getRequest.mockReset();
  });

  it('normalizes system settings rows into key-value fields', async () => {
    getRequest.mockResolvedValueOnce({
      data: {
        settings: [
          { setting_key: 'storage_root', setting_value: 'C:/workspace/projects/FlowGate/server/storage' },
          { setting_key: 'log_level', setting_value: 'INFO' },
        ],
      },
    });

    const { useSettingsStore } = await import('../src/settings/stores/settings.js');
    const store = useSettingsStore();

    await store.fetchSystemSettings();

    expect(store.systemSettings.storage_root).toBe('C:/workspace/projects/FlowGate/server/storage');
    expect(store.systemSettings.log_level).toBe('INFO');
  });

  it('loads all projects for management while activeProjects excludes archived entries', async () => {
    getRequest.mockResolvedValueOnce({
      data: {
        projects: [
          { project_id: 'active', project_name: 'Active', is_active: 1 },
          { project_id: 'archived', project_name: 'Archived', is_active: 0 },
        ],
      },
    });

    const { useSettingsStore } = await import('../src/settings/stores/settings.js');
    const store = useSettingsStore();

    await store.fetchProjects();

    expect(getRequest).toHaveBeenCalledWith('/api/v1/projects', { status: 'all' });
    expect(store.projects.map((p) => p.project_id)).toEqual(['active', 'archived']);
    expect(store.activeProjects.map((p) => p.project_id)).toEqual(['active']);
    expect(store.currentProjectId).toBe('active');
  });
  it('retains an archived current project while activeProjects stays selectable-only', async () => {
    localStorage.setItem('fg_current_project_id', 'archived');
    getRequest.mockResolvedValueOnce({
      data: {
        projects: [
          { project_id: 'active', project_name: 'Active', is_active: 1 },
          { project_id: 'archived', project_name: 'Archived', is_active: 0 },
        ],
      },
    });

    const { useSettingsStore } = await import('../src/settings/stores/settings.js');
    const store = useSettingsStore();

    await store.fetchProjects();

    expect(store.currentProjectId).toBe('archived');
    expect(localStorage.getItem('fg_current_project_id')).toBe('archived');
    expect(store.activeProjects.map((p) => p.project_id)).toEqual(['active']);
  });
});
