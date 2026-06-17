<template>
  <div>
    <!-- Header -->
    <div class="flex justify-between items-center mb-4" style="margin-bottom:14px;">
      <p class="text-s text-sm">{{ $t('settings.project.messages_view.description') }}</p>
      <button v-if="auth.can('project.settings.edit')" class="btn btn-primary btn-sm" @click="openModal(null)">
        <i class="fa-solid fa-plus"></i> {{ $t('settings.project.messages_view.btn_add') }}
      </button>
    </div>

    <!-- Table Card -->
    <div class="card">
      <div class="card-bd">
        <table class="tbl">
          <thead>
            <tr>
              <th style="width:160px;">{{ $t('settings.project.messages_view.col_doc_type') }}</th>
              <th>{{ $t('settings.project.messages_view.col_message') }}</th>
              <th style="width:110px;">{{ $t('settings.project.messages_view.col_action') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="messages.length === 0">
              <td colspan="3" style="text-align:center;padding:32px;color:var(--text-m);">
                <i class="fa-solid fa-comment-dots" style="font-size:1.5rem;margin-bottom:8px;display:block;opacity:.4;"></i>
                {{ $t('settings.project.messages_view.empty') }}
              </td>
            </tr>
            <tr v-for="m in messages" :key="m.id">
              <td>
                <span class="mono text-xs" style="font-weight:700;color:var(--primary);">{{ docTypeLabel(m.doc_type) }}</span>
              </td>
              <td><span class="text-sm">{{ m.message }}</span></td>
              <td>
                <div class="tbl-actions">
                  <button v-if="auth.can('project.settings.edit')" class="btn btn-secondary btn-sm" @click="openModal(m)">
                    <i class="fa-solid fa-pen"></i>
                  </button>
                  <button
                    v-if="auth.can('project.settings.edit')"
                    class="btn btn-ghost btn-sm"
                    style="color:var(--danger);"
                    @click="askDelete(m.id)"
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

    <!-- Message create/edit modal -->
    <Transition name="modal-fade">
      <div v-if="showModal" class="modal-bg" role="dialog" aria-modal="true">
        <div class="modal-box">
          <div class="modal-hd">
            <span class="modal-title">
              <i :class="editing ? 'fa-solid fa-pen' : 'fa-solid fa-plus'" style="color:var(--primary);"></i>
              {{ editing ? $t('settings.project.messages_view.modal_edit_title') : $t('settings.project.messages_view.modal_add_title') }}
            </span>
            <button class="modal-close" @click="showModal = false"><i class="fa-solid fa-xmark"></i></button>
          </div>
          <div class="modal-bd">
            <div class="form-group">
              <label class="form-label req">{{ $t('settings.project.messages_view.label_doc_type') }}</label>
              <select class="form-ctrl" v-model="form.doc_type">
                <option v-for="opt in docTypeOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
              </select>
            </div>
            <div class="form-group">
              <label class="form-label req">{{ $t('settings.project.messages_view.label_message') }}</label>
              <textarea
                class="form-ctrl"
                rows="3"
                v-model="form.message"
                :placeholder="$t('settings.project.messages_view.placeholder_message')"
              ></textarea>
            </div>
          </div>
          <div class="modal-ft">
            <button class="btn btn-secondary" @click="showModal = false">{{ $t('common.cancel') }}</button>
            <button class="btn btn-primary" :disabled="!form.message.trim()" @click="saveMessage">
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
      :title="$t('settings.project.messages_view.delete_confirm_title')"
      :message="$t('settings.project.messages_view.delete_confirm')"
      :confirm-label="$t('common.delete')"
      danger
      @confirm="doDelete"
    />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import { getRequest, postRequest, patchRequest, deleteRequest } from '@shared/api';
import ConfirmModal from '../../../main/components/ConfirmModal.vue';
import { useToast } from '../../../main/components/common/useToast';
import { useAuthStore } from '../../stores/auth.js';
import { useSettingsStore } from '../../stores/settings.js';

const WILDCARD = '*';

const { t, locale } = useI18n();
const auth = useAuthStore();
const settings = useSettingsStore();
const { showToast } = useToast();

const messages = ref([]);
const docTypes = ref([]);
const showModal = ref(false);
const editing = ref(null);
const form = ref({ doc_type: WILDCARD, message: '' });
const confirmVisible = ref(false);
const pendingDeleteId = ref(null);

// Dropdown options: [All]('*') first, then the project's active document types.
const docTypeOptions = computed(() => [
  { value: WILDCARD, label: t('settings.project.messages_view.all_label') },
  ...docTypes.value.map((dt) => ({ value: dt.code, label: dt.label || dt.code })),
]);

const docTypeLabelMap = computed(() => {
  const map = {};
  for (const dt of docTypes.value) if (dt.code) map[dt.code] = dt.label || dt.code;
  return map;
});

function docTypeLabel(code) {
  if (code === WILDCARD) return t('settings.project.messages_view.all_label');
  return docTypeLabelMap.value[code] || code;
}

async function fetchDocTypes() {
  if (!settings.currentProjectId) return;
  const { data } = await getRequest(`/api/v1/projects/${settings.currentProjectId}/document-types`, { locale: locale.value });
  docTypes.value = (data.data || []).filter((dt) => dt.is_active);
}

async function fetchMessages() {
  if (!settings.currentProjectId) return;
  const { data } = await getRequest(`/api/v1/projects/${settings.currentProjectId}/messages`);
  messages.value = data.data || [];
}

function openModal(m) {
  editing.value = m;
  form.value = m
    ? { doc_type: m.doc_type, message: m.message }
    : { doc_type: WILDCARD, message: '' };
  showModal.value = true;
}

async function saveMessage() {
  const payload = { doc_type: form.value.doc_type, message: form.value.message.trim() };
  if (!payload.message) return;
  try {
    if (editing.value) {
      await patchRequest(`/api/v1/projects/${settings.currentProjectId}/messages/${editing.value.id}`, payload);
    } else {
      await postRequest(`/api/v1/projects/${settings.currentProjectId}/messages`, payload);
    }
  } catch {
    showToast(t('settings.project.messages_view.save_failed'), 'danger');
    return;
  }
  showModal.value = false;
  await fetchMessages();
}

function askDelete(id) {
  pendingDeleteId.value = id;
  confirmVisible.value = true;
}

async function doDelete() {
  const id = pendingDeleteId.value;
  pendingDeleteId.value = null;
  if (id == null) return;
  await deleteRequest(`/api/v1/projects/${settings.currentProjectId}/messages/${id}`);
  await fetchMessages();
}

async function reload() {
  await Promise.all([fetchDocTypes(), fetchMessages()]);
}

watch(() => settings.currentProjectId, reload);
watch(locale, fetchDocTypes);
onMounted(reload);
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
</style>
