<template>
  <div>
    <!-- Info banner -->
    <div class="alert alert-info">
      <i class="fa-solid fa-circle-info"></i>
      {{ $t('settings.project.path_settings_view.text_6') }}
    </div>

    <!-- Default path structure preview -->
    <div class="card mb-4">
      <div class="card-hd"><span class="card-title">{{ $t('settings.project.path_settings_view.card_title_11') }}</span></div>
      <div class="card-bd pad">
        <div class="code-block path-structure">{{ pathStructurePreview }}</div>
      </div>
    </div>

    <!-- Path settings table -->
    <div class="card mb-4">
      <div class="card-hd">
        <span class="card-title">{{ $t('settings.project.path_settings_view.card_title_22', { name: currentProjectName }) }}</span>
        <button class="btn btn-secondary btn-sm" @click="showStorageEdit = !showStorageEdit">
          <i class="fa-solid fa-plus"></i> {{ $t('settings.project.path_settings_view.text_24') }}
        </button>
      </div>
      <div class="card-bd">
        <table class="tbl">
          <thead>
            <tr><th>{{ $t('settings.project.path_settings_view.table_header_30') }}</th><th>{{ $t('settings.project.path') }}</th><th>{{ $t('projects.form_desc') }}</th><th>{{ $t('settings.project.path_settings_view.table_header_30_2') }}</th></tr>
          </thead>
          <tbody>
            <tr>
              <td><span class="badge badge-blue">{{ $t('settings.project.path_settings_view.text_34') }}</span></td>
              <td>
                <span class="mono" style="font-size:.75rem;">
                  {{ storageMode === 'custom' ? form.storage_root_override : systemDefault }}
                </span>
              </td>
              <td class="text-s text-sm">
                {{ storageMode === 'custom' ? $t('settings.project.path_settings_view.text_41') : $t('settings.project.path_settings_view.text_41_2') }}
              </td>
              <td>
                <div class="tbl-actions">
                  <button class="btn btn-secondary btn-sm" @click="showStorageEdit = !showStorageEdit">
                    <i class="fa-solid fa-pen"></i>
                  </button>
                  <button
                    v-if="storageMode === 'custom'"
                    class="btn btn-ghost btn-sm"
                    style="color:var(--danger);"
                    @click="clearStorageOverride"
                  >
                    <i class="fa-solid fa-trash"></i>
                  </button>
                </div>
              </td>
            </tr>
            <tr v-if="showStorageEdit">
              <td colspan="4">
                <div class="form-inline" style="padding:4px 0;">
                  <input
                    v-model="form.storage_root_override"
                    type="text"
                    placeholder="/custom/path"
                    class="form-ctrl"
                    style="max-width:400px;"
                    @input="storageMode = 'custom'"
                  />
                  <button class="btn btn-secondary btn-sm" @click="openStorageBrowser"><i class="fa-solid fa-folder-open"></i> {{ $t('common.browse') }}</button>
                  <button class="btn btn-secondary btn-sm" @click="showStorageEdit = false">{{ $t('settings.numbering.done') }}</button>
                </div>
              </td>
            </tr>
            <tr>
              <td><span class="badge badge-gray">{{ $t('settings.project.path_settings_view.text_76_badge') }}</span></td>
              <td><span class="mono" style="font-size:.75rem;">{{ documentRootPreview }}</span></td>
              <td class="text-s text-sm">{{ $t('settings.project.path_settings_view.text_77') }}</td>
              <td><div class="tbl-actions"><button class="btn btn-secondary btn-sm"><i class="fa-solid fa-pen"></i></button></div></td>
            </tr>
            <tr>
              <td><span class="badge badge-gray">{{ $t('settings.project.path_settings_view.text_82_badge') }}</span></td>
              <td><span class="mono" style="font-size:.75rem;">{{ sourceRootPreview }}</span></td>
              <td class="text-s text-sm">{{ $t('settings.project.path_settings_view.text_83') }}</td>
              <td><div class="tbl-actions"><button class="btn btn-secondary btn-sm"><i class="fa-solid fa-pen"></i></button></div></td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Actions -->
    <div class="flex" style="justify-content:flex-end; gap:10px;">
      <button class="btn btn-secondary" @click="resetSettings">
        <i class="fa-solid fa-rotate-left"></i> {{ $t('common.reset') }}
      </button>
      <button class="btn btn-primary" @click="save">
        <i class="fa-solid fa-floppy-disk"></i> {{ $t('common.save') }}
      </button>
    </div>

    <!-- Storage path change confirmation dialog -->
    <Transition name="modal-fade">
      <StorageMigrateConfirmDialog
        v-if="showConfirmDialog"
        :from-path="originalStoragePath"
        :to-path="pendingStoragePath"
        @confirm="onConfirmMigrate"
        @cancel="showConfirmDialog = false"
      />
    </Transition>

    <!-- Migration progress/result dialog -->
    <Transition name="modal-fade">
      <StorageMigrateProgressDialog
        v-if="showProgressDialog"
        :state="progressState"
        :result="migrateResult"
        :error-message="migrateErrorMessage"
        @close="onProgressClose"
      />
    </Transition>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import { getRequest, patchRequest, postRequest, postFormRequest } from '@shared/api';
import { useSettingsStore } from '../../stores/settings.js';
import { useToast } from '../../../main/components/common/useToast';
import StorageMigrateConfirmDialog from '../../components/StorageMigrateConfirmDialog.vue';
import StorageMigrateProgressDialog from '../../components/StorageMigrateProgressDialog.vue';

const { t } = useI18n();
const settings = useSettingsStore();
const { showToast } = useToast();
const form = ref({ storage_root_override: '' });
const storageMode = ref('');
const showStorageEdit = ref(false);
const originalStoragePath = ref('');
const pendingStoragePath = ref('');
const showConfirmDialog = ref(false);
const showProgressDialog = ref(false);
const progressState = ref('running');
const migrateResult = ref(null);
const migrateErrorMessage = ref('');
const systemDefault = computed(() => settings.systemSettings.storage_root || '/data/flowgate/storage');
const currentProjectName = computed(() => {
  const p = settings.projects.find(p => p.project_id === settings.currentProjectId);
  return p?.project_name || '';
});
const currentBranch = computed(() => 'main');
const effectiveRoot = computed(() => {
  const raw = storageMode.value === 'custom' ? form.value.storage_root_override : systemDefault.value;
  return String(raw || '').replace(/[\\/]+$/, '');
});
const sourceProjectName = computed(() => currentProjectName.value || settings.currentProjectId || '');
const documentRootPreview = computed(() => joinPath(
  effectiveRoot.value,
  'documents',
  settings.currentProjectId || '<project>',
  currentBranch.value,
));
const sourceRootPreview = computed(() => joinPath(
  effectiveRoot.value,
  'src',
  sourceProjectName.value || '<projectName>',
  currentBranch.value,
));
const pathStructurePreview = computed(() => [
  `${documentRootPreview.value}/`,
  '  <module>/<group>/<doc_number>_<filename>',
  `${sourceRootPreview.value}/`,
].join('\n'));

function joinPath(...parts) {
  return parts
    .filter((part) => part !== null && part !== undefined && String(part) !== '')
    .map((part, idx) => {
      const value = String(part).replace(/\\/g, '/');
      if (idx === 0) return value.replace(/\/+$/, '');
      return value.replace(/^\/+|\/+$/g, '');
    })
    .join('/');
}

function normalizeProjectSettings(payload) {
  return payload?.data || payload || {};
}

async function fetchProjectSettings() {
  if (!settings.currentProjectId) return;
  const { data } = await getRequest(`/api/v1/projects/${settings.currentProjectId}/settings`);
  const ps = normalizeProjectSettings(data);
  form.value.storage_root_override = ps.storage_root_override || '';
  storageMode.value = ps.storage_root_override ? 'custom' : '';
  originalStoragePath.value = ps.storage_root_override || '';
}

function _storageChanged() {
  const next = storageMode.value === 'custom' ? (form.value.storage_root_override || '').trim() : '';
  return next !== (originalStoragePath.value || '');
}

async function save() {
  if (_storageChanged()) {
    // Storage path changed — show warning dialog → migration flow
    pendingStoragePath.value = storageMode.value === 'custom' ? (form.value.storage_root_override || '').trim() : '';
    if (!pendingStoragePath.value) {
      // 'custom' disabled (null) — reverting to default path is a simple patch
      await _plainSave();
      return;
    }
    showConfirmDialog.value = true;
    return;
  }
  await _plainSave();
}

async function _plainSave() {
  try {
    await patchRequest(`/api/v1/projects/${settings.currentProjectId}/settings`, {
      storage_root_override: storageMode.value === 'custom' ? form.value.storage_root_override : null,
    });
    await fetchProjectSettings();
    showToast(t('common.toast.settings_saved'), 'success');
  } catch (e) {
    showToast(t('common.toast.settings_save_failed'), 'danger');
  }
}

async function onConfirmMigrate() {
  showConfirmDialog.value = false;
  showProgressDialog.value = true;
  progressState.value = 'running';
  migrateResult.value = null;
  migrateErrorMessage.value = '';
  try {
    const { data } = await postRequest(
      `/api/v1/projects/${settings.currentProjectId}/storage/migrate`,
      { new_root: pendingStoragePath.value, confirm: true },
    );
    const payload = data?.data ?? data;
    migrateResult.value = payload;
    progressState.value = payload?.ok ? 'success' : 'error';
  } catch (e) {
    progressState.value = 'error';
    migrateErrorMessage.value = e?.response?.data?.detail || e?.message || String(e);
  }
}

async function onProgressClose() {
  showProgressDialog.value = false;
  
  // Show toast based on migration result
  if (progressState.value === 'success') {
    showToast(t('settings.project.storage_migrate.toast.success'), 'success');
  } else if (progressState.value === 'error') {
    const errorMessage = migrateErrorMessage.value || t('settings.project.storage_migrate.toast.error');
    showToast(t('settings.project.storage_migrate.toast.error_with_detail', { detail: errorMessage }), 'danger');
  }
  
  await fetchProjectSettings();
}

async function resetSettings() {
  try {
    await fetchProjectSettings();
    showStorageEdit.value = false;
    showToast(t('common.toast.settings_reverted'), 'info');
  } catch (e) {
    showToast(t('common.toast.settings_load_failed'), 'danger');
  }
}

function clearStorageOverride() {
  form.value.storage_root_override = '';
  storageMode.value = '';
  showStorageEdit.value = false;
}

async function openStorageBrowser() {
  const fd = new FormData();
  fd.append('initial_dir', form.value.storage_root_override.trim());
  try {
    const res = await postFormRequest('/api/v1/project-settings/pick-folder', fd);
    if (!res.data.cancelled && res.data.path) {
      form.value.storage_root_override = res.data.path;
      storageMode.value = 'custom';
    }
  } catch (e) {
    // Ignore on dialog cancel or error
  }
}

watch(() => settings.currentProjectId, fetchProjectSettings);
onMounted(fetchProjectSettings);
</script>

<style scoped>
.modal-fade-enter-active,
.modal-fade-leave-active {
  transition: opacity 0.15s ease;
}
.modal-fade-enter-from,
.modal-fade-leave-to {
  opacity: 0;
}

.path-structure {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
