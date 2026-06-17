<template>
  <div>
    <!-- Header -->
    <div class="flex justify-between items-center mb-4" style="margin-bottom:14px;">
      <p class="text-s text-sm">{{ $t('settings.project.document_types_view.description_5') }}</p>
      <button v-if="auth.can('project.document_type.create')" class="btn btn-primary btn-sm" @click="openModal(null)">
        <i class="fa-solid fa-plus"></i> {{ $t('settings.project.document_types_view.text_7') }}
      </button>
    </div>

    <!-- Table Card -->
    <div class="card">
      <div class="card-bd">
        <table class="tbl">
          <thead>
            <tr>
              <th style="width:80px;">{{ $t('settings.project.document_types_view.table_header_17') }}</th>
              <th>{{ $t('settings.project.document_types_view.table_header_18') }}</th>
              <th>{{ $t('settings.project.document_types_view.table_header_19') }}</th>
              <th>{{ $t('settings.project.document_types_view.table_header_20') }}</th>
              <th>{{ $t('settings.project.templates') }}</th>
              <th>{{ $t('settings.users.status_filter') }}</th>
              <th>{{ $t('settings.project.document_types_view.table_header_23') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="docTypes.length === 0">
              <td colspan="7" style="text-align:center;padding:32px;color:var(--text-m);">
                <i class="fa-solid fa-tags" style="font-size:1.5rem;margin-bottom:8px;display:block;opacity:.4;"></i>
                {{ $t('settings.project.document_types_view.text_30') }}
              </td>
            </tr>
            <tr v-for="dt in docTypes" :key="dt.id">
              <td>
                <span :style="`display:inline-flex;align-items:center;justify-content:center;padding:3px 8px;border-radius:4px;background:${dt.color || '#64748b'};color:white;font-size:.7rem;font-weight:700;font-family:'JetBrains Mono',monospace;min-width:36px;`">
                  {{ dt.code }}
                </span>
              </td>
              <td>
                <div class="doc-type-name-cell">
                  <span class="fw-5">{{ dt.label }}</span>
                  <span class="doc-type-badges">
                    <span v-if="dt.is_system" class="badge badge-gray">{{ t('common.system') }}</span>
                    <span v-if="!dt.project_id" class="badge badge-blue">{{ t('common.global') }}</span>
                  </span>
                </div>
              </td>
              <td><span class="text-xs text-s">{{ categoryLabel(dt.category) }}</span></td>
              <td>
                <span v-if="dt.color" class="color-swatch" :style="`background:${dt.color};`"></span>
                <span v-else class="text-xs text-m">—</span>
              </td>
              <td>
                <span v-if="dt.template" class="mono text-xs text-s">{{ dt.template }}</span>
                <span v-else class="text-xs text-m">—</span>
              </td>
              <td>
                <label
                  class="toggle"
                  :title="dt.is_system ? t('settings.project.document_types_view.system_type_protected') : ''"
                >
                  <input
                    type="checkbox"
                    :checked="dt.is_active"
                    :disabled="dt.is_system"
                    :aria-label="t('settings.project.document_types_view.toggle_active_aria')"
                    @change="toggleActive(dt)"
                  >
                  <span class="toggle-track"></span>
                </label>
              </td>
              <td>
                <div class="tbl-actions">
                  <button v-if="auth.can('project.document_type.update')" class="btn btn-secondary btn-sm" @click="openModal(dt)">
                    <i class="fa-solid fa-pen"></i>
                  </button>
                  <button
                    v-if="auth.can('project.document_type.delete') && !dt.is_system"
                    class="btn btn-ghost btn-sm"
                    style="color:var(--danger);"
                    @click="deleteType(dt.id)"
                  >
                    <i class="fa-solid fa-trash"></i>
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Type create/edit modal -->
    <Transition name="modal-fade">
      <div v-if="showModal" class="modal-bg" role="dialog" aria-modal="true">
        <div class="modal-box">
          <div class="modal-hd">
            <span class="modal-title">
              <i :class="editing ? 'fa-solid fa-pen' : 'fa-solid fa-plus'" style="color:var(--primary);"></i>
              {{ editing ? $t('settings.project.document_types_view.text_86') : $t('settings.project.document_types_view.text_86_2') }}
            </span>
            <button class="modal-close" @click="showModal = false"><i class="fa-solid fa-xmark"></i></button>
          </div>
          <div class="modal-bd">
            <div class="form-row">
              <div class="form-group">
                <label class="form-label req">{{ $t('settings.project.document_types_view.label_93') }}</label>
                <input class="form-ctrl mono" v-model="form.code" maxlength="5" :placeholder="$t('settings.project.document_types_view.placeholder_94')">
                <p class="form-hint">{{ $t('settings.project.document_types_view.description_95') }}</p>
              </div>
              <div class="form-group">
                <label class="form-label req">{{ $t('settings.project.document_types_view.label_98') }}</label>
                <input class="form-ctrl" v-model="form.label" :placeholder="$t('settings.project.document_types_view.placeholder_99')">
              </div>
            </div>
            <div class="form-row">
              <div class="form-group">
                <label class="form-label req">{{ $t('settings.project.document_types_view.label_104') }}</label>
                <select class="form-ctrl" v-model="form.category">
                  <option v-for="c in categories" :key="c.value" :value="c.value">{{ c.label }}</option>
                </select>
              </div>
              <div class="form-group">
                <label class="form-label">{{ $t('settings.project.document_types_view.label_110') }}</label>
                <input class="form-ctrl" v-model="form.i18n_key" :placeholder="$t('settings.project.document_types_view.placeholder_111')">
                <p class="form-hint">{{ $t('settings.project.document_types_view.description_112') }}</p>
              </div>
            </div>
            <div class="form-group">
              <label class="form-label">{{ $t('settings.project.document_types_view.label_116') }}</label>
              <div class="flex items-center gap-2">
                <input type="color" class="form-ctrl" style="width:60px;height:36px;padding:2px;" v-model="form.color">
                <div
                  :style="`flex:1;height:36px;border-radius:var(--r);background:${form.color || '#64748b'};display:flex;align-items:center;justify-content:center;color:white;font-size:.8rem;font-weight:700;font-family:'JetBrains Mono',monospace;`"
                >{{ form.code || 'CODE' }}</div>
              </div>
            </div>
            <div class="form-group">
              <label class="form-label">{{ $t('settings.project.document_types_view.label_125') }}</label>
              <div style="border:2px dashed var(--border);border-radius:var(--r);padding:16px;text-align:center;cursor:pointer;">
                <i class="fa-solid fa-upload" style="color:var(--text-m);margin-right:6px;"></i>
                <span class="text-sm text-s">{{ $t('settings.project.document_types_view.text_128') }}</span>
              </div>
            </div>
            <p v-if="editing" class="form-hint" style="padding:.5rem .75rem;background:rgba(255,255,255,.02);border-left:3px solid var(--primary);margin-bottom:4px;">
              {{ $t('settings.project.document_types_view.text_132') }}
            </p>
          </div>
          <div class="modal-ft">
            <button class="btn btn-secondary" @click="showModal = false">{{ $t('common.cancel') }}</button>
            <button class="btn btn-primary" @click="saveType">
              <i :class="editing ? 'fa-solid fa-floppy-disk' : 'fa-solid fa-plus'"></i>
              {{ editing ? $t('common.save') : $t('common.add') }}
            </button>
          </div>
        </div>
      </div>
    </Transition>

    <!-- Delete confirmation (shared ConfirmModal, no native confirm()) -->
    <ConfirmModal
      v-model:visible="confirmVisible"
      :title="confirmTitle"
      :message="confirmMessage"
      :confirm-label="confirmLabel || undefined"
      :danger="confirmDanger"
      @confirm="onConfirmAccept"
    />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import { getRequest, postRequest, patchRequest, deleteRequest } from '@shared/api';
import { useToast } from '../../../main/components/common/useToast';
import ConfirmModal from '@main/components/ConfirmModal.vue';
import { useAuthStore } from '../../stores/auth.js';
import { useSettingsStore } from '../../stores/settings.js';

const { t, locale } = useI18n();
const auth = useAuthStore();
const settings = useSettingsStore();
const { showToast } = useToast();
const docTypes = ref([]);
const showModal = ref(false);
const editing = ref(null);

// Custom confirm dialog state (replaces native confirm())
const confirmVisible = ref(false);
const confirmTitle = ref('');
const confirmMessage = ref('');
const confirmLabel = ref('');
const confirmDanger = ref(false);
let confirmAction = null;
function askConfirm(opts) {
  confirmTitle.value = opts.title;
  confirmMessage.value = opts.message;
  confirmLabel.value = opts.confirmLabel || '';
  confirmDanger.value = !!opts.danger;
  confirmAction = opts.action;
  confirmVisible.value = true;
}
function onConfirmAccept() {
  const action = confirmAction;
  confirmAction = null;
  if (action) action();
}

const categories = computed(() => [
  { value: 'general', label: t('settings.project.document_types_view.text_162') },
  { value: 'instruction',  label: t('settings.project.document_types_view.text_163') },
  { value: 'design',       label: t('settings.project.document_types_view.text_164') },
  { value: 'work',         label: t('settings.project.document_types_view.text_165') },
  { value: 'action',       label: t('settings.project.document_types_view.text_166') },
]);

const form = ref({ code: '', label: '', i18n_key: '', category: 'general', color: '#2563eb' });

function categoryLabel(value) {
  return categories.value.find((category) => category.value === value)?.label || value || '—';
}

async function fetchTypes() {
  if (!settings.currentProjectId) return;
  const { data } = await getRequest(`/api/v1/projects/${settings.currentProjectId}/document-types`, { locale: locale.value });
  docTypes.value = data.data || [];
}

function openModal(dt) {
  editing.value = dt;
  if (dt) {
    form.value = { code: dt.code, label: dt.label, i18n_key: dt.i18n_key, category: dt.category, color: dt.color || '#64748b' };
  } else {
    form.value = { code: '', label: '', i18n_key: '', category: 'general', color: '#2563eb' };
  }
  showModal.value = true;
}

async function saveType() {
  if (editing.value) {
    await patchRequest(`/api/v1/projects/${settings.currentProjectId}/document-types/${editing.value.id}`, form.value);
  } else {
    await postRequest(`/api/v1/projects/${settings.currentProjectId}/document-types`, form.value);
  }
  showModal.value = false;
  await fetchTypes();
}

async function toggleActive(dt) {
  if (dt.is_system) return;
  await patchRequest(`/api/v1/projects/${settings.currentProjectId}/document-types/${dt.id}`, { is_active: !dt.is_active });
  const msg = !dt.is_active
    ? t('settings.project.document_types_view.toggle_active_success')
    : t('settings.project.document_types_view.toggle_inactive_success');
  showToast(msg, 'success');
  await fetchTypes();
}

function deleteType(id) {
  askConfirm({
    title: t('settings.project.document_types_view.delete_confirm_title'),
    message: t('settings.project.document_types_view.delete_confirm'),
    confirmLabel: t('common.delete'),
    danger: true,
    action: async () => {
      await deleteRequest(`/api/v1/projects/${settings.currentProjectId}/document-types/${id}`);
      await fetchTypes();
    },
  });
}

watch(() => settings.currentProjectId, fetchTypes);
watch(locale, fetchTypes);
onMounted(fetchTypes);
</script>

<style scoped>
.doc-type-name-cell {
  display: flex;
  flex-direction: column;
  gap: 4px;
  align-items: flex-start;
}

.doc-type-badges {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.doc-type-badges .badge {
  font-size: .6rem;
}

.modal-fade-enter-active,
.modal-fade-leave-active {
  transition: opacity 0.15s ease;
}
.modal-fade-enter-from,
.modal-fade-leave-to {
  opacity: 0;
}
</style>

