import { defineStore } from 'pinia';
import { computed, ref } from 'vue';
import { getRequest } from '@shared/api';

const ADMIN_PERMISSIONS = [
  'system.settings.manage',
  'system.user.read',
  'project.settings.read',
  'project.settings.manage',
];

const getStoredAccessToken = () => window.__accessToken__ || sessionStorage.getItem('fg_access_token') || null;

export const useAuthStore = defineStore('auth', () => {
  const user = ref(null);
  const permissions = ref([]);
  const accessToken = ref(getStoredAccessToken());

  const isAuthenticated = computed(() => !!accessToken.value);

  function can(permissionCode) {
    if (user.value?.is_admin) {
      return true;
    }
    return permissions.value.includes(permissionCode);
  }

  function setUser(userData, perms) {
    user.value = userData;
    permissions.value = userData?.is_admin ? [...ADMIN_PERMISSIONS] : perms || userData?.permissions || [];
  }

  async function initialize() {
    const token = sessionStorage.getItem('fg_access_token') || window.__accessToken__ || null;
    if (!token) {
      clearAuth();
      window.location.href = '/index.html';
      return;
    }

    sessionStorage.setItem('fg_access_token', token);
    window.__accessToken__ = token;
    accessToken.value = token;

    try {
      // If immediately after login, check permission info from global (B005)
      const perms = window.__userPermissions__;
      if (perms) {
        setUser({
          is_admin: perms.is_admin,
          user_id: null,
          username: null,
          email: null,
          first_login_required: false,
          roles: perms.roles,
          permissions: perms.is_admin ? [...ADMIN_PERMISSIONS] : [],
        }, perms.permissions || []);
        delete window.__userPermissions__;
        return;
      }

      // In the normal case, call /auth/me
      const { data } = await getRequest('/auth/me');
      setUser(data, data?.permissions || []);
    } catch {
      clearAuth();
      window.location.href = '/index.html';
    }
  }

  function clearAuth() {
    user.value = null;
    permissions.value = [];
    accessToken.value = null;
    sessionStorage.removeItem('fg_access_token');
    localStorage.removeItem('access_token');
    localStorage.removeItem('refresh_token');
    localStorage.removeItem('fg_refresh_token');
    sessionStorage.removeItem('fg_refresh_token');
    delete window.__accessToken__;
  }

  return { user, permissions, accessToken, isAuthenticated, can, setUser, initialize, clearAuth };
});
