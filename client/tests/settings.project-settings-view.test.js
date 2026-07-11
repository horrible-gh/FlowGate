import { describe, it, expect, vi, beforeEach } from 'vitest';
import { mount, shallowMount } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import { createI18n } from 'vue-i18n';
import { useSettingsStore } from '../src/settings/stores/settings.js';
import ProjectSettingsView from '../src/settings/views/project/ProjectSettingsView.vue';
import PathSettingsView from '../src/settings/views/project/PathSettingsView.vue';
import ko from '@shared/i18n/ko';
import en from '@shared/i18n/en';
import ja from '@shared/i18n/ja';

const { getRequest, patchRequest, postRequest, postFormRequest, showToast, routerReplace, routeState } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  patchRequest: vi.fn(),
  postRequest: vi.fn(),
  postFormRequest: vi.fn(),
  showToast: vi.fn(),
  routerReplace: vi.fn(),
  routeState: { query: {} },
}));

vi.mock('vue-router', () => ({
  RouterLink: { template: '<a><slot /></a>', props: ['to'] },
  useRouter: () => ({ replace: routerReplace, push: vi.fn() }),
  useRoute: () => routeState,
}));

vi.mock('@shared/api', () => ({
  getRequest,
  patchRequest,
  postRequest,
  postFormRequest,
}));

vi.mock('../src/main/components/common/useToast', () => ({
  useToast: () => ({ showToast }),
}));

const i18n = createI18n({ legacy: false, locale: 'ko', messages: { ko, en, ja } });

function setupStore() {
  const pinia = createPinia();
  setActivePinia(pinia);
  const settings = useSettingsStore();
  settings.currentProjectId = 'flowgate';
  settings.projects = [{ project_id: 'flowgate', project_name: 'FlowGate' }];
  settings.systemSettings = { storage_root: '/data/flowgate/storage' };
  return pinia;
}

function mountProjectSettings(query = {}) {
  routeState.query = query;
  const pinia = setupStore();
  return shallowMount(ProjectSettingsView, {
    global: {
      plugins: [pinia, i18n],
      stubs: {
        PathSettingsView: { template: '<section data-testid="path-view" />' },
        NumberingSettingsView: { template: '<section data-testid="numbering-view" />' },
        MessagesView: { template: '<section data-testid="messages-view" />' },
      },
    },
  });
}

beforeEach(() => {
  getRequest.mockReset();
  patchRequest.mockReset();
  postRequest.mockReset();
  postFormRequest.mockReset();
  showToast.mockReset();
  routerReplace.mockReset();
  routeState.query = {};
  getRequest.mockResolvedValue({ data: { data: { storage_root_override: null } } });
});

describe('ProjectSettingsView tabs', () => {
  it('renders the current project tabs with paths as the default tab', () => {
    const wrapper = mountProjectSettings();

    // Tab set has grown across groups (source-mode 0147, test-recipes 0152, git 0162/0186,
    // ai 0164/0187) to 7 unconditional tabs; the removed doc-types/structure tabs stay gone.
    expect(wrapper.findAll('.tab-nav-item')).toHaveLength(7);
    expect(wrapper.text()).toContain(i18n.global.t('settings.project.path'));
    expect(wrapper.text()).toContain(i18n.global.t('settings.project.project_settings_view.text_56'));
    expect(wrapper.text()).toContain(i18n.global.t('settings.project.messages'));
    expect(wrapper.text()).toContain(i18n.global.t('settings.project.git.tab'));
    expect(wrapper.text()).toContain(i18n.global.t('settings.project.ai.tab'));
    expect(wrapper.text()).not.toContain(i18n.global.t('settings.project.types'));
    expect(wrapper.text()).not.toContain(i18n.global.t('projects.form_structure'));
    expect(wrapper.find('[data-testid="path-view"]').exists()).toBe(true);
  });

  it('falls back to paths when an old removed tab is requested', () => {
    const wrapper = mountProjectSettings({ tab: 'doctypes' });

    expect(wrapper.find('[data-testid="path-view"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="numbering-view"]').exists()).toBe(false);
  });

  it('updates the query when selecting a non-default tab', async () => {
    const wrapper = mountProjectSettings();
    const numberingTab = wrapper.findAll('.tab-nav-item')
      .find((tab) => tab.text().includes(i18n.global.t('settings.project.project_settings_view.text_56')));

    expect(numberingTab).toBeTruthy();
    await numberingTab.trigger('click');

    expect(routerReplace).toHaveBeenCalledWith({
      path: '/settings/project',
      query: { tab: 'numbering' },
    });
  });
});

describe('PathSettingsView previews', () => {
  it('renders document and source previews using the server storage path model', async () => {
    const pinia = setupStore();
    const wrapper = mount(PathSettingsView, { global: { plugins: [pinia, i18n] } });
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(wrapper.text()).toContain('/data/flowgate/storage/documents/flowgate/main');
    expect(wrapper.text()).toContain('/data/flowgate/storage/src/FlowGate/main');
    expect(wrapper.find('.path-structure').text()).toContain('<module>/<group>/<doc_number>_<filename>');
  });

  it('uses a project storage override when one is configured', async () => {
    getRequest.mockResolvedValueOnce({ data: { data: { storage_root_override: 'D:/flowgate-store/flowgate' } } });
    const pinia = setupStore();
    const wrapper = mount(PathSettingsView, { global: { plugins: [pinia, i18n] } });
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(wrapper.text()).toContain('D:/flowgate-store/flowgate/documents/flowgate/main');
    expect(wrapper.text()).toContain('D:/flowgate-store/flowgate/src/FlowGate/main');
  });
});
