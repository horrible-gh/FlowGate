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
          @update:providers="onUpdateProviders"
          @update:defaultIndex="onUpdateDefaultIndex"
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

    <!-- 0469 T4: the provider card saves itself immediately on every add/edit/delete/move/
         drag/default-select (§4) — no Save/Reset row, no dirty badge, no leave confirmation
         for this card. The execution policy card below is untouched: its own Save button is
         now the only *unconditional* `button.btn-primary` on this view. Opening a provider
         dialog (add/edit) inserts that dialog's own `.btn-primary` earlier in DOM order, which
         is what client/tests/settings.ai-provider-errors.spec.ts relies on to target the
         provider save path instead of the execution-policy one (§2.2/§7). -->
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
import { nextTick, onMounted, ref } from 'vue';
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

function applyResponse(data) {
  saveErrors.value = [];
  providers.value = data.providers || [];
  defaultIndex.value = providers.value.findIndex((p) => p.id === data.default_provider_id);
  if (data.catalog) catalog.value = data.catalog;
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

// 0469 T4 §4: every row operation (add/edit/delete/move/drag/default-select) saves the whole
// list immediately instead of waiting for a Save button. `lastGoodSnapshot` is the state the
// server last confirmed (or the initial load) — a failed save rolls all the way back to it
// rather than to whatever was on screen right before the failing operation, since a
// queued-but-not-yet-sent later operation may already have moved past that point.
let lastGoodSnapshot = snapshot();
let saveScheduled = false;
let saveChain = Promise.resolve();
// Bumped by every local row mutation. A save reads it before its PUT leaves and compares on
// the way back, so a response can tell whether the screen has moved on since it was sent.
let localVersion = 0;
// Client-side identity for a row the server has not issued an id for yet. It outlives the row
// object being replaced by a later edit, so an in-flight save's response can still hand the id
// the server just issued to the right local row.
let localKeySeq = 0;

function snapshot() {
  return JSON.stringify({ providers: providers.value, defaultIndex: defaultIndex.value });
}

// The state the server confirmed, read off its response rather than off the screen — the
// screen may already carry a later operation this response knows nothing about.
function serverSnapshot(data) {
  const list = data.providers || [];
  return JSON.stringify({
    providers: list,
    defaultIndex: list.findIndex((p) => p.id === data.default_provider_id),
  });
}

function tagNewRows(list) {
  list.forEach((p) => {
    if (!p.id && p._localKey === undefined) p._localKey = `lk${(localKeySeq += 1)}`;
  });
}

// A response that lands after the screen has moved on must not overwrite the newer rows, but
// it does carry one thing they cannot reconstruct: the id the server issued to each row that
// save created. The saved list comes back in the order it was sent (the server stores
// sort_order = request index), so the two line up position by position.
function adoptIssuedIds(sentKeys, savedRows) {
  sentKeys.forEach((key, i) => {
    const issued = savedRows[i]?.id;
    if (!key || !issued) return;
    const row = providers.value.find((p) => !p.id && p._localKey === key);
    if (row) row.id = issued;
  });
}

function restoreSnapshot(snap) {
  const parsed = JSON.parse(snap);
  providers.value = parsed.providers;
  defaultIndex.value = parsed.defaultIndex;
}

async function doSave() {
  saveErrors.value = [];
  const sentVersion = localVersion;
  // Only rows the server has never seen need an id handed back to them.
  const sentKeys = providers.value.map((p) => (p.id ? null : p._localKey));
  try {
    const { data } = await putRequest('/api/v1/system/ai-settings', buildPayload());
    if (localVersion === sentVersion) {
      applyResponse(data);
    } else {
      // Another operation edited the list while this PUT was in flight and queued its own save
      // behind this one. Replacing the list with this now-stale response would make the queued
      // save re-send the state we just saved and silently drop that later operation — so keep
      // the local rows and take only the ids this save had issued to them.
      adoptIssuedIds(sentKeys, data.providers || []);
      if (data.catalog) catalog.value = data.catalog;
    }
    lastGoodSnapshot = serverSnapshot(data);
    showToast(t('common.toast.settings_saved'), 'success');
  } catch (e) {
    restoreSnapshot(lastGoodSnapshot);
    if (e?.response?.status === 422) {
      // The server collects every offending row/field; surface them instead of only the toast.
      saveErrors.value = formatErrors(e.response.data?.detail?.errors, providers.value, { t, te });
      showToast(t('settings.ai.toast_invalid'), 'danger');
    } else {
      showToast(t('common.toast.settings_save_failed'), 'danger');
    }
  }
}

// update:providers and update:defaultIndex can both fire in the same synchronous tick (e.g.
// adding a provider that also becomes the new default) — nextTick collapses them into a
// single save. A save already in flight when the next operation arrives is not interrupted:
// the new save is appended to `saveChain` and runs afterwards against the then-current local
// state, which `localVersion` keeps the earlier response from overwriting first.
function scheduleSave() {
  if (saveScheduled) return;
  saveScheduled = true;
  nextTick(() => {
    saveScheduled = false;
    saveChain = saveChain.then(doSave);
  });
}

function onUpdateProviders(next) {
  tagNewRows(next);
  providers.value = next;
  localVersion += 1;
  scheduleSave();
}

function onUpdateDefaultIndex(next) {
  defaultIndex.value = next;
  localVersion += 1;
  scheduleSave();
}

onMounted(async () => {
  await load();
  lastGoodSnapshot = snapshot();
  try {
    await settingsStore.fetchSystemSettings();
    applyExecutionPolicyFromSettings();
  } catch (e) {
    // §3.7: the server always synthesizes an effective row, so this is defensive only —
    // repeatCountMax keeps its ref default (3) on a load failure.
  }
});
</script>
