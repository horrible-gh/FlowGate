<template>
  <div>
    <div class="alert alert-info">
      <i class="fa-solid fa-circle-info"></i>
      {{ $t('settings.project.numbering_settings_view.text_5') }}
    </div>

    <!-- Numbering format preview -->
    <div class="card mb-4">
      <div class="card-hd"><span class="card-title">{{ $t('settings.project.numbering_settings_view.card_title_10') }}</span></div>
      <div class="card-bd pad">
        <div class="code-block">{{ numberingPreview }}</div>
        <p class="text-xs text-m" style="margin-top:8px;">{{ $t('settings.project.numbering_settings_view.description_13') }}</p>
      </div>
    </div>

    <!-- Digit count settings -->
    <div class="card mb-4">
      <div class="card-hd"><span class="card-title">{{ $t('settings.project.numbering_settings_view.card_title_19') }}</span></div>
      <div class="card-bd pad">
        <div class="numbering-grid">
          <div class="form-group">
            <label class="form-label req">{{ $t('settings.project.numbering_settings_view.label_23') }}</label>
            <input type="number" class="form-ctrl" v-model.number="form.digits_group" min="1" max="6">
            <p class="form-hint">{{ $t('settings.project.numbering_settings_view.description_25') }}</p>
          </div>
          <div class="form-group">
            <label class="form-label req">{{ $t('settings.project.numbering_settings_view.label_33') }}</label>
            <input type="number" class="form-ctrl" v-model.number="form.digits_type" min="1" max="6">
            <p class="form-hint">{{ $t('settings.project.numbering_settings_view.description_35') }}</p>
          </div>
        </div>
      </div>
    </div>

    <!-- Action buttons -->
    <div class="flex" style="justify-content:flex-end; gap:10px;">
      <button class="btn btn-secondary" @click="resetSettings"><i class="fa-solid fa-rotate-left"></i> {{ $t('common.reset') }}</button>
      <button class="btn btn-primary" @click="save"><i class="fa-solid fa-floppy-disk"></i> {{ $t('common.save') }}</button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import { getRequest, patchRequest } from '@shared/api';
import { useSettingsStore } from '../../stores/settings.js';
import { useToast } from '../../../main/components/common/useToast';

const { t } = useI18n();
const settings = useSettingsStore();
const { showToast } = useToast();
const form = ref({ digits_group: 4, digits_type: 4 });

const numberingPreview = computed(() => {
  const g = '0'.repeat(Math.max(1, form.value.digits_group - 1)) + '1';
  const n = '0'.repeat(Math.max(1, form.value.digits_type - 1)) + '1';
  return `FlowGate.none.${g}.${n}-R`;
});

function normalizeProjectSettings(payload) {
  return payload?.data || payload || {};
}

async function fetchSettings() {
  if (!settings.currentProjectId) return;
  const { data } = await getRequest(`/api/v1/projects/${settings.currentProjectId}/settings`);
  const ps = normalizeProjectSettings(data);
  form.value.digits_group = ps.digits_group ?? 4;
  form.value.digits_type = ps.digits_type ?? 4;
}

async function save() {
  try {
    await patchRequest(`/api/v1/projects/${settings.currentProjectId}/settings`, {
      digits_group: form.value.digits_group,
      digits_type: form.value.digits_type,
    });
    await fetchSettings();
    showToast(t('common.toast.settings_saved'), 'success');
  } catch (e) {
    showToast(t('common.toast.settings_save_failed'), 'danger');
  }
}

async function resetSettings() {
  try {
    await fetchSettings();
    showToast(t('common.toast.settings_reverted'), 'info');
  } catch (e) {
    showToast(t('common.toast.settings_load_failed'), 'danger');
  }
}

watch(() => settings.currentProjectId, fetchSettings);
onMounted(fetchSettings);
</script>

<style scoped>
.numbering-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}

@media (max-width: 720px) {
  .numbering-grid {
    grid-template-columns: 1fr;
  }
}
</style>
