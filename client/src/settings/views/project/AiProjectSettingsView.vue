<template>
  <div class="ai-project-page">
    <section class="card mb-4" data-test="ai-overview">
      <div class="card-hd"><span class="card-title">{{ $t('settings.project.ai_v02.overview') }}</span></div>
      <div class="card-bd pad ai-overview-grid">
        <div v-for="section in overviewSections" :key="section.key" class="ai-overview-item">
          <strong>{{ $t(`settings.project.ai_v02.${section.key}`) }}</strong>
          <span v-if="overview?.[section.key]">{{ section.text }}</span>
          <span v-else>{{ $t('settings.project.ai_v02.section_failed') }}</span>
        </div>
      </div>
    </section>
    <div class="card mb-4" data-test="ai-provider-card">
      <div class="card-hd">
        <span class="card-title">
          <AppIcon name="robot" style="margin-right:6px; color:var(--primary);" />
          {{ $t('settings.project.ai_v02.providers') }}
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
            :build-preset-command="buildPresetCommand"
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
        <div class="flex ai-provider-actions">
          <span v-if="dirty" class="badge badge-yellow">{{ $t('settings.ai.unsaved_badge') }}</span>
          <button class="btn btn-secondary" @click="load"><AppIcon name="arrow-counter-clockwise" /> {{ $t('common.reset') }}</button>
          <button class="btn btn-primary" @click="save"><AppIcon name="floppy-disk" /> {{ $t('common.save') }}</button>
        </div>
      </div>
    </div>

    <div v-if="saveErrors.length" class="alert alert-danger mb-4">
      <div>
        <p style="margin:0 0 6px;">
          <AppIcon name="warning" /> {{ $t('settings.ai.saveerr_title') }}
        </p>
        <ul style="margin:0; padding-left:18px;">
          <li v-for="(msg, i) in saveErrors" :key="i">{{ msg }}</li>
        </ul>
      </div>
    </div>

    <AiEvaluationPanel :project-id="projectId" :providers="effectiveProviders" @changed="loadOverview" />
    <AiGroupsPanel :project-id="projectId" :providers="effectiveProviders" @changed="loadOverview" />
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
import { getRequest, postRequest, putRequest } from '@shared/api';
import AiProviderListEditor from '../../components/AiProviderListEditor.vue';
import AiEvaluationPanel from '../../components/AiEvaluationPanel.vue';
import AiGroupsPanel from '../../components/AiGroupsPanel.vue';
import { formatErrors } from '../../components/aiProviderLimits';
import AppIcon from '@shared/AppIcon.vue';
import { useSettingsStore } from '../../stores/settings.js';
import { useToast } from '../../../main/components/common/useToast';
import { confirm } from '../../../main/composables/useDialogStack';

const { t, te } = useI18n();
const settings = useSettingsStore();
const { showToast } = useToast();

const mode = ref('inherit');
const savedMode = ref('inherit');
const providers = ref([]);
const defaultIndex = ref(-1);
const effective = ref({ source: 'system', providers: [], default_provider_id: null });
const catalog = ref({ exec_types: ['cli', 'api'], kinds: { cli: [], api: [] } });
// Rendered P0003 422 `errors` (index/field/reason) from the last save attempt.
const saveErrors = ref([]);

function snapshot() {
  return JSON.stringify({ mode: mode.value, providers: providers.value, defaultIndex: defaultIndex.value });
}
const baseline = ref(snapshot());
const dirty = computed(() => baseline.value !== snapshot());

const projectId = computed(() => settings.currentProjectId);
const overview = ref(null);
let overviewGeneration = 0;
const overviewSections = computed(() => [
  { key: 'providers', text: overview.value?.providers && `${overview.value.providers.enabled}/${overview.value.providers.total}` },
  { key: 'evaluation', text: overview.value?.evaluation && `${overview.value.evaluation.providers_with_current_result} · ${t('settings.project.ai_v02.running')}: ${overview.value.evaluation.running} · ${t('settings.project.ai_v02.failed')}: ${overview.value.evaluation.failed} · ${t('settings.project.ai_v02.stale')}: ${overview.value.evaluation.stale}` },
  { key: 'ai_groups', text: overview.value?.ai_groups && `${overview.value.ai_groups.total} · ${t('settings.project.ai_v02.invalid')}: ${overview.value.ai_groups.invalid}` },
]);
async function loadOverview() {
  const project = projectId.value;
  const request = ++overviewGeneration;
  if (!project) return;
  try {
    const { data } = await getRequest(`/api/v1/projects/${encodeURIComponent(project)}/ai-overview`);
    if (projectId.value === project && request === overviewGeneration) overview.value = data;
  } catch (e) {
    if (projectId.value === project && request === overviewGeneration) overview.value = null;
  }
}

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

// 0519 T0009: read-only build for the magic tool, scoped to this project's own permission
// and project_id — never the settings-save PUT below.
async function buildPresetCommand({ kind, model_name, skip_permissions }) {
  const { data } = await postRequest(
    `/api/v1/projects/${projectId.value}/ai-settings/cli-preset-command`,
    { kind, model_name, skip_permissions },
  );
  return data;
}

function applyResponse(data) {
  saveErrors.value = [];
  mode.value = data.mode || 'inherit';
  savedMode.value = mode.value;
  providers.value = data.providers || [];
  defaultIndex.value = providers.value.findIndex((p) => p.id === data.default_provider_id);
  effective.value = data.effective || { source: 'system', providers: [], default_provider_id: null };
  if (data.catalog) catalog.value = data.catalog;
  baseline.value = snapshot();
}

let providerRequestGeneration = 0;
async function load() {
  const project = projectId.value;
  const generation = ++providerRequestGeneration;
  if (!project) return;
  try {
    const { data } = await getRequest(`/api/v1/projects/${encodeURIComponent(project)}/ai-settings`);
    if (projectId.value === project && generation === providerRequestGeneration) applyResponse(data);
  } catch (e) {
    if (projectId.value === project && generation === providerRequestGeneration)
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
  saveErrors.value = [];
  try {
    const { data } = await putRequest(`/api/v1/projects/${projectId.value}/ai-settings`, buildPayload());
    applyResponse(data);
    void loadOverview();
    showToast(t('common.toast.settings_saved'), 'success');
  } catch (e) {
    if (e?.response?.status === 422) {
      // The server collects every offending row/field; surface them instead of only the toast.
      saveErrors.value = formatErrors(e.response.data?.detail?.errors, providers.value, { t, te });
      showToast(t('settings.ai.toast_invalid'), 'danger');
    } else {
      showToast(t('common.toast.settings_save_failed'), 'danger');
    }
  }
}

onBeforeRouteLeave(async () => {
  if (dirty.value) {
    const ok = await confirm({ title: t('settings.ai.unsaved_confirm') });
    if (!ok) return false;
  }
  return true;
});

watch(projectId, () => { overview.value = null; void load(); void loadOverview(); });
onMounted(() => { void load(); void loadOverview(); });
</script>

<style scoped>
.ai-project-page { width: 100%; min-width: 0; }
.ai-overview-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; }
.ai-overview-item { display: flex; flex-direction: column; gap: 6px; min-width: 0; overflow-wrap: anywhere; }
.ai-provider-actions { justify-content: flex-end; align-items: center; gap: 10px; margin-top: 16px; flex-wrap: wrap; }
@media (min-width: 2560px) { .ai-project-page { max-width: 1800px; margin-inline: auto; } }
</style>
