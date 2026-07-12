<template>
  <div>
    <div class="card mb-4">
      <div class="card-hd">
        <span class="card-title">
          <AppIcon name="robot" style="margin-right:6px; color:var(--primary);" />
          {{ $t('settings.project.ai.title') }}
        </span>
        <span class="badge" :class="badgeClass" style="margin-left:10px;">{{ badgeLabel }}</span>
      </div>
      <div class="card-bd pad">
        <div class="form-group">
          <label class="form-label">{{ $t('settings.project.ai.mode_label') }}</label>
          <select class="form-ctrl" v-model="mode" style="max-width:320px;">
            <option value="inherit">{{ $t('settings.project.ai.mode_inherit') }}</option>
            <option value="disabled">{{ $t('settings.project.ai.mode_disabled') }}</option>
            <option value="custom">{{ $t('settings.project.ai.mode_custom') }}</option>
          </select>
        </div>

        <template v-if="mode === 'custom'">
          <p class="form-hint" style="margin-bottom:12px;">{{ $t('settings.project.ai.custom_hint') }}</p>
          <AiProviderListEditor
            :providers="providers"
            :default-index="defaultIndex"
            :catalog="catalog"
            @update:providers="providers = $event"
            @update:defaultIndex="defaultIndex = $event"
          />
        </template>
        <template v-else-if="mode === 'inherit'">
          <p class="form-hint" style="margin-bottom:12px;">{{ $t('settings.project.ai.inherited_title') }}</p>
          <AiProviderListEditor
            v-if="effective.source === 'system'"
            :providers="effectiveProviders"
            :default-index="effectiveDefaultIndex"
            :catalog="catalog"
            readonly
          />
          <p v-else class="form-hint">{{ $t('settings.project.ai.inherit_pending') }}</p>
        </template>
        <p v-else class="form-hint">{{ $t('settings.project.ai.disabled_note') }}</p>
      </div>
    </div>

    <div class="flex" style="justify-content:flex-end; align-items:center; gap:10px;">
      <span v-if="dirty" class="badge badge-yellow" style="margin-right:2px;">{{ $t('settings.ai.unsaved_badge') }}</span>
      <button class="btn btn-secondary" @click="load">
        <AppIcon name="arrow-counter-clockwise" /> {{ $t('common.reset') }}
      </button>
      <button class="btn btn-primary" @click="save">
        <AppIcon name="floppy-disk" /> {{ $t('common.save') }}
      </button>
    </div>
  </div>
</template>

<script setup>
// flowgate.default.0164 (D0002 §6 project tab): tri-state AI settings — inherit the
// global list, explicitly disable AI, or keep a project-only list. Contract: GET/PUT
// /api/v1/projects/{id}/ai-settings (P0003). A mode-only save never destroys the stored
// custom list (L0004 §3), so switching back to custom restores the last list.
import { computed, onMounted, ref, watch } from 'vue';
import { onBeforeRouteLeave } from 'vue-router';
import { useI18n } from 'vue-i18n';
import { getRequest, putRequest } from '@shared/api';
import AiProviderListEditor from '../../components/AiProviderListEditor.vue';
import AppIcon from '@shared/AppIcon.vue';
import { useSettingsStore } from '../../stores/settings.js';
import { useToast } from '../../../main/components/common/useToast';

const { t } = useI18n();
const settings = useSettingsStore();
const { showToast } = useToast();

const mode = ref('inherit');
const savedMode = ref('inherit');
const providers = ref([]);
const defaultIndex = ref(-1);
const effective = ref({ source: 'system', providers: [], default_provider_id: null });
const catalog = ref({ exec_types: ['cli', 'api'], kinds: { cli: [], api: [] } });

function snapshot() {
  return JSON.stringify({ mode: mode.value, providers: providers.value, defaultIndex: defaultIndex.value });
}
const baseline = ref(snapshot());
const dirty = computed(() => baseline.value !== snapshot());

const projectId = computed(() => settings.currentProjectId);
const effectiveProviders = computed(() => effective.value.providers || []);
const effectiveDefaultIndex = computed(() =>
  effectiveProviders.value.findIndex((p) => p.id === effective.value.default_provider_id));

const badgeLabel = computed(() => {
  if (savedMode.value === 'custom') return t('settings.project.ai.badge_custom');
  if (savedMode.value === 'disabled') return t('settings.project.ai.badge_disabled');
  return t('settings.project.ai.badge_inherit');
});
const badgeClass = computed(() => {
  if (savedMode.value === 'custom') return 'badge-green';
  if (savedMode.value === 'disabled') return 'badge-gray';
  return 'badge-blue';
});

function applyResponse(data) {
  mode.value = data.mode || 'inherit';
  savedMode.value = mode.value;
  providers.value = data.providers || [];
  defaultIndex.value = providers.value.findIndex((p) => p.id === data.default_provider_id);
  effective.value = data.effective || { source: 'system', providers: [], default_provider_id: null };
  if (data.catalog) catalog.value = data.catalog;
  baseline.value = snapshot();
}

async function load() {
  if (!projectId.value) return;
  try {
    const { data } = await getRequest(`/api/v1/projects/${projectId.value}/ai-settings`);
    applyResponse(data);
  } catch (e) {
    showToast(t('common.toast.settings_load_failed'), 'danger');
  }
}

function buildPayload() {
  if (mode.value !== 'custom') {
    return { mode: mode.value, providers: null, default_provider_id: null, default_provider_index: null };
  }
  const items = providers.value.map((p) => {
    const item = {
      id: p.id ?? null,
      name: p.name,
      exec_type: p.exec_type,
      kind: p.kind,
      enabled: !!p.enabled,
      cli_command: p.cli_command ?? null,
      api_base_url: p.api_base_url ?? null,
      api_model: p.api_model ?? null,
    };
    if (p.api_key !== undefined) item.api_key = p.api_key;
    return item;
  });
  const selected = defaultIndex.value >= 0 ? providers.value[defaultIndex.value] : null;
  return {
    mode: 'custom',
    providers: items,
    default_provider_id: selected && selected.id ? selected.id : null,
    default_provider_index: selected && !selected.id ? defaultIndex.value : null,
  };
}

async function save() {
  if (!projectId.value) return;
  try {
    const { data } = await putRequest(`/api/v1/projects/${projectId.value}/ai-settings`, buildPayload());
    applyResponse(data);
    showToast(t('common.toast.settings_saved'), 'success');
  } catch (e) {
    if (e?.response?.status === 422) showToast(t('settings.ai.toast_invalid'), 'danger');
    else showToast(t('common.toast.settings_save_failed'), 'danger');
  }
}

onBeforeRouteLeave(() => {
  if (dirty.value && !window.confirm(t('settings.ai.unsaved_confirm'))) return false;
  return true;
});

watch(projectId, load);
onMounted(load);
</script>
