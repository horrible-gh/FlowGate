<template>
  <div>
    <!-- Test commands (per-project, flowgate.default.0152) -->
    <div class="card mb-4">
      <div class="card-hd">
        <span class="card-title">
          <i class="fa-solid fa-list-check" style="margin-right:6px; color:var(--primary);"></i>
          {{ $t('settings.project.test_recipes.commands_title') }}
        </span>
      </div>
      <div class="card-bd">
        <p class="s-page-sub" style="padding:0 4px 12px;">{{ $t('settings.project.test_recipes.commands_desc') }}</p>
        <table class="tbl">
          <thead>
            <tr>
              <th>{{ $t('settings.project.test_recipes.col_command') }}</th>
              <th style="width:220px;">{{ $t('settings.project.test_recipes.col_description') }}</th>
              <th style="width:110px;">{{ $t('settings.project.test_recipes.col_origin') }}</th>
              <th style="width:140px;">{{ $t('settings.project.test_recipes.col_last_success') }}</th>
              <th style="width:140px;">{{ $t('settings.project.test_recipes.col_updated') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="loading">
              <td colspan="5" class="tr-empty">
                <i class="fa-solid fa-spinner fa-spin tr-empty-icon"></i> {{ $t('common.loading') }}
              </td>
            </tr>
            <tr v-else-if="commands.length === 0">
              <td colspan="5" class="tr-empty">
                <i class="fa-solid fa-list-check tr-empty-icon"></i> {{ $t('settings.project.test_recipes.commands_empty') }}
              </td>
            </tr>
            <template v-else>
              <tr v-for="c in commands" :key="c.id">
                <td><span class="mono tr-mono">{{ c.command }}</span></td>
                <td><span class="text-sm text-s">{{ c.description || '—' }}</span></td>
                <td><span class="badge" :class="originClass(c.origin)">{{ originLabel(c.origin) }}</span></td>
                <td><span class="text-xs text-s">{{ fmt(c.last_success_at) }}</span></td>
                <td><span class="text-xs text-s">{{ fmt(c.updated_at) }}</span></td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Engine recipes (global, flowgate.default.0157) -->
    <div class="card">
      <div class="card-hd">
        <span class="card-title">
          <i class="fa-solid fa-flask" style="margin-right:6px; color:var(--primary);"></i>
          {{ $t('settings.project.test_recipes.recipes_title') }}
        </span>
      </div>
      <div class="card-bd">
        <p class="s-page-sub" style="padding:0 4px 12px;">{{ $t('settings.project.test_recipes.recipes_desc') }}</p>
        <table class="tbl">
          <thead>
            <tr>
              <th style="width:150px;">{{ $t('settings.project.test_recipes.col_engine') }}</th>
              <th>{{ $t('settings.project.test_recipes.col_setup') }}</th>
              <th style="width:150px;">{{ $t('settings.project.test_recipes.col_origin') }}</th>
              <th style="width:170px;">{{ $t('settings.project.test_recipes.col_last_success') }}</th>
              <th style="width:90px;">{{ $t('settings.project.test_recipes.col_used') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="loading">
              <td colspan="5" class="tr-empty">
                <i class="fa-solid fa-spinner fa-spin tr-empty-icon"></i> {{ $t('common.loading') }}
              </td>
            </tr>
            <tr v-else-if="recipes.length === 0">
              <td colspan="5" class="tr-empty">
                <i class="fa-solid fa-flask tr-empty-icon"></i> {{ $t('settings.project.test_recipes.recipes_empty') }}
              </td>
            </tr>
            <template v-else>
              <tr v-for="r in recipes" :key="r.id">
                <td>
                  <span class="mono tr-engine">{{ r.engine }}</span>
                  <span v-if="r.label" class="text-xs text-s tr-label">{{ r.label }}</span>
                </td>
                <td>
                  <code class="tr-code">{{ r.setup || '—' }}</code>
                  <code v-if="r.run_example" class="tr-code tr-run">{{ r.run_example }}</code>
                  <span v-if="r.notes" class="text-xs text-s tr-notes">{{ r.notes }}</span>
                </td>
                <td>
                  <span class="badge" :class="originClass(r.origin)">{{ originLabel(r.origin) }}</span>
                  <span v-if="r.updated_by" class="text-xs text-s tr-by">
                    {{ $t('settings.project.test_recipes.updated_by', { who: updatedByLabel(r.updated_by) }) }}
                  </span>
                </td>
                <td>
                  <span class="text-xs text-s">{{ fmt(r.last_success_at) }}</span>
                  <span v-if="r.last_success_run_id" class="mono text-xs tr-runid">{{ r.last_success_run_id }}</span>
                </td>
                <td>
                  <span v-if="r.used_by_project" class="badge badge-green">{{ $t('settings.project.test_recipes.used_yes') }}</span>
                  <span v-else class="text-xs text-s">{{ $t('settings.project.test_recipes.used_no') }}</span>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
    </div>

    <p class="form-hint" style="margin-top:14px;">
      <i class="fa-solid fa-circle-info" style="margin-right:5px;"></i>
      {{ $t('settings.project.test_recipes.readonly_note') }}
    </p>
  </div>
</template>

<script setup>
// flowgate.default.0157 T0010: read-only visualization of the per-project verified test
// commands (0152) and the global engine recipes (0157) a project's test runs reference.
// The backend read endpoints already exist (project.settings.read RBAC); this view only
// renders them. No edit affordances — the user reacts to anomalies via the review/reject
// flow, per D0002 §6. Data contracts: GET /projects/{id}/test-commands -> { data: [...] },
// GET /projects/{id}/engine-recipes -> { ok, recipes: [...], total } (P0003 §가시화).
import { computed, onMounted, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import { getRequest } from '@shared/api';
import { useSettingsStore } from '../../stores/settings.js';
import { useToast } from '../../../main/components/common/useToast';

const { t } = useI18n();
const settings = useSettingsStore();
const { showToast } = useToast();

const commands = ref([]);
const recipes = ref([]);
const loading = ref(false);

const projectId = computed(() => settings.currentProjectId);

// origin/updated_by vocabularies (P0003): seed·auto·auto-learn·worker·manual. Unknown values
// pass through verbatim so a new origin never renders as a broken i18n key.
const KNOWN_ORIGINS = new Set(['seed', 'auto', 'auto-learn', 'worker', 'manual']);
function originLabel(origin) {
  if (!origin) return '—';
  return KNOWN_ORIGINS.has(origin)
    ? t(`settings.project.test_recipes.origin_${origin.replace('-', '_')}`)
    : origin;
}
function originClass(origin) {
  if (origin === 'seed') return 'badge-blue';
  if (origin === 'auto' || origin === 'auto-learn') return 'badge-green';
  return 'badge-gray';
}
function updatedByLabel(who) {
  if (who === 'seed') return t('settings.project.test_recipes.origin_seed');
  if (who === 'auto-learn') return t('settings.project.test_recipes.origin_auto_learn');
  return who;
}
function fmt(value) {
  if (!value) return '—';
  return String(value).slice(0, 16).replace('T', ' ');
}

async function fetchAll() {
  if (!projectId.value) return;
  loading.value = true;
  try {
    const [tc, er] = await Promise.all([
      getRequest(`/api/v1/projects/${projectId.value}/test-commands`),
      getRequest(`/api/v1/projects/${projectId.value}/engine-recipes`),
    ]);
    commands.value = tc.data?.data ?? [];
    recipes.value = er.data?.recipes ?? [];
  } catch (e) {
    showToast(t('common.toast.settings_load_failed'), 'danger');
  } finally {
    loading.value = false;
  }
}

watch(projectId, fetchAll);
onMounted(fetchAll);
</script>

<style scoped>
.tr-empty {
  text-align: center;
  padding: 36px 16px !important;
  color: var(--text-m);
}
.tr-empty-icon {
  display: block;
  margin-bottom: 10px;
  font-size: 1.4rem;
  opacity: .45;
}
.tr-mono {
  font-size: .82rem;
  font-family: var(--font-mono, monospace);
  color: var(--primary);
  word-break: break-all;
}
.tr-engine {
  display: block;
  font-size: .84rem;
  font-weight: 600;
  color: var(--primary);
}
.tr-label {
  display: block;
  margin-top: 2px;
}
.tr-code {
  display: block;
  font-family: var(--font-mono, monospace);
  font-size: .78rem;
  color: var(--text);
  white-space: pre-wrap;
  word-break: break-all;
}
.tr-run {
  color: var(--text-m);
  margin-top: 3px;
}
.tr-notes {
  display: block;
  margin-top: 5px;
  line-height: 1.45;
}
.tr-by {
  display: block;
  margin-top: 4px;
}
.tr-runid {
  display: block;
  margin-top: 3px;
  color: var(--text-m);
  word-break: break-all;
}
</style>
