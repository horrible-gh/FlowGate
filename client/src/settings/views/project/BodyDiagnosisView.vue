<template>
  <div class="card mb-4" data-testid="body-diagnosis">
    <div class="card-hd"><span class="card-title">{{ $t('settings.project.body_diagnosis.title') }}</span></div>
    <div class="card-bd pad">
      <p class="form-hint">{{ $t('settings.project.body_diagnosis.hint') }}</p>
      <div class="flex items-center gap-2" style="flex-wrap:wrap;margin-bottom:12px;">
        <label class="text-sm"><input v-model="scopeKind" type="radio" value="group" data-testid="scope-group" /> {{ $t('settings.project.body_diagnosis.scope_flow') }}</label>
        <select v-model="groupId" class="form-ctrl" style="width:240px;" :disabled="scopeKind !== 'group'" data-testid="scope-flow">
          <option value="">{{ $t('settings.project.body_diagnosis.flow_placeholder') }}</option>
          <option v-for="g in groups" :key="g.group_id" :value="g.group_id">{{ g.group_id }}{{ g.title ? ` · ${g.title}` : '' }}</option>
        </select>
        <label class="text-sm"><input v-model="scopeKind" type="radio" value="project" data-testid="scope-project" /> {{ $t('settings.project.body_diagnosis.scope_project') }}</label>
        <button type="button" class="btn btn-primary" :disabled="!canRun || busy" data-testid="run-diagnosis" @click="diagnose()">
          {{ diagnosing ? $t('settings.project.body_diagnosis.running') : $t('settings.project.body_diagnosis.run') }}
        </button>
      </div>

      <template v-if="result">
        <div class="form-hint" data-testid="diagnosis-root">
          {{ $t('settings.project.body_diagnosis.root_effective', { root: result.storage_root.effective, source: sourceLabel(result.storage_root.source) }) }}
        </div>
        <div v-if="result.summary.storage_root_suspect" class="alert alert-warning" data-testid="diagnosis-suspect">
          {{ $t('settings.project.body_diagnosis.suspect', { n: result.summary.storage_root_suspect, roots: result.summary.suspect_roots.join(', ') }) }}
        </div>
        <div v-if="changed" class="alert alert-info" data-testid="diagnosis-changed">{{ $t('settings.project.body_diagnosis.changed') }}</div>
        <p class="text-sm" data-testid="diagnosis-summary">
          {{ $t('settings.project.body_diagnosis.summary', { checked: result.summary.checked, unreadable: result.summary.unreadable, recoverable: result.summary.recoverable }) }}
        </p>
        <p v-if="!result.items.length" class="form-hint" data-testid="diagnosis-empty">{{ $t('settings.project.body_diagnosis.empty') }}</p>
        <table v-else class="tbl" data-testid="diagnosis-table">
          <thead>
            <tr>
              <th style="width:32px;"><input type="checkbox" :checked="allSelected" :disabled="!selectable.length || busy" data-testid="select-all" @change="toggleAll($event.target.checked)" /></th>
              <th>{{ $t('settings.project.body_diagnosis.col_code') }}</th>
              <th>{{ $t('settings.project.body_diagnosis.col_title') }}</th>
              <th>{{ $t('settings.project.body_diagnosis.col_reason') }}</th>
              <th>{{ $t('settings.project.body_diagnosis.col_method') }}</th>
              <th>{{ $t('settings.project.body_diagnosis.col_loss') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in result.items" :key="item.doc_id" :data-doc="item.doc_id">
              <td><input v-model="selected" type="checkbox" :value="item.doc_id" :disabled="!item.recoverable || busy" /></td>
              <td>{{ item.code }}</td>
              <td>{{ item.title }}</td>
              <td>{{ $t(`settings.project.body_diagnosis.reason.${item.reason}`) }}</td>
              <td>{{ $t(`settings.project.body_diagnosis.method.${item.method}`) }}</td>
              <td>{{ item.body_loss ? $t(`settings.project.body_diagnosis.loss.${item.body_loss}`) : '—' }}</td>
            </tr>
          </tbody>
        </table>
        <div class="flex" style="justify-content:flex-end;gap:10px;margin-top:12px;">
          <button type="button" class="btn btn-primary" :disabled="!selected.length || busy || !resultCurrent" data-testid="recover-selected" @click="recoverSelected">
            {{ recovering ? $t('settings.project.body_diagnosis.recovering') : $t('settings.project.body_diagnosis.recover_selected', { n: selected.length }) }}
          </button>
        </div>
      </template>

      <div v-if="outcome" style="margin-top:12px;" data-testid="recovery-outcome">
        <p class="text-sm">{{ $t('settings.project.body_diagnosis.result_summary', outcome.counts) }}</p>
        <ul>
          <li v-for="row in outcome.results" :key="row.doc_id" :data-status="row.status">
            {{ row.doc_id }} — {{ $t(`settings.project.body_diagnosis.status.${row.status}`) }}<template v-if="row.status === 'recovered' && row.body_loss && row.body_loss !== 'none'">
              · {{ $t(`settings.project.body_diagnosis.loss.${row.body_loss}`) }}</template><template v-if="row.refusal">
              · {{ $t(`settings.project.body_diagnosis.refusal.${row.refusal.error}`) }}</template>
          </li>
        </ul>
      </div>
    </div>
  </div>
</template>

<script setup>
// flowgate.v02.0064 T-B1d (P0006 §6.3): the project settings tab "문서 본문 진단". A Flow or the whole
// project is diagnosed (read only); the person picks recoverable documents and [선택 복구] sends each with
// the method its diagnosis showed (`expected_method`), at most 50 per request. The same scope is diagnosed
// again afterwards. Refused and failed documents are listed as such and never counted as recovered; a
// diagnosis whose fingerprint differs from the previous one of the same scope only says the list changed.
// A response that arrives after the project changed belongs to the old project: every request keeps the
// project and the generation it started in, and one that no longer matches is dropped with its follow-ups.
import { computed, onMounted, ref, watch } from 'vue';
import { getRequest, postRequest } from '@shared/api';
import { useI18n } from 'vue-i18n';
import { useSettingsStore } from '../../stores/settings.js';
import { useToast } from '../../../main/components/common/useToast';

const BULK_MAX = 50;

const { t } = useI18n();
const settings = useSettingsStore();
const { showToast } = useToast();
const projectId = computed(() => settings.currentProjectId);

const groups = ref([]);
const scopeKind = ref('group');
const groupId = ref('');
const result = ref(null);
const lastScope = ref(null);
const lastFingerprint = ref(null);
const changed = ref(false);
const selected = ref([]);
const outcome = ref(null);
const diagnosing = ref(false);
const recovering = ref(false);
let generation = 0;

const busy = computed(() => diagnosing.value || recovering.value);
const resultCurrent = computed(() => !!result.value && result.value.scope?.project_id === projectId.value);
const canRun = computed(() => !!projectId.value && (scopeKind.value === 'project' || !!groupId.value));
const selectable = computed(() => (result.value?.items ?? []).filter((item) => item.recoverable).map((item) => item.doc_id));
const allSelected = computed(() => selectable.value.length > 0 && selectable.value.every((id) => selected.value.includes(id)));

function sourceLabel(source) {
  const key = `settings.project.body_diagnosis.source.${source}`;
  const label = t(key);
  return label === key ? source : label;
}

function toggleAll(checked) {
  selected.value = checked ? [...selectable.value] : [];
}

function currentScope() {
  return scopeKind.value === 'project'
    ? { project_id: projectId.value }
    : { group_id: groupId.value };
}

function startedIn() {
  const gen = generation;
  const project = projectId.value;
  return () => gen === generation && project === projectId.value;
}

async function loadGroups() {
  groups.value = [];
  if (!projectId.value) return;
  const current = startedIn();
  const project = projectId.value;
  try {
    const res = await getRequest('/api/v1/groups', { project_id: project });
    if (current()) groups.value = res.data?.groups ?? [];
  } catch {
    if (current()) groups.value = [];
  }
}

async function diagnose(scope = currentScope()) {
  const current = startedIn();
  const project = projectId.value;
  diagnosing.value = true;
  try {
    const { data } = await getRequest('/api/v1/documents/body-diagnosis', scope);
    if (!current() || data.scope?.project_id !== project) return;
    const key = JSON.stringify(scope);
    changed.value = lastScope.value === key && lastFingerprint.value !== null && lastFingerprint.value !== data.fingerprint;
    lastScope.value = key;
    lastFingerprint.value = data.fingerprint;
    result.value = data;
    selected.value = selected.value.filter((id) => data.items.some((item) => item.doc_id === id && item.recoverable));
  } catch {
    if (current()) showToast(t('settings.project.body_diagnosis.load_failed'), 'danger');
  } finally {
    if (current()) diagnosing.value = false;
  }
}

async function recoverSelected() {
  if (!resultCurrent.value || !selected.value.length) return;
  const current = startedIn();
  const methods = new Map(result.value.items.map((item) => [item.doc_id, item.method]));
  const selections = selected.value.map((docId) => ({ doc_id: docId, expected_method: methods.get(docId) }));
  const scope = JSON.parse(lastScope.value);
  recovering.value = true;
  const results = [];
  try {
    for (let at = 0; at < selections.length && current(); at += BULK_MAX) {
      const { data } = await postRequest('/api/v1/documents/body-recovery', { selections: selections.slice(at, at + BULK_MAX) });
      results.push(...(data.results ?? []));
    }
  } catch {
    if (current()) showToast(t('settings.project.body_diagnosis.recover_failed'), 'danger');
  } finally {
    if (current()) recovering.value = false;
  }
  if (!current()) return;
  const count = (status) => results.filter((row) => row.status === status).length;
  outcome.value = { results, counts: { recovered: count('recovered'), refused: count('refused'), failed: count('failed') } };
  selected.value = [];
  await diagnose(scope);
}

watch(projectId, () => {
  generation += 1;
  diagnosing.value = false;
  recovering.value = false;
  result.value = null;
  outcome.value = null;
  selected.value = [];
  groupId.value = '';
  lastScope.value = null;
  lastFingerprint.value = null;
  changed.value = false;
  void loadGroups();
});
onMounted(loadGroups);

defineExpose({ diagnose, recoverSelected });
</script>
