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
  auth.setUser({ id: 'u1', username: 'admin' }, ['project.settings.edit', 'project.settings.read']);
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
    data: { data: { digits_group: 4, digits_sub_group: 3, digits_type: 4, group_structure: 2 } },
  });
  patchRequest.mockResolvedValue({ data: {} });
});

describe('NumberingSettingsView', () => {
  it('loads numbering settings for the current project', async () => {
    const wrapper = createWrapper();
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(getRequest).toHaveBeenCalledWith('/api/v1/projects/proj_001/settings');
    expect(wrapper.find('.code-block').text()).toContain('0001');
  });

  it('saves edited digit settings', async () => {
    const wrapper = createWrapper();
    await new Promise((resolve) => setTimeout(resolve, 0));

    const inputs = wrapper.findAll('input[type="number"]');
    await inputs[0].setValue(5);
    await inputs[1].setValue(3);

    const saveBtn = wrapper.findAll('button').find((button) => button.text().includes(i18n.global.t('common.save')));
    expect(saveBtn).toBeTruthy();
    await saveBtn.trigger('click');

    expect(patchRequest).toHaveBeenCalledWith('/api/v1/projects/proj_001/settings', {
      digits_group: 5,
      digits_type: 3,
    });
  });

  it('does not render subgroup digit or numbering structure controls', async () => {
    const wrapper = createWrapper();
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(wrapper.findAll('input[type="number"]')).toHaveLength(2);
    expect(wrapper.findAll('input[type="radio"]')).toHaveLength(0);
    expect(wrapper.text()).not.toContain(i18n.global.t('settings.project.numbering_settings_view.label_28'));
    expect(wrapper.text()).not.toContain(i18n.global.t('settings.project.numbering_settings_view.text_40'));
  });

  it('resets settings by refetching from the server', async () => {
    const wrapper = createWrapper();
    await new Promise((resolve) => setTimeout(resolve, 0));

    const resetBtn = wrapper.findAll('button').find((button) => button.text().includes(i18n.global.t('common.reset')));
    expect(resetBtn).toBeTruthy();
    await resetBtn.trigger('click');

    expect(getRequest).toHaveBeenCalledTimes(2);
  });

  it('updates the preview when digit inputs change', async () => {
    const wrapper = createWrapper();
    await new Promise((resolve) => setTimeout(resolve, 0));

    await wrapper.findAll('input[type="number"]')[0].setValue(5);

    expect(wrapper.find('.code-block').text()).toContain('00001');
  });
});
