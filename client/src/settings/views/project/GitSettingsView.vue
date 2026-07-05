<template>
  <div>
    <div class="card mb-4">
      <div class="card-hd">
        <span class="card-title">{{ $t('settings.project.git.title') }}</span>
        <span v-if="configured" class="badge" :class="form.enabled ? 'badge-green' : 'badge-gray'">
          {{ form.enabled ? $t('settings.project.git.enabled_badge') : $t('settings.project.git.disabled_badge') }}
        </span>
      </div>
      <div class="card-bd pad">
        <div class="form-section">
          <div class="form-group">
            <label class="form-label">{{ $t('settings.project.git.repo_url') }}</label>
            <input
              class="form-ctrl"
              v-model="form.repo_url"
              type="text"
              placeholder="https://github.com/org/repo.git"
              style="max-width:560px;"
            />
            <p v-if="urlError" class="form-hint git-error-text">{{ urlError }}</p>
          </div>

          <div class="form-group">
            <label class="form-label">{{ $t('settings.project.git.provider') }}</label>
            <select class="form-ctrl" v-model="form.provider" style="max-width:320px;">
              <option value="generic">{{ $t('settings.project.git.provider_generic') }}</option>
              <option value="github">GitHub</option>
              <option value="gitlab">GitLab</option>
              <option value="gitea">Gitea</option>
              <option value="gitbucket">GitBucket</option>
            </select>
            <p class="form-hint">{{ $t('settings.project.git.provider_hint') }}</p>
          </div>

          <div class="form-group">
            <label class="form-label">{{ $t('settings.project.git.username') }}</label>
            <input class="form-ctrl" v-model="form.username" type="text" autocomplete="off" style="max-width:320px;" />
          </div>

          <div class="form-group">
            <label class="form-label">{{ $t('settings.project.git.secret') }}</label>
            <input
              class="form-ctrl"
              v-model="secretInput"
              type="password"
              autocomplete="new-password"
              :placeholder="secretPlaceholder"
              style="max-width:320px;"
              @input="clearSecret = false"
            />
            <p class="form-hint">
              {{ hasSecret ? $t('settings.project.git.secret_hint_keep') : $t('settings.project.git.secret_hint') }}
              <a v-if="hasSecret && !clearSecret" href="#" @click.prevent="onClearSecret">
                {{ $t('settings.project.git.secret_clear') }}
              </a>
              <span v-if="clearSecret" class="git-error-text">{{ $t('settings.project.git.secret_cleared') }}</span>
            </p>
          </div>

          <div class="form-group">
            <label class="form-label">{{ $t('settings.project.git.base_branch') }}</label>
            <input class="form-ctrl" v-model="form.base_branch" type="text" placeholder="main" style="max-width:220px;" />
          </div>

          <div class="form-group">
            <label class="form-label">{{ $t('settings.project.git.default_action') }}</label>
            <select class="form-ctrl" v-model="form.default_finalize_action" style="max-width:320px;">
              <option value="merge">{{ $t('settings.project.git.action_merge') }}</option>
              <option value="push">{{ $t('settings.project.git.action_push') }}</option>
              <option value="wait">{{ $t('settings.project.git.action_wait') }}</option>
            </select>
            <p class="form-hint">{{ $t('settings.project.git.default_action_hint') }}</p>
          </div>

          <div class="form-group">
            <label class="form-label" style="display:flex; align-items:center; gap:8px;">
              <input type="checkbox" v-model="form.enabled" />
              {{ $t('settings.project.git.enable') }}
            </label>
            <p class="form-hint">{{ $t('settings.project.git.enable_hint') }}</p>
          </div>

          <div v-if="testResult" class="git-test-result" :class="testOk ? 'git-test-ok' : 'git-test-fail'">
            <i :class="testOk ? 'fa-solid fa-circle-check' : 'fa-solid fa-circle-xmark'"></i>
            <span>{{ testMessage }}</span>
          </div>
        </div>
      </div>
    </div>

    <div class="flex" style="justify-content:space-between; gap:10px;">
      <button
        v-if="configured"
        class="btn btn-danger"
        :disabled="busy"
        @click="disconnect"
      >
        <i class="fa-solid fa-link-slash"></i> {{ $t('settings.project.git.disconnect') }}
      </button>
      <span v-else></span>
      <span class="flex" style="gap:10px;">
        <button class="btn btn-secondary" :disabled="busy || !form.repo_url" @click="testConnection">
          <i class="fa-solid fa-plug-circle-check"></i>
          {{ busyTest ? $t('settings.project.git.testing') : $t('settings.project.git.test') }}
        </button>
        <button class="btn btn-primary" :disabled="busy || !form.repo_url" @click="save">
          <i class="fa-solid fa-floppy-disk"></i> {{ $t('common.save') }}
        </button>
      </span>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import { deleteRequest, getRequest, postRequest, putRequest } from '@shared/api';
import { useSettingsStore } from '../../stores/settings.js';
import { useToast } from '../../../main/components/common/useToast';

const { t } = useI18n();
const settings = useSettingsStore();
const { showToast } = useToast();

const projectId = computed(() => settings.currentProjectId);

const configured = ref(false);
const hasSecret = ref(false);
const secretMasked = ref('');
const secretInput = ref('');
const clearSecret = ref(false);
const urlError = ref('');
const busy = ref(false);
const busyTest = ref(false);
const testResult = ref(null);

const form = ref({
  repo_url: '',
  provider: 'generic',
  username: '',
  base_branch: 'main',
  default_finalize_action: 'wait',
  enabled: false,
});

const secretPlaceholder = computed(() =>
  hasSecret.value ? secretMasked.value || '********' : t('settings.project.git.secret_placeholder'),
);
const testOk = computed(
  () => !!(testResult.value && testResult.value.reachable && testResult.value.authenticated),
);
const testMessage = computed(() => {
  const r = testResult.value;
  if (!r) return '';
  if (r.reachable && r.authenticated) {
    const sec = ((r.elapsed_ms ?? 0) / 1000).toFixed(1);
    return t('settings.project.git.test_ok', { branch: r.remote_default_branch || '-', sec });
  }
  const detail = r.failure?.message ? ` — ${r.failure.message}` : '';
  if (r.reachable === false) return t('settings.project.git.test_unreachable') + detail;
  if (r.authenticated === false) return t('settings.project.git.test_auth_failed') + detail;
  return t('settings.project.git.test_failed') + detail;
});

function applyConfig(cfg) {
  configured.value = true;
  hasSecret.value = !!cfg.has_secret;
  secretMasked.value = cfg.secret_masked || '';
  form.value = {
    repo_url: cfg.repo_url || '',
    provider: cfg.provider || 'generic',
    username: cfg.username || '',
    base_branch: cfg.base_branch || 'main',
    default_finalize_action: cfg.default_finalize_action || 'wait',
    enabled: !!cfg.enabled,
  };
  secretInput.value = '';
  clearSecret.value = false;
}

function resetForm() {
  configured.value = false;
  hasSecret.value = false;
  secretMasked.value = '';
  secretInput.value = '';
  clearSecret.value = false;
  form.value = {
    repo_url: '',
    provider: 'generic',
    username: '',
    base_branch: 'main',
    default_finalize_action: 'wait',
    enabled: false,
  };
}

async function fetchConfig() {
  if (!projectId.value) return;
  urlError.value = '';
  testResult.value = null;
  try {
    const { data } = await getRequest(`/api/v1/projects/${projectId.value}/git/config`);
    if (data.configured && data.config) applyConfig(data.config);
    else resetForm();
  } catch (e) {
    showToast(t('common.toast.settings_load_failed'), 'danger');
  }
}

function secretForSubmit() {
  if (clearSecret.value) return '';
  if (secretInput.value) return secretInput.value;
  return null; // keep the stored secret (P0005 §2-1)
}

async function save() {
  if (!projectId.value) return;
  busy.value = true;
  urlError.value = '';
  try {
    const { data } = await putRequest(`/api/v1/projects/${projectId.value}/git/config`, {
      ...form.value,
      username: form.value.username || null,
      secret: secretForSubmit(),
    });
    if (data.ok === false) {
      urlError.value = data.error?.message || '';
      showToast(t('common.toast.settings_save_failed'), 'danger');
    } else {
      applyConfig(data.config);
      showToast(t('settings.project.git.saved'), 'success');
    }
  } catch (e) {
    const msg = e?.response?.data?.error?.message;
    if (msg) urlError.value = msg;
    showToast(t('common.toast.settings_save_failed'), 'danger');
  } finally {
    busy.value = false;
  }
}

async function testConnection() {
  if (!projectId.value) return;
  busy.value = true;
  busyTest.value = true;
  testResult.value = null;
  try {
    // Send the current form values so the test works BEFORE saving (P0005 §3-2);
    // an untouched secret field falls back to the stored secret server-side.
    const body = {
      repo_url: form.value.repo_url || null,
      username: form.value.username || null,
      base_branch: form.value.base_branch || null,
      secret: secretInput.value || null,
    };
    const { data } = await postRequest(`/api/v1/projects/${projectId.value}/git/test-connection`, body);
    if (data.ok === false) {
      testResult.value = { reachable: null, authenticated: null, failure: data.error };
    } else {
      testResult.value = data.result;
    }
  } catch (e) {
    testResult.value = {
      reachable: null,
      authenticated: null,
      failure: e?.response?.data?.error || { message: String(e) },
    };
  } finally {
    busy.value = false;
    busyTest.value = false;
  }
}

async function disconnect() {
  if (!projectId.value) return;
  if (!window.confirm(t('settings.project.git.disconnect_confirm'))) return;
  busy.value = true;
  try {
    await deleteRequest(`/api/v1/projects/${projectId.value}/git/config`);
    resetForm();
    showToast(t('settings.project.git.disconnected'), 'success');
  } catch (e) {
    showToast(t('common.toast.settings_save_failed'), 'danger');
  } finally {
    busy.value = false;
  }
}

function onClearSecret() {
  clearSecret.value = true;
  secretInput.value = '';
}

onMounted(fetchConfig);
watch(projectId, fetchConfig);
</script>

<style scoped>
.git-error-text {
  color: var(--danger, #dc2626);
}
.git-test-result {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-radius: var(--r, 8px);
  font-size: 0.82rem;
}
.git-test-ok {
  color: #166534;
  background: var(--success-l, #f0fdf4);
  border: 1px solid #bbf7d0;
}
.git-test-fail {
  color: #991b1b;
  background: #fef2f2;
  border: 1px solid #fecaca;
}
.badge-gray {
  background: var(--bg-m, #f1f5f9);
  color: var(--text-m, #64748b);
}
</style>
