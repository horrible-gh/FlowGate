<template>
  <div>
    <h1 class="s-page-title">{{ $t('settings.system.title') }}</h1>
    <p class="s-page-sub">{{ $t('settings.system.sub') }}</p>

    <!-- Storage -->
    <div class="card mb-4">
      <div class="card-hd">
        <span class="card-title"><i class="fa-solid fa-hard-drive" style="color:var(--primary);"></i> {{ $t('settings.system.storage.label') }}</span>
      </div>
      <div class="card-bd pad">
        <div class="form-section">
          <div class="form-group">
            <label class="form-label req">{{ $t('settings.storage_root') }}</label>
            <div class="form-inline">
              <input type="text" class="form-ctrl" v-model="storageInput" style="flex:1;">
              <button class="btn btn-secondary btn-sm" style="white-space:nowrap;" @click="openStorageBrowser"><i class="fa-solid fa-folder-open"></i> {{ $t('common.browse') }}</button>
            </div>
            <p class="form-hint">{{ $t('settings.system.storage.hint') }}</p>
          </div>

          <div class="setting-item">
            <div class="setting-info">
              <div class="setting-name">{{ $t('settings.system.storage.auto_dir') }}</div>
              <div class="setting-desc">{{ $t('settings.system.storage.auto_dir_desc') }}</div>
            </div>
            <div class="setting-ctrl">
              <label class="toggle"><input type="checkbox" checked><span class="toggle-track"></span></label>
            </div>
          </div>

          <div class="setting-item">
            <div class="setting-info">
              <div class="setting-name">{{ $t('settings.system.storage.structure') }}</div>
              <div class="setting-desc">{{ $t('settings.system.storage.structure_desc') }} <span class="mono" style="font-size:.75rem;">/documents/&lt;project&gt;/&lt;module&gt;/&lt;group&gt;/&lt;sub_group&gt;/</span></div>
            </div>
            <div class="setting-ctrl"><span class="badge badge-green"><i class="fa-solid fa-check"></i> {{ $t('settings.system.storage.method_c') }}</span></div>
          </div>
        </div>
      </div>
    </div>

    <!-- Source mode -->
    <div class="card mb-4">
      <div class="card-hd">
        <span class="card-title"><i class="fa-solid fa-plug" style="color:#0891b2;"></i> {{ $t('settings.system.source_mode.label') }}</span>
      </div>
      <div class="card-bd pad">
        <div class="setting-item">
          <div class="setting-info">
            <div class="setting-name">{{ $t('settings.system.source_mode.default_mode') }}</div>
            <div class="setting-desc">{{ $t('settings.system.source_mode.default_desc') }}</div>
          </div>
          <div class="setting-ctrl">
            <select class="form-ctrl" v-model="sourceMode" style="min-width:160px;">
              <option value="local">{{ $t('settings.source_mode.local') }}</option>
              <option value="remote">{{ $t('settings.source_mode.remote') }}</option>
            </select>
          </div>
        </div>
      </div>
    </div>

    <!-- Log -->
    <div class="card mb-4">
      <div class="card-hd">
        <span class="card-title"><i class="fa-solid fa-scroll" style="color:#d97706;"></i> {{ $t('settings.system.log.label') }}</span>
      </div>
      <div class="card-bd pad">
        <div class="form-row">
          <div class="form-group">
            <label class="form-label">{{ $t('settings.log_level') }}</label>
            <select class="form-ctrl" v-model="logLevel">
              <option v-for="lv in logLevels" :key="lv" :value="lv">{{ lv }}</option>
            </select>
          </div>
          <div class="form-group">
            <label class="form-label">{{ $t('settings.log_retention') }}</label>
            <input type="number" class="form-ctrl" v-model.number="logRetention" min="1" max="365">
            <p class="form-hint">{{ $t('settings.system.log.retention_hint') }}</p>
          </div>
        </div>

        <div class="setting-item">
          <div class="setting-info">
            <div class="setting-name">{{ $t('settings.system.log.file_log') }}</div>
            <div class="setting-desc">{{ $t('settings.system.log.file_log_desc') }} (<span class="mono" style="font-size:.75rem;">/var/flowgate/logs/</span>)</div>
          </div>
          <div class="setting-ctrl">
            <label class="toggle"><input type="checkbox" v-model="fileLogEnabled"><span class="toggle-track"></span></label>
          </div>
        </div>
        <div class="setting-item">
          <div class="setting-info">
            <div class="setting-name">{{ $t('settings.system.log.audit') }}</div>
            <div class="setting-desc">{{ $t('settings.system.log.audit_desc') }}</div>
          </div>
          <div class="setting-ctrl">
            <label class="toggle"><input type="checkbox" v-model="auditEnabled"><span class="toggle-track"></span></label>
          </div>
        </div>
      </div>
    </div>

    <!-- Security -->
    <div class="card mb-4">
      <div class="card-hd">
        <span class="card-title"><i class="fa-solid fa-shield-halved" style="color:#7c3aed;"></i> {{ $t('settings.system.auth_policy.label') }}</span>
      </div>
      <div class="card-bd pad">
        <div class="form-row">
          <div class="form-group">
            <label class="form-label">{{ $t('settings.system.security.jwt_expiry') }}</label>
            <input type="number" class="form-ctrl" v-model.number="jwtExpiry" min="5">
            <p class="form-hint">{{ $t('settings.system.security.jwt_hint') }}</p>
          </div>
          <div class="form-group">
            <label class="form-label">{{ $t('settings.system.security.rate_limit_login') }}</label>
            <input type="number" class="form-ctrl" v-model.number="rateLimitLogin" min="1">
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label class="form-label">{{ $t('settings.system.security.rate_limit_upload') }}</label>
            <input type="number" class="form-ctrl" v-model.number="rateLimitUpload" min="1">
          </div>
          <div class="form-group">
            <label class="form-label">{{ $t('settings.system.security.cors_origin') }}</label>
            <input type="text" class="form-ctrl" v-model="corsOrigin">
          </div>
        </div>
        <div class="setting-item">
          <div class="setting-info">
            <div class="setting-name">{{ $t('settings.system.security.totp') }}</div>
            <div class="setting-desc">{{ $t('settings.system.security.totp_desc') }}</div>
          </div>
          <div class="setting-ctrl">
            <label class="toggle"><input type="checkbox" v-model="totpEnabled"><span class="toggle-track"></span></label>
          </div>
        </div>
        <div class="setting-item">
          <div class="setting-info">
            <div class="setting-name">{{ $t('settings.system.security.token_blacklist') }}</div>
            <div class="setting-desc">{{ $t('settings.system.security.token_blacklist_desc') }}</div>
          </div>
          <div class="setting-ctrl">
            <label class="toggle"><input type="checkbox" v-model="tokenBlacklist"><span class="toggle-track"></span></label>
          </div>
        </div>
      </div>
    </div>

    <!-- Mail server (TBD) -->
    <div class="card mb-4">
      <div class="card-hd">
        <span class="card-title"><i class="fa-solid fa-envelope" style="color:var(--text-m);"></i> {{ $t('settings.system.mail.label') }}</span>
        <span class="badge badge-yellow"><i class="fa-solid fa-clock"></i> {{ $t('settings.system.mail.tbd') }}</span>
      </div>
      <div class="card-bd pad">
        <div class="alert alert-warning">
          <i class="fa-solid fa-triangle-exclamation"></i>
          {{ $t('settings.system.mail.tbd_note') }}
        </div>
        <div class="form-row" style="opacity:.4; pointer-events:none;">
          <div class="form-group">
            <label class="form-label">{{ $t('settings.system.mail.smtp_host') }}</label>
            <input type="text" class="form-ctrl" placeholder="smtp.example.com" disabled>
          </div>
          <div class="form-group">
            <label class="form-label">{{ $t('settings.system.mail.smtp_port') }}</label>
            <input type="number" class="form-ctrl" value="587" disabled>
          </div>
        </div>
      </div>
    </div>

    <!-- Save -->
    <div class="flex" style="justify-content:flex-end; gap:10px;">
      <button class="btn btn-secondary" @click="resetSettings"><i class="fa-solid fa-rotate-left"></i> {{ $t('common.reset') }}</button>
      <button class="btn btn-primary" @click="saveAll"><i class="fa-solid fa-floppy-disk"></i> {{ $t('common.save') }}</button>
    </div>

    <!-- Storage path change confirmation modal -->
    <div v-if="showStorageConfirm" class="modal-bg" role="dialog" aria-modal="true">
      <div class="modal-box">
        <div class="modal-hd">
          <span class="modal-title">{{ $t('settings.system.storage.confirm_title') }}</span>
        </div>
        <div class="modal-bd">
          <p>{{ $t('settings.system.storage.confirm') }}</p>
        </div>
        <div class="modal-ft">
          <button class="btn btn-secondary" @click="showStorageConfirm = false">{{ $t('common.cancel') }}</button>
          <button class="btn btn-primary" @click="confirmSaveStorage">{{ $t('common.confirm') }}</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue';
import { useI18n } from 'vue-i18n';
import { useSettingsStore } from '../../stores/settings.js';
import { postFormRequest } from '@shared/api';
import { useToast } from '../../../main/components/common/useToast';

const s = useSettingsStore();
const { t } = useI18n();
const { showToast } = useToast();
const storageInput = ref('');
const showStorageConfirm = ref(false);
const pendingSystemPatch = ref(null);
const logRetention = ref(30);
const logLevel = ref('INFO');
const logLevels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'];
const fileLogEnabled = ref(true);
const auditEnabled = ref(true);
const jwtExpiry = ref(30);
const rateLimitLogin = ref(5);
const rateLimitUpload = ref(20);
const corsOrigin = ref('http://localhost:3000');
const totpEnabled = ref(true);
const tokenBlacklist = ref(true);
const sourceMode = ref('remote');

onMounted(async () => {
  try {
    await s.fetchSystemSettings();
    await s.fetchSystemInfo();
    applySystemSettingsToForm();
  } catch (e) {
    showToast(t('common.toast.settings_load_failed'), 'danger');
  }
});

function applySystemSettingsToForm() {
  storageInput.value = s.systemSettings.storage_root ?? '';
  logRetention.value = s.systemSettings.log_retention_days ?? 30;
  logLevel.value = s.systemSettings.log_level ?? 'INFO';
  fileLogEnabled.value = s.systemSettings.file_log !== 'false';
  auditEnabled.value = s.systemSettings.audit !== 'false';
  jwtExpiry.value = s.systemSettings.jwt_expiry_minutes ?? 30;
  rateLimitLogin.value = s.systemSettings.rate_limit_login ?? 5;
  rateLimitUpload.value = s.systemSettings.rate_limit_upload ?? 20;
  corsOrigin.value = s.systemSettings.cors_origin ?? 'http://localhost:3000';
  totpEnabled.value = s.systemSettings.totp !== 'false';
  tokenBlacklist.value = s.systemSettings.token_blacklist !== 'false';
  sourceMode.value = s.systemSettings.source_mode === 'local' ? 'local' : 'remote';
}

function saveStorage() {
  storageInput.value = storageInput.value.trim();
  if (!storageInput.value) return;
  showStorageConfirm.value = true;
}

async function confirmSaveStorage() {
  const patch = pendingSystemPatch.value || { storage_root: storageInput.value.trim() };
  try {
    await s.updateSystemSettings(patch);
    applySystemSettingsToForm();
    showToast(t('common.toast.settings_saved'), 'success');
  } catch (e) {
    showToast(t('common.toast.settings_save_failed'), 'danger');
  } finally {
    pendingSystemPatch.value = null;
    showStorageConfirm.value = false;
  }
}

async function saveLogSettings() {
  await s.updateSystemSettings({
    log_retention_days: logRetention.value,
    log_level: logLevel.value,
  });
}

async function resetSettings() {
  try {
    await s.fetchSystemSettings();
    applySystemSettingsToForm();
    pendingSystemPatch.value = null;
    showStorageConfirm.value = false;
    showToast(t('common.toast.settings_reverted'), 'info');
  } catch (e) {
    showToast(t('common.toast.settings_load_failed'), 'danger');
  }
}

async function openStorageBrowser() {
  const fd = new FormData();
  fd.append('initial_dir', storageInput.value.trim());
  try {
    const res = await postFormRequest('/api/v1/project-settings/pick-folder', fd);
    if (!res.data.cancelled && res.data.path) {
      storageInput.value = res.data.path;
    }
  } catch (e) {
    // Ignore on dialog cancel or error
  }
}

function buildSystemPatch() {
  return {
    log_retention_days: String(logRetention.value),
    log_level: logLevel.value,
    file_log: fileLogEnabled.value ? 'true' : 'false',
    audit: auditEnabled.value ? 'true' : 'false',
    jwt_expiry_minutes: String(jwtExpiry.value),
    rate_limit_login: String(rateLimitLogin.value),
    rate_limit_upload: String(rateLimitUpload.value),
    cors_origin: corsOrigin.value,
    totp: totpEnabled.value ? 'true' : 'false',
    token_blacklist: tokenBlacklist.value ? 'true' : 'false',
    source_mode: sourceMode.value,
  };
}

async function saveAll() {
  const patch = buildSystemPatch();
  const trimmed = storageInput.value.trim();
  if (trimmed && trimmed !== s.systemSettings.storage_root) {
    pendingSystemPatch.value = { ...patch, storage_root: trimmed };
    showStorageConfirm.value = true;
    return;
  }
  try {
    await s.updateSystemSettings(patch);
    applySystemSettingsToForm();
    showToast(t('common.toast.settings_saved'), 'success');
  } catch (e) {
    showToast(t('common.toast.settings_save_failed'), 'danger');
  }
}
</script>
