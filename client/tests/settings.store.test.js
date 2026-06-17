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
});
