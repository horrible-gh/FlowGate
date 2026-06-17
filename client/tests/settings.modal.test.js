/**
 * settings.modal.test.js
 * Modal confirmation tests — numbering digit-change confirmation modal (project name typing + button activation)
 */
import { describe, it, expect, vi } from 'vitest';
import { mount } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import { createI18n } from 'vue-i18n';
import { useAuthStore } from '../src/settings/stores/auth.js';
import { useSettingsStore } from '../src/settings/stores/settings.js';
import NumberingSettingsView from '../src/settings/views/project/NumberingSettingsView.vue';
import ko from '@shared/i18n/ko';
import en from '@shared/i18n/en';
import ja from '@shared/i18n/ja';

vi.mock('vue-router', () => ({
  RouterLink: { template: '<a><slot /></a>', props: ['to'] },
  useRouter: () => ({ push: vi.fn() }),
  useRoute: () => ({ params: {}, meta: {} }),
}));

const mockImpact = { document_count: 1234, group_count: 56, sub_group_count: 123 };

vi.mock('@shared/api', () => ({
  getRequest: vi.fn(async (path) => {
    if (path.includes('/numbering/impact')) return { data: { data: mockImpact } };
    if (path.includes('/settings')) return { data: { data: { digits_group: 4, digits_sub_group: 3, digits_type: 4, name: 'FLOWGATE', group_structure: 2 } } };
    return { data: { data: {} } };
  }),
  postRequest: vi.fn().mockResolvedValue({ data: { data: { job_id: 'job_001' } } }),
  patchRequest: vi.fn().mockResolvedValue({ data: {} }),
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

describe('NumberingSettingsView — numbering change modal', () => {
  it('shows the impact modal on save button click', async () => {
    const wrapper = createWrapper();
    await wrapper.vm.$nextTick();
    await new Promise((r) => setTimeout(r, 0));

    const saveBtn = wrapper.findAll('button').find((b) => b.text() === 'Save');
    await saveBtn.trigger('click');
    await wrapper.vm.$nextTick();
    await new Promise((r) => setTimeout(r, 0));

    expect(wrapper.find('[data-testid="impact-modal"]').exists()).toBe(true);
  });

  it('disables the execute button when project name is not entered', async () => {
    const wrapper = createWrapper();
    wrapper.vm.showImpactModal = true;
    wrapper.vm.impact = mockImpact;
    wrapper.vm.currentProjectName = 'FLOWGATE';
    wrapper.vm.confirmProjectName = '';
    await wrapper.vm.$nextTick();

    const execBtn = wrapper.find('[data-testid="execute-btn"]');
    expect(execBtn.attributes('disabled')).toBeDefined();
  });

  it('enables the execute button when project name is entered correctly', async () => {
    const wrapper = createWrapper();
    wrapper.vm.showImpactModal = true;
    wrapper.vm.impact = mockImpact;
    wrapper.vm.currentProjectName = 'FLOWGATE';
    wrapper.vm.confirmProjectName = 'FLOWGATE';
    await wrapper.vm.$nextTick();

    const execBtn = wrapper.find('[data-testid="execute-btn"]');
    expect(execBtn.attributes('disabled')).toBeUndefined();
  });

  it('displays the impact count in the modal', async () => {
    const wrapper = createWrapper();
    wrapper.vm.showImpactModal = true;
    wrapper.vm.impact = mockImpact;
    await wrapper.vm.$nextTick();

    expect(wrapper.text()).toContain('1,234');
    expect(wrapper.text()).toContain('56');
    expect(wrapper.text()).toContain('123');
  });
});
