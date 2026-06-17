/**
 * settings.permission.test.js
 * Permission branching tests — navigation and action button visibility control per admin/manager role
 */
import { describe, it, expect, vi } from 'vitest';
import { mount } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import { createI18n } from 'vue-i18n';
import { useAuthStore } from '../src/settings/stores/auth.js';
import SettingsNav from '../src/settings/components/SettingsNav.vue';
import UsersView from '../src/settings/views/users/UsersView.vue';
import ko from '@shared/i18n/ko';
import en from '@shared/i18n/en';
import ja from '@shared/i18n/ja';

vi.mock('vue-router', () => ({
  RouterLink: { template: '<a><slot /></a>', props: ['to'] },
  RouterView: { template: '<div />' },
  useRouter: () => ({ push: vi.fn() }),
  useRoute: () => ({ params: {}, meta: {} }),
}));

vi.mock('@shared/api', () => ({
  getRequest: vi.fn().mockResolvedValue({ data: { data: { items: [], total_pages: 1 } } }),
  patchRequest: vi.fn().mockResolvedValue({ data: {} }),
  postRequest: vi.fn().mockResolvedValue({ data: {} }),
  deleteRequest: vi.fn().mockResolvedValue({ data: {} }),
}));

const i18n = createI18n({ legacy: false, locale: 'ko', messages: { ko, en, ja } });

function createWrapper(Component, permissions = []) {
  const pinia = createPinia();
  setActivePinia(pinia);
  const auth = useAuthStore();
  auth.setUser({ id: 'u1', username: 'testuser' }, permissions);
  auth.accessToken = 'fake-token';
  return mount(Component, { global: { plugins: [pinia, i18n] } });
}

describe('SettingsNav — permission branching', () => {
  it('hides the user section when system.user.read permission is absent', () => {
    const wrapper = createWrapper(SettingsNav, ['system.settings.manage']);
    expect(wrapper.text()).not.toContain('Users');
  });

  it('shows the user section when system.user.read permission is present', () => {
    const wrapper = createWrapper(SettingsNav, ['system.settings.manage', 'system.user.read']);
    expect(wrapper.text()).toContain('Users');
  });

  it('project section: hides document type link when currentProjectId is absent', () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const auth = useAuthStore();
    auth.setUser({ id: 'u1', username: 'testuser' }, ['system.settings.manage', 'project.settings.read']);
    auth.accessToken = 'fake-token';
    const wrapper = mount(SettingsNav, { global: { plugins: [pinia, i18n] } });
    expect(wrapper.text()).not.toContain('Document Type');
  });
});

describe('UsersView — permission branching', () => {
  it('hides the new user button when system.user.create permission is absent', () => {
    const wrapper = createWrapper(UsersView, ['system.user.read']);
    expect(wrapper.text()).not.toContain('+ New User');
  });

  it('shows the new user button when system.user.create permission is present', () => {
    const wrapper = createWrapper(UsersView, ['system.user.read', 'system.user.create']);
    expect(wrapper.text()).toContain('+ New User');
  });
});
