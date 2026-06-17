import { describe, it, expect, vi, beforeEach } from 'vitest';
import { mount } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import { createI18n } from 'vue-i18n';
import { useAuthStore } from '../src/settings/stores/auth.js';
import { useSettingsStore } from '../src/settings/stores/settings.js';
import NumberingSettingsView from '../src/settings/views/project/NumberingSettingsView.vue';
import ko from '@shared/i18n/ko';
import en from '@shared/i18n/en';
import ja from '@shared/i18n/ja';

const { getRequest, patchRequest, showToast } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  patchRequest: vi.fn(),
  showToast: vi.fn(),
}));

vi.mock('vue-router', () => ({
  RouterLink: { template: '<a><slot /></a>', props: ['to'] },
  useRouter: () => ({ push: vi.fn() }),
  useRoute: () => ({ params: {}, meta: {} }),
}));

vi.mock('@shared/api', () => ({
  getRequest,
  patchRequest,
}));

vi.mock('../src/main/components/common/useToast', () => ({
  useToast: () => ({ showToast }),
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

beforeEach(() => {
  getRequest.mockReset();
  patchRequest.mockReset();
  showToast.mockReset();
  getRequest.mockResolvedValue({
    data: { data: { digits_group: 4, digits_sub_group: 3, digits_type: 4 } },
  });
  patchRequest.mockResolvedValue({ data: {} });
});

describe('NumberingSettingsView current behavior', () => {
  it('does not render removed migration progress UI', async () => {
    const wrapper = createWrapper();
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(wrapper.find('[data-testid="progress-modal"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="impact-modal"]').exists()).toBe(false);
  });

  it('shows a success toast after saving', async () => {
    const wrapper = createWrapper();
    await new Promise((resolve) => setTimeout(resolve, 0));

    const saveBtn = wrapper.findAll('button').find((button) => button.text().includes(i18n.global.t('common.save')));
    expect(saveBtn).toBeTruthy();
    await saveBtn.trigger('click');
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(showToast).toHaveBeenCalledWith(i18n.global.t('common.toast.settings_saved'), 'success');
  });

  it('shows a failure toast when saving fails', async () => {
    patchRequest.mockRejectedValueOnce(new Error('boom'));
    const wrapper = createWrapper();
    await new Promise((resolve) => setTimeout(resolve, 0));

    const saveBtn = wrapper.findAll('button').find((button) => button.text().includes(i18n.global.t('common.save')));
    expect(saveBtn).toBeTruthy();
    await saveBtn.trigger('click');
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(showToast).toHaveBeenCalledWith(i18n.global.t('common.toast.settings_save_failed'), 'danger');
  });

  it('shows a reset toast after reloading settings', async () => {
    const wrapper = createWrapper();
    await new Promise((resolve) => setTimeout(resolve, 0));

    const resetBtn = wrapper.findAll('button').find((button) => button.text().includes(i18n.global.t('common.reset')));
    expect(resetBtn).toBeTruthy();
    await resetBtn.trigger('click');
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(showToast).toHaveBeenCalledWith(i18n.global.t('common.toast.settings_reverted'), 'info');
  });
});
