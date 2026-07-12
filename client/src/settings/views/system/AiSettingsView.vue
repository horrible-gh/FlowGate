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
import AppIcon from '@shared/AppIcon.vue'
// flowgate.default.0164 (D0002 §6 global screen): register/edit AI providers, order them
// (order = fallback chain) and pick the current default. Contract: GET/PUT
// /api/v1/system/ai-settings (P0003). api_key is write-only — the payload carries it only
// when the user typed a new value ('' = delete); omission means keep.
import { computed, onMounted, ref } from 'vue';
import { onBeforeRouteLeave } from 'vue-router';
import { useI18n } from 'vue-i18n';
import { getRequest, putRequest } from '@shared/api';
import AiProviderListEditor from '../../components/AiProviderListEditor.vue';
import { useToast } from '../../../main/components/common/useToast';

const { t } = useI18n();
const { showToast } = useToast();

const providers = ref([]);
const defaultIndex = ref(-1);
const catalog = ref({ exec_types: ['cli', 'api'], kinds: { cli: [], api: [] } });

function snapshot() {
  return JSON.stringify({ providers: providers.value, defaultIndex: defaultIndex.value });
}
const baseline = ref(snapshot());
const dirty = computed(() => baseline.value !== snapshot());

function applyResponse(data) {
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
  try {
    const { data } = await putRequest('/api/v1/system/ai-settings', buildPayload());
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

onMounted(load);
</script>
