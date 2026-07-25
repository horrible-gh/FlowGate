import { beforeEach, describe, expect, it, vi } from 'vitest';
import { flushPromises, shallowMount } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import { createI18n } from 'vue-i18n';
import MainProjectsView from '../src/main/views/ProjectsView.vue';
import SettingsProjectsView from '../src/settings/views/projects/ProjectsView.vue';
import ko from '@shared/i18n/ko';
import en from '@shared/i18n/en';
import ja from '@shared/i18n/ja';

const { getRequest, postRequest, setProjectArchiveState, showToast, routerPush } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
  setProjectArchiveState: vi.fn(),
  showToast: vi.fn(),
  routerPush: vi.fn(),
}));

vi.mock('vue-router', () => ({
  RouterLink: { template: '<a><slot /></a>', props: ['to'] },
  useRouter: () => ({ push: routerPush }),
  useRoute: () => ({ query: {} }),
}));

vi.mock('@shared/api', () => ({
  getRequest,
  postRequest,
}));

vi.mock('@shared/projects', () => ({
  setProjectArchiveState,
}));

vi.mock('../src/main/components/common/useToast', () => ({
  useToast: () => ({ showToast }),
}));

const i18n = createI18n({ legacy: false, locale: 'ko', messages: { ko, en, ja } });
const projectPayload = {
  data: {
    projects: [
      { project_id: 'active', project_name: 'Active', is_active: 1 },
      { project_id: 'archived', project_name: 'Archived', is_active: 0 },
    ],
  },
};

function mountView(component) {
  const pinia = createPinia();
  setActivePinia(pinia);
  return shallowMount(component, {
    global: {
      plugins: [pinia, i18n],
      stubs: {
        AppHeader: true,
        AppIcon: true,
        RouterLink: { template: '<a><slot /></a>', props: ['to'] },
      },
    },
  });
}

beforeEach(() => {
  localStorage.clear();
  getRequest.mockReset();
  postRequest.mockReset();
  setProjectArchiveState.mockReset();
  showToast.mockReset();
  routerPush.mockReset();
  getRequest.mockResolvedValue(projectPayload);
});

describe('project archive controls', () => {
  it('main view archives through HTTP and refreshes the management list on success', async () => {
    setProjectArchiveState.mockResolvedValue({ project_id: 'active', is_active: 0 });
    const wrapper = mountView(MainProjectsView);
    await flushPromises();

    expect(wrapper.text()).toContain('Active');
    expect(wrapper.text()).toContain('Archived');
    await wrapper.get(`button[title="${i18n.global.t('projects.archive')}"]`).trigger('click');
    await flushPromises();

    expect(setProjectArchiveState).toHaveBeenCalledWith('active', true);
    expect(getRequest).toHaveBeenLastCalledWith('/api/v1/projects', { status: 'all' });
    expect(getRequest).toHaveBeenCalledTimes(2);
    expect(showToast).toHaveBeenCalledWith(i18n.global.t('projects.toast_archived'), 'info');
  });

  it('settings view keeps the list unchanged and never emits a success toast on failure', async () => {
    setProjectArchiveState.mockRejectedValue(new Error('network failed'));
    const wrapper = mountView(SettingsProjectsView);
    await flushPromises();

    await wrapper.get(`button[title="${i18n.global.t('projects.archive')}"]`).trigger('click');
    await flushPromises();

    expect(setProjectArchiveState).toHaveBeenCalledWith('active', true);
    expect(getRequest).toHaveBeenCalledTimes(1);
    expect(wrapper.text()).toContain('Active');
    expect(showToast).not.toHaveBeenCalledWith(i18n.global.t('projects.toast_archived'), 'info');
    expect(showToast).toHaveBeenCalledWith(i18n.global.t('projects.toast_state_error'), 'danger');
  });
});
