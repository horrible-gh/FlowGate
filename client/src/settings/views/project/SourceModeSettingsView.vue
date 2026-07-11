<template>
  <div>
    <div class="card mb-4">
      <div class="card-hd">
        <span class="card-title">{{ $t('settings.project.source_mode.title') }}</span>
      </div>
      <div class="card-bd pad">
        <div class="form-section">
          <div class="form-group">
            <label class="form-label">{{ $t('settings.project.source_mode.effective') }}</label>
            <div>
              <span class="badge" :class="effectiveMode === 'local' ? 'badge-yellow' : 'badge-blue'">
                {{ modeLabel(effectiveMode) }}
              </span>
              <span class="text-s text-sm" style="margin-left:8px;">
                {{ $t('settings.project.source_mode.global_current', { mode: modeLabel(globalMode) }) }}
              </span>
            </div>
          </div>

          <div class="form-group">
            <label class="form-label">{{ $t('settings.project.source_mode.override') }}</label>
            <select class="form-ctrl" v-model="overrideMode" style="max-width:320px;">
              <option value="">{{ $t('settings.project.source_mode.follow_global') }}</option>
              <option value="local">{{ $t('settings.source_mode.local') }}</option>
              <option value="remote">{{ $t('settings.source_mode.remote') }}</option>
            </select>
            <p class="form-hint">{{ $t('settings.project.source_mode.hint') }}</p>
          </div>
        </div>
      </div>
    </div>

    <div class="flex" style="justify-content:flex-end; gap:10px;">
      <button class="btn btn-secondary" @click="fetchMode">
        <AppIcon name="arrow-counter-clockwise" /> {{ $t('common.reset') }}
      </button>
      <button class="btn btn-primary" @click="save">
        <AppIcon name="floppy-disk" /> {{ $t('common.save') }}
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import { getRequest, putRequest } from '@shared/api';
import { useSettingsStore } from '../../stores/settings.js';
import { useToast } from '../../../main/components/common/useToast';
import AppIcon from '@shared/AppIcon.vue';

const { t } = useI18n();
const settings = useSettingsStore();
const { showToast } = useToast();

const overrideMode = ref('');
const globalMode = ref('remote');
const effectiveMode = ref('remote');

const projectId = computed(() => settings.currentProjectId);

function modeLabel(mode) {
  return mode === 'local' ? t('settings.source_mode.local') : t('settings.source_mode.remote');
}

async function fetchMode() {
  if (!projectId.value) return;
  try {
    const { data } = await getRequest(`/api/v1/settings/project/${projectId.value}/mode`);
    overrideMode.value = data.override || '';
    globalMode.value = data.global_mode || 'remote';
    effectiveMode.value = data.effective_mode || globalMode.value;
  } catch (e) {
    showToast(t('common.toast.settings_load_failed'), 'danger');
  }
}

async function save() {
  if (!projectId.value) return;
  try {
    const { data } = await putRequest(`/api/v1/settings/project/${projectId.value}/mode`, {
      override: overrideMode.value || null,
    });
    overrideMode.value = data.override || '';
    globalMode.value = data.global_mode || 'remote';
    effectiveMode.value = data.effective_mode || globalMode.value;
    showToast(t('common.toast.settings_saved'), 'success');
  } catch (e) {
    showToast(t('common.toast.settings_save_failed'), 'danger');
  }
}

watch(projectId, fetchMode);
onMounted(fetchMode);
</script>
