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
            <label class="form-label">{{ $t('settings.project.git.translate_url') }}</label>
            <input
              class="form-ctrl"
              v-model="form.translate_url"
              type="text"
              placeholder="http://192.168.0.250:5000"
              style="max-width:360px;"
            />
            <p class="form-hint">{{ $t('settings.project.git.translate_url_hint') }}</p>
          </div>

          <div class="form-group">
            <label class="form-label">{{ $t('settings.project.git.author_name') }}</label>
            <input
              class="form-ctrl"
              v-model="form.author_name"
              type="text"
              placeholder="FlowGate"
              style="max-width:320px;"
            />
          </div>

          <div class="form-group">
            <label class="form-label">{{ $t('settings.project.git.author_email') }}</label>
            <input
              class="form-ctrl"
              v-model="form.author_email"
              type="text"
              autocomplete="off"
              placeholder="flowgate@localhost"
              style="max-width:320px;"
            />
            <p class="form-hint">{{ $t('settings.project.git.author_hint') }}</p>
          </div>

          <!-- TR scope-check enforcement stage (0299 D0004 §3.6). Shows a one-line
               explanation per stage — so the operator isn't left not knowing
               "강제로 올리면 무슨 일이 생기는지" and forcing it on rejects every job
               already in flight all at once. -->
          <div class="form-group">
            <label class="form-label">{{ $t('settings.project.git.tr_scope_stage') }}</label>
            <select class="form-ctrl" v-model="form.tr_scope_stage" style="max-width:320px;">
              <option value="observe">{{ $t('settings.project.git.tr_scope_observe') }}</option>
              <option value="warn">{{ $t('settings.project.git.tr_scope_warn') }}</option>
              <option value="enforce">{{ $t('settings.project.git.tr_scope_enforce') }}</option>
            </select>
            <p class="form-hint">{{ $t(`settings.project.git.tr_scope_hint_${form.tr_scope_stage || 'observe'}`) }}</p>
            <p class="form-hint">{{ $t('settings.project.git.tr_scope_hint') }}</p>
          </div>

          <div class="form-group">
            <label class="form-label" style="display:flex; align-items:center; gap:8px;">
              <input type="checkbox" v-model="form.enabled" />
              {{ $t('settings.project.git.enable') }}
            </label>
            <p class="form-hint">{{ $t('settings.project.git.enable_hint') }}</p>
          </div>

          <div v-if="testResult" class="git-test-result" :class="testOk ? 'git-test-ok' : 'git-test-fail'">
            <AppIcon :name="testOk ? 'check-circle' : 'x-circle'" />
            <span>{{ testMessage }}</span>
          </div>
        </div>
      </div>
    </div>

    <div class="card mb-4">
      <div class="card-hd">
        <span class="card-title">{{ $t('settings.project.git.provision_title') }}</span>
      </div>
      <div class="card-bd pad">
        <template v-if="provision && provision.configured && provision.enabled">
          <div class="provision-row">
            <span class="provision-label">{{ $t('settings.project.git.provision_checkout') }}</span>
            <span>
              {{ provision.base_checkout_exists
                ? $t('settings.project.git.provision_checkout_ready', { branch: provision.base_branch || 'main' })
                : $t('settings.project.git.provision_checkout_missing') }}
            </span>
          </div>
          <div class="provision-row">
            <span class="provision-label">{{ $t('settings.project.git.provision_last_attempt') }}</span>
            <span v-if="!provision.last_attempt">{{ $t('settings.project.git.provision_attempt_none') }}</span>
            <span v-else :class="provision.last_attempt.result === 'failed' ? 'git-error-text' : ''">
              {{ lastAttemptText }}
            </span>
          </div>
          <div v-if="provision.adopt_snapshot" class="provision-row">
            <span class="provision-label">{{ $t('settings.project.git.provision_snapshot') }}</span>
            <span>{{ $t('settings.project.git.provision_snapshot_note', { commit: provision.adopt_snapshot.commit }) }}</span>
          </div>
          <div style="margin-top:10px;">
            <button class="btn btn-secondary" :disabled="busy" @click="provisionNow">
              <AppIcon name="download-simple" />
              {{ busyProvision ? $t('settings.project.git.provision_running') : $t('settings.project.git.provision_run') }}
            </button>
          </div>
          <p class="form-hint" style="margin-top:10px;">
            <AppIcon name="info" />
            {{ $t('settings.project.git.provision_note') }}
          </p>
        </template>
        <p v-else class="form-hint">{{ $t('settings.project.git.provision_disabled_hint') }}</p>
      </div>
    </div>

    <div class="flex" style="justify-content:space-between; gap:10px;">
      <button
        v-if="configured"
        class="btn btn-danger"
        :disabled="busy"
        @click="disconnect"
      >
        <AppIcon name="link-break" /> {{ $t('settings.project.git.disconnect') }}
      </button>
      <span v-else></span>
      <span class="flex" style="gap:10px;">
        <button class="btn btn-secondary" :disabled="busy || !form.repo_url" @click="testConnection">
          <AppIcon name="plugs-connected" />
          {{ busyTest ? $t('settings.project.git.testing') : $t('settings.project.git.test') }}
        </button>
        <button class="btn btn-primary" :disabled="busy || !form.repo_url" @click="save">
          <AppIcon name="floppy-disk" /> {{ $t('common.save') }}
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
import AppIcon from '@shared/AppIcon.vue';

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
const provision = ref(null);
const busyProvision = ref(false);

const form = ref({
  repo_url: '',
  provider: 'generic',
  username: '',
  base_branch: 'main',
  // 0331 NR0005 §4.1: an unconfigured project now seeds the approved
  // default (merge + push). A config already saved as 'wait' is still loaded
  // as 'wait' below — an explicit choice is never migrated away.
  default_finalize_action: 'merge',
  enabled: false,
  translate_url: '',
  author_name: '',
  author_email: '',
  // TR scope-check enforcement stage (0299 D0004 §3.6). Defaults to observe —
  // jobs already in flight don't have a `## 변경 파일` section, so switching
  // straight to enforce would catch all of them.
  tr_scope_stage: 'observe',
});

const secretPlaceholder = computed(() =>
  hasSecret.value ? secretMasked.value || '********' : t('settings.project.git.secret_placeholder'),
);
const testOk = computed(
  () => !!(testResult.value && testResult.value.reachable && testResult.value.authenticated),
);
const lastAttemptText = computed(() => {
  const a = provision.value?.last_attempt;
  if (!a) return '';
  let head = a.result === 'ok'
    ? t('settings.project.git.provision_attempt_ok')
    : t('settings.project.git.provision_attempt_failed');
  if (a.result !== 'ok' && a.reason) head += ` — ${a.reason}`;
  const parts = [head];
  if (a.at) {
    const at = new Date(a.at);
    if (!Number.isNaN(at.getTime())) parts.push(at.toLocaleString());
  }
  const triggerKey = {
    manual: 'provision_trigger_manual',
    workflow_decide: 'provision_trigger_workflow_decide',
    remote_access: 'provision_trigger_remote_access',
  }[a.trigger];
  if (triggerKey) parts.push(t(`settings.project.git.${triggerKey}`));
  return parts.join(' · ');
});

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
    translate_url: cfg.translate_url || '',
    // Empty = not overridden; the placeholder shows the FlowGate default (0237).
    author_name: cfg.author_name || '',
    author_email: cfg.author_email || '',
    tr_scope_stage: cfg.tr_scope_stage || 'observe',
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
    default_finalize_action: 'merge',
    enabled: false,
    translate_url: '',
    author_name: '',
    author_email: '',
    tr_scope_stage: 'observe',
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

async function fetchProvision() {
  if (!projectId.value) return;
  try {
    const { data } = await getRequest(`/api/v1/projects/${projectId.value}/git/provision`);
    provision.value = data.ok === false ? null : data.provision;
  } catch (e) {
    provision.value = null;
  }
}

async function provisionNow() {
  if (!projectId.value) return;
  busy.value = true;
  busyProvision.value = true;
  try {
    const { data } = await postRequest(`/api/v1/projects/${projectId.value}/git/provision`, {});
    if (data.ok === false) {
      showToast(data.error?.message || t('settings.project.git.provision_failed'), 'danger');
    } else {
      const r = data.result || {};
      if (r.provision) provision.value = r.provision;
      if (r.status === 'ok' && r.mode === 'none') {
        showToast(t('settings.project.git.provision_already'), 'info');
      } else if (r.status === 'ok') {
        showToast(t('settings.project.git.provision_done'), 'success');
      } else if (r.reason === 'git_busy') {
        showToast(t('settings.project.git.provision_busy'), 'warning');
      } else {
        showToast(t('settings.project.git.provision_failed'), 'danger');
      }
    }
  } catch (e) {
    const msg = e?.response?.data?.error?.message;
    showToast(msg || t('settings.project.git.provision_failed'), 'danger');
  } finally {
    busy.value = false;
    busyProvision.value = false;
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
      fetchProvision(); // enabling/disabling changes the provisioning panel
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
    fetchProvision();
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

function refreshAll() {
  fetchConfig();
  fetchProvision();
}

onMounted(refreshAll);
watch(projectId, refreshAll);
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
.provision-row {
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding: 3px 0;
  font-size: 0.85rem;
}
.provision-label {
  flex: 0 0 auto;
  min-width: 140px;
  color: var(--text-m, #64748b);
}
</style>
