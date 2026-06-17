/**
 * settings.polling.test.js
 * Progress polling tests — numbering reformat job status polling (in progress → completed)
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import { createI18n } from 'vue-i18n';
import { useAuthStore } from '../src/settings/stores/auth.js';
import { useSettingsStore } from '../src/settings/stores/settings.js';
import NumberingSettingsView from '../src/settings/views/project/NumberingSettingsView.vue';
import ko from '@shared/i18n/ko';
import en from '@shared/i18n/en';
import ja from '@shared/i18n/ja';

const { JOB_ID, pollState } = vi.hoisted(() => {
  const JOB_ID = 'job_poll_001';
  const pollState = { count: 0 };
  return { JOB_ID, pollState };
});

vi.mock('vue-router', () => ({
  RouterLink: { template: '<a><slot /></a>', props: ['to'] },
  useRouter: () => ({ push: vi.fn() }),
  useRoute: () => ({ params: {}, meta: {} }),
}));

vi.mock('@shared/api', () => ({
  getRequest: vi.fn(async (path) => {
    if (path.includes(`/jobs/${JOB_ID}/status`)) {
      pollState.count++;
      if (pollState.count < 3) {
        return { data: { data: { status: 'running', progress: pollState.count * 30 } } };
      }
      return {
        data: {
          data: {
            status: 'completed',
            progress: 100,
            result: { success_count: 1234, fail_count: 0, elapsed_sec: 5.2 },
          },
        },
      };
    }
    if (path.includes('/settings')) {
      return { data: { data: { digits_group: 4, digits_sub_group: 3, digits_type: 4, name: 'FLOWGATE', group_structure: 2 } } };
    }
    if (path.includes('/numbering/impact')) {
      return { data: { data: { document_count: 100, group_count: 10, sub_group_count: 5 } } };
    }
    return { data: { data: {} } };
  }),
  postRequest: vi.fn().mockResolvedValue({ data: { data: { job_id: 'job_poll_001' } } }),
  patchRequest: vi.fn().mockResolvedValue({ data: {} }),
}));

const i18n = createI18n({ legacy: false, locale: 'ko', messages: { ko, en, ja } });

function createWrapper() {
  const pinia = createPinia();
  setActivePinia(pinia);
  const auth = useAuthStore();
  auth.setUser({ id: 'u1', username: 'admin' }, ['project.settings.edit']);
  auth.accessToken = 'fake-token';
  const settings = useSettingsStore();
  settings.currentProjectId = 'proj_001';
  return mount(NumberingSettingsView, { global: { plugins: [pinia, i18n] } });
}

describe('NumberingSettingsView — progress polling', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    pollState.count = 0;
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('shows the progress modal when showProgressModal=true', async () => {
    const wrapper = createWrapper();
    wrapper.vm.showProgressModal = true;
    wrapper.vm.jobStatus = 'running';
    await wrapper.vm.$nextTick();
    expect(wrapper.find('[data-testid="progress-modal"]').exists()).toBe(true);
  });

  it('polling: running → updates progress', async () => {
    const wrapper = createWrapper();
    wrapper.vm.showProgressModal = true;
    wrapper.vm.jobStatus = 'running';
    wrapper.vm.jobProgress = 0;
    wrapper.vm.startPolling(JOB_ID);

    // 1st poll (setInterval 1500ms)
    await vi.advanceTimersByTimeAsync(1500);
    await wrapper.vm.$nextTick();
    expect(wrapper.vm.jobProgress).toBe(30);
    expect(wrapper.vm.jobStatus).toBe('running');

    // 2nd poll
    await vi.advanceTimersByTimeAsync(1500);
    await wrapper.vm.$nextTick();
    expect(wrapper.vm.jobProgress).toBe(60);

    // 3rd poll → completed
    await vi.advanceTimersByTimeAsync(1500);
    await wrapper.vm.$nextTick();
    expect(wrapper.vm.jobProgress).toBe(100);
    expect(wrapper.vm.jobStatus).toBe('completed');
    expect(wrapper.vm.jobResult?.success_count).toBe(1234);
  });

  it('shows completion/close text when status is completed', async () => {
    const wrapper = createWrapper();
    wrapper.vm.showProgressModal = true;
    wrapper.vm.jobStatus = 'completed';
    wrapper.vm.jobProgress = 100;
    wrapper.vm.jobResult = { success_count: 10, fail_count: 0, elapsed_sec: 2 };
    await wrapper.vm.$nextTick();

    const modal = wrapper.find('[data-testid="progress-modal"]');
    expect(modal.text()).toContain('Completed');
    expect(modal.text()).toContain('Close');
  });

  it('closes the progress modal on 409 Conflict', async () => {
    vi.useRealTimers(); // this test uses real timers
    const { postRequest } = await import('@shared/api');
    postRequest.mockRejectedValueOnce({ response: { status: 409 } });
    const wrapper = createWrapper();
    await wrapper.vm.$nextTick();

    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {});
    await wrapper.vm.executeMigration();
    await wrapper.vm.$nextTick();

    expect(wrapper.vm.showProgressModal).toBe(false);
    expect(alertSpy).toHaveBeenCalled();
    alertSpy.mockRestore();
  });
});
