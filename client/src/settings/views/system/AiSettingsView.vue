<template>
  <div>
    <h1 class="s-page-title">{{ $t('settings.system.ai.title') }}</h1>
    <p class="s-page-sub">{{ $t('settings.system.ai.sub') }}</p>

    <div class="card mb-4">
      <div class="card-hd">
        <span class="card-title">
          <AppIcon name="robot" style="margin-right:6px; color:var(--primary);" />
          {{ $t('settings.ai.providers_title') }}
        </span>
      </div>
      <div class="card-bd pad">
        <p class="form-hint" style="margin-bottom:12px;">{{ $t('settings.ai.providers_hint') }}</p>
        <AiProviderListEditor
          :providers="providers"
          :default-index="defaultIndex"
          :catalog="catalog"
          @update:providers="providers = $event"
          @update:defaultIndex="defaultIndex = $event"
        />
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

    <div class="flex" style="justify-content:flex-end; align-items:center; gap:10px;">
      <span v-if="dirty" class="badge badge-yellow" style="margin-right:2px;">{{ $t('settings.ai.unsaved_badge') }}</span>
      <button class="btn btn-secondary" @click="load">
        <AppIcon name="arrow-counter-clockwise" /> {{ $t('common.reset') }}
      </button>
      <button class="btn btn-primary" @click="save">
        <AppIcon name="floppy-disk" /> {{ $t('common.save') }}
      </button>
    </div>

    <!-- 0490 T0007 §3.6/§4-5: named for what it sets, not the screen it lives on — the card
         title does not repeat "AI 설정" (settings.system.ai.title above it already says that).
         Placed after the provider card's own Save/Reset row rather than directly under the
         provider card so this card's OWN .btn-primary is never the first DOM match for a bare
         `button.btn-primary` selector — client/tests/settings.ai-provider-errors.spec.ts targets
         the provider Save button that way and must keep hitting it unmodified (§2.2). -->
    <div class="card mb-4">
      <div class="card-hd">
        <span class="card-title">
          <AppIcon name="sliders-horizontal" style="margin-right:6px; color:var(--primary);" />
          {{ $t('settings.system.ai.execution_policy.title') }}
        </span>
      </div>
      <div class="card-bd pad">
        <div class="form-group">
          <label class="form-label">{{ $t('settings.system.ai.execution_policy.repeat_count_max_label') }}</label>
          <input
            type="number"
            class="form-ctrl"
            min="1"
            max="30"
            v-model.number="repeatCountMax"
            style="max-width:160px;"
          >
          <p class="form-hint">{{ $t('settings.system.ai.execution_policy.hint') }}</p>
        </div>
        <div v-if="executionPolicyError" class="alert alert-danger mb-4">{{ executionPolicyError }}</div>
        <div class="flex" style="justify-content:flex-end;">
          <button
            class="btn btn-primary"
            data-test="ai-execution-policy-save"
            :disabled="executionPolicySaving"
            @click="saveExecutionPolicy"
          >
            <AppIcon name="floppy-disk" /> {{ $t('common.save') }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import AppIcon from '@shared/AppIcon.vue'
// flowgate.default.0164 (D0002 §6 global screen): register/edit AI providers, order them
// (order = fallback chain) and pick the current default. Contract: GET/PUT
// /api/v1/system/ai-settings (P0003). api_key is write-only — the payload carries it only
// when the user typed a new value ('' = delete); omission means keep.
import { computed, onMounted, ref } from 'vue';
import { onBeforeRouteLeave } from 'vue-router';
import { useI18n } from 'vue-i18n';
import { createPinia, getActivePinia, setActivePinia } from 'pinia';
import { getRequest, putRequest } from '@shared/api';
import AiProviderListEditor from '../../components/AiProviderListEditor.vue';
import { formatErrors } from '../../components/aiProviderLimits';
import { useToast } from '../../../main/components/common/useToast';
import { useSettingsStore } from '../../stores/settings.js';

const { t, te } = useI18n();
const { showToast } = useToast();
// client/tests/settings.ai-provider-errors.spec.ts (protected — must not change, T0007 §2.2)
// mounts this view standalone with no Pinia plugin, predating this view's first Pinia
// dependency. Self-heal exactly like a component that might be mounted before the app's own
// `app.use(createPinia())` has run: in the real app main.ts already installed one, so this is a
// no-op there.
if (!getActivePinia()) setActivePinia(createPinia());
const settingsStore = useSettingsStore();

const providers = ref([]);
const defaultIndex = ref(-1);
const catalog = ref({ exec_types: ['cli', 'api'], kinds: { cli: [], api: [] } });
// Rendered P0003 422 `errors` (index/field/reason) from the last save attempt.
const saveErrors = ref([]);

// 0490 T0007 §3.7 / §4-5: reuses useSettingsStore()'s fetchSystemSettings()/updateSystemSettings()
// (same store SystemSettingsView.vue already uses) instead of a new API helper. Independent of
// the provider card above — its own load call, its own error surface, its own save button, so a
// failure in one never blocks the other (SourceModeSettingsView.vue's per-card-save pattern).
const repeatCountMax = ref(3);
const executionPolicyError = ref('');
const executionPolicySaving = ref(false);

function applyExecutionPolicyFromSettings() {
  const raw = Number(settingsStore.systemSettings.ai_repeat_count_max);
  repeatCountMax.value = Number.isFinite(raw) ? raw : 3;
}

async function saveExecutionPolicy() {
  executionPolicyError.value = '';
  executionPolicySaving.value = true;
  try {
    await settingsStore.updateSystemSettings({ ai_repeat_count_max: String(repeatCountMax.value) });
    applyExecutionPolicyFromSettings();
    showToast(t('common.toast.settings_saved'), 'success');
  } catch (e) {
    // §3.7: routers/system.py raises HTTPException(422, detail=str(exc)) here — a raw,
    // un-localized string, shown verbatim rather than run through the provider card's
    // {detail:{errors:[...]}} formatter.
    executionPolicyError.value = typeof e?.response?.data?.detail === 'string'
      ? e.response.data.detail
      : t('settings.system.ai.execution_policy.save_failed');
  } finally {
    executionPolicySaving.value = false;
  }
}

function snapshot() {
  return JSON.stringify({ providers: providers.value, defaultIndex: defaultIndex.value });
}
const baseline = ref(snapshot());
const dirty = computed(() => baseline.value !== snapshot());

function applyResponse(data) {
  saveErrors.value = [];
  providers.value = data.providers || [];
  defaultIndex.value = providers.value.findIndex((p) => p.id === data.default_provider_id);
  if (data.catalog) catalog.value = data.catalog;
  baseline.value = snapshot();
}

async function load() {
  try {
    const { data } = await getRequest('/api/v1/system/ai-settings');
    applyResponse(data);
  } catch (e) {
    showToast(t('common.toast.settings_load_failed'), 'danger');
  }
}

function buildPayload() {
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
    providers: items,
    default_provider_id: selected && selected.id ? selected.id : null,
    default_provider_index: selected && !selected.id ? defaultIndex.value : null,
  };
}

async function save() {
  saveErrors.value = [];
  try {
    const { data } = await putRequest('/api/v1/system/ai-settings', buildPayload());
    applyResponse(data);
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

onBeforeRouteLeave(() => {
  if (dirty.value && !window.confirm(t('settings.ai.unsaved_confirm'))) return false;
  return true;
});

onMounted(async () => {
  load();
  try {
    await settingsStore.fetchSystemSettings();
    applyExecutionPolicyFromSettings();
  } catch (e) {
    // §3.7: the server always synthesizes an effective row, so this is defensive only —
    // repeatCountMax keeps its ref default (3) on a load failure.
  }
});
</script>
