<template>
  <div>
    <div class="alert alert-info mb-4">
      <AppIcon name="info" />
      {{ $t('settings.project.templates_view.text_5') }}
    </div>

    <!-- Select group structure -->
    <div class="card mb-4">
      <div class="card-hd"><span class="card-title">{{ $t('settings.project.templates_view.card_title_10') }}</span></div>
      <div class="card-bd pad">
        <div style="display:grid; grid-template-columns:1fr 1fr; gap:12px;">
          <label
            v-for="opt in structureOptions"
            :key="opt.value"
            :style="form.group_structure === opt.value
              ? 'display:flex;align-items:flex-start;gap:10px;padding:14px;border:2px solid var(--primary);border-radius:var(--r-lg);cursor:pointer;background:var(--primary-l);'
              : 'display:flex;align-items:flex-start;gap:10px;padding:14px;border:1px solid var(--border);border-radius:var(--r-lg);cursor:pointer;'"
          >
            <input type="radio" name="groupStruct" :value="opt.value" v-model="form.group_structure" style="margin-top:2px;">
            <div>
              <div
                class="fw-6 text-sm"
                :style="form.group_structure === opt.value ? 'color:var(--primary);' : ''"
              >{{ opt.label }}</div>
              <div class="text-xs text-m">{{ opt.sub }}</div>
              <div class="text-xs" style="margin-top:6px; font-family:'JetBrains Mono',monospace; color:var(--text-s);">{{ opt.example }}</div>
            </div>
          </label>
        </div>
      </div>
    </div>

    <!-- Actions -->
    <div class="flex" style="justify-content:flex-end; gap:10px;">
      <button class="btn btn-secondary" @click="resetSettings">
        <AppIcon name="arrow-counter-clockwise" /> {{ $t('common.reset') }}
      </button>
      <button class="btn btn-primary" @click="save">
        <AppIcon name="floppy-disk" /> {{ $t('common.save') }}
      </button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import { getRequest, patchRequest } from '@shared/api';
import { useSettingsStore } from '../../stores/settings.js';
import { useToast } from '../../../main/components/common/useToast';
import AppIcon from '@shared/AppIcon.vue';

const { t } = useI18n();
const settings = useSettingsStore();
const { showToast } = useToast();
const form = ref({ group_structure: 3 });

const structureOptions = computed(() => [
  { value: 3, label: t('settings.project.templates_view.text_52'), sub: t('settings.project.templates_view.text_52_2'), example: 'FlowGate.none.0001.0001-R' },
  { value: 4, label: t('settings.project.templates_view.text_53'), sub: t('settings.project.templates_view.text_53_2'), example: 'FlowGate.none.0001.0001-R' },
  { value: 1, label: t('settings.project.templates_view.text_54'), sub: t('settings.project.templates_view.text_54_2'), example: 'FlowGate-server-0001-001-R0001' },
  { value: 2, label: t('settings.project.templates_view.text_55'), sub: t('settings.project.templates_view.text_55_2'), example: 'FlowGate-server-0001-R0001' },
]);

function normalizeProjectSettings(payload) {
  return payload?.data || payload || {};
}

async function fetchSettings() {
  if (!settings.currentProjectId) return;
  const { data } = await getRequest(`/api/v1/projects/${settings.currentProjectId}/settings`);
  const ps = normalizeProjectSettings(data);
  form.value.group_structure = ps.group_structure ?? 3;
}

async function save() {
  try {
    await patchRequest(`/api/v1/projects/${settings.currentProjectId}/settings`, {
      group_structure: form.value.group_structure,
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
