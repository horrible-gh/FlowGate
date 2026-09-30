<template>
  <section class="card mb-4" data-testid="tr-self-check-panel">
    <div class="card-hd"><span class="card-title">Self-check</span><span class="text-s text-sm">Advisory · current/non-fixed source</span></div>
    <div class="card-bd pad">
      <p v-if="!enabled" class="form-hint">Project Settings에서 TR Self-check를 활성화할 수 있습니다.</p>
      <div class="form-section">
        <label class="form-label">Program <input v-model="program" class="form-ctrl" :disabled="!enabled || busy" placeholder="pytest" /></label>
        <label class="form-label">Args <input v-model="argsText" class="form-ctrl" :disabled="!enabled || busy" placeholder="server/tests/test_x.py -q" /></label>
        <label class="form-label">CWD <input v-model="cwd" class="form-ctrl" :disabled="!enabled || busy" placeholder="." /></label>
        <label class="form-label">Timeout (seconds) <input v-model.number="timeout" type="number" min="1" max="1800" class="form-ctrl" :disabled="!enabled || busy" /></label>
      </div>
      <div class="flex" style="gap:8px;margin-top:10px">
        <button class="btn btn-primary" type="button" :disabled="!enabled || busy || !!active || !program.trim()" @click="run">Run</button>
        <button class="btn btn-secondary" type="button" :disabled="!active" @click="cancel">Cancel</button>
        <span v-if="active">{{ active.status }}<span v-if="active.cancel_requested"> · cancellation requested</span></span>
      </div>
      <p v-if="error" class="text-s">{{ error }}</p>
      <div v-if="runs.length" style="margin-top:14px">
        <strong>Recent history</strong>
        <select v-model="selectedId" class="form-ctrl" @change="selectRun">
          <option v-for="item in runs" :key="item.self_check_run_id" :value="item.self_check_run_id">{{ item.created_at }} · {{ item.program }} · {{ item.status }}</option>
        </select>
        <div v-if="selected">
          <p>Status: {{ selected.status }} · Exit code: {{ selected.exit_code ?? '—' }} · Timed out: {{ selected.timed_out ? 'yes' : 'no' }}</p>
          <p v-if="selected.source_changed_during_run || selected.worktree_state_changed" class="alert alert-warning">Source or worktree state changed during this run.</p>
          <p v-if="selected.error_code">{{ selected.error_code }}</p>
          <label class="form-label">stdout</label><pre class="code-block">{{ selected.stdout_tail || '' }}</pre>
          <label class="form-label">stderr</label><pre class="code-block">{{ selected.stderr_tail || '' }}</pre>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { getRequest, postRequest } from '@shared/api';
import type { Tab } from '../../stores/tabs';

interface SelfCheckRun {
  self_check_run_id: string;
  status: string;
  created_at?: string | null;
  program?: string | null;
  cancel_requested?: boolean;
  exit_code?: number | null;
  timed_out?: boolean;
  stdout_tail?: string | null;
  stderr_tail?: string | null;
  source_changed_during_run?: boolean;
  worktree_state_changed?: boolean;
  error_code?: string | null;
}

interface SelfCheckListResponse {
  ok: boolean;
  runs: SelfCheckRun[];
}

interface SelfCheckRunResponse extends SelfCheckRun {
  ok: boolean;
}

interface ProjectSettingsResponse {
  tr_self_check_enabled?: boolean;
}

const props = defineProps<{ tab: Tab }>();
const enabled = ref(false);
const program = ref('pytest');
const argsText = ref('');
const cwd = ref('.');
const timeout = ref(300);
const busy = ref(false);
const error = ref('');
const runs = ref<SelfCheckRun[]>([]);
const selectedId = ref('');
const selected = computed(() => runs.value.find((row) => row.self_check_run_id === selectedId.value));
const active = computed(() => runs.value.find((row) => row.status === 'pending' || row.status === 'running'));
const base = computed(() => `/api/v1/documents/${props.tab.id}/self-check/runs`);
let timer: ReturnType<typeof setInterval> | null = null;

async function refresh() {
  try {
    const { data } = await getRequest<SelfCheckListResponse>(base.value);
    runs.value = data.runs || [];
    if (!selectedId.value || !runs.value.some((row) => row.self_check_run_id === selectedId.value)) selectedId.value = runs.value[0]?.self_check_run_id || '';
  } catch { /* REST is authoritative; retain last visible result */ }
}

async function load() {
  if (props.tab.projectId) {
    try {
      const { data } = await getRequest<ProjectSettingsResponse>(`/api/v1/projects/${props.tab.projectId}/settings`);
      enabled.value = Boolean(data.tr_self_check_enabled);
    } catch { enabled.value = false; }
  }
  await refresh();
}

function parseArgs(text: string): string[] {
  // This is a convenience tokenizer for the UI. The server receives an argv array, never a command string.
  return text.match(/(?:[^\s"']+|"[^"]*"|'[^']*')+/g)?.map((value) => value.replace(/^(["'])(.*)\1$/, '$2')) || [];
}

async function run() {
  busy.value = true; error.value = '';
  try {
    const { data } = await postRequest<SelfCheckRunResponse>(base.value, { program: program.value.trim(), args: parseArgs(argsText.value), cwd: cwd.value, timeout_seconds: timeout.value });
    selectedId.value = data.self_check_run_id;
    await refresh();
  } catch (exc: any) { error.value = exc?.response?.data?.error?.code || 'Could not start Self-check'; }
  finally { busy.value = false; }
}

async function cancel() {
  if (!active.value) return;
  try { await postRequest(`${base.value}/${active.value.self_check_run_id}/cancel`, {}); await refresh(); }
  catch (exc: any) { error.value = exc?.response?.data?.error?.code || 'Could not cancel Self-check'; }
}

async function selectRun() {
  if (!selectedId.value) return;
  try {
    const { data } = await getRequest<SelfCheckRunResponse>(`${base.value}/${selectedId.value}`);
    runs.value = runs.value.map((row) => row.self_check_run_id === selectedId.value ? data : row);
  } catch { /* refresh will retry */ }
}

function onUpdate(event: Event) {
  const detail = (event as CustomEvent).detail || {};
  if (detail.tr_doc_id === props.tab.id) refresh();
}

watch(() => props.tab.id, load);
onMounted(() => { load(); window.addEventListener('fg:self_check_run_updated', onUpdate); timer = setInterval(() => { if (active.value) refresh(); }, 1500); });
onBeforeUnmount(() => { window.removeEventListener('fg:self_check_run_updated', onUpdate); if (timer) clearInterval(timer); });
</script>
