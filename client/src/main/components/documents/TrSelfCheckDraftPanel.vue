<template>
  <!-- 0638 T#2: Self-check runs of a TR that is not registered yet. A TR(new) worker token owns
       them (server: /api/v1/self-check/draft); a console user can only watch and cancel them.
       Registering the TR links them to it, after which they leave this list and appear in the
       TR's own TrSelfCheckPanel. Nothing is shown while the group has no such run. -->
  <section v-if="runs.length" class="card mb-4" data-testid="tr-self-check-draft-panel">
    <div class="card-hd"><span class="card-title">Self-check · TR 등록 전</span><span class="text-s text-sm">Advisory · current/non-fixed source</span></div>
    <div class="card-bd pad">
      <p class="form-hint">TR(new) 작업이 TR 문서 등록 전에 실행한 Self-check입니다. 실행은 작업 토큰만 시작할 수 있고, TR이 등록되면 그 TR의 Self-check 이력으로 옮겨집니다.</p>
      <div class="flex" style="gap:8px;margin-top:10px">
        <button class="btn btn-secondary" type="button" :disabled="!active || busy" @click="cancel">Cancel</button>
        <span v-if="active">{{ active.status }}<span v-if="active.cancel_requested"> · cancellation requested</span></span>
      </div>
      <p v-if="error" class="text-s">{{ error }}</p>
      <div style="margin-top:14px">
        <strong>Unregistered runs</strong>
        <select v-model="selectedId" class="form-ctrl" @change="selectRun">
          <option v-for="item in runs" :key="item.self_check_run_id" :value="item.self_check_run_id">{{ item.created_at }} · {{ item.program }} · {{ item.status }}</option>
        </select>
        <TrSelfCheckRunDetail v-if="selected" :run="selected" />
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { getRequest, postRequest } from '@shared/api';
import TrSelfCheckRunDetail, { type SelfCheckRun } from './TrSelfCheckRunDetail.vue';
import { serializedRefresh } from './selfCheckRefresh';

interface SelfCheckListResponse {
  ok: boolean;
  runs: SelfCheckRun[];
}

interface SelfCheckRunResponse extends SelfCheckRun {
  ok: boolean;
}

const props = defineProps<{ groupId: string }>();
const base = '/api/v1/self-check/draft/runs';
const busy = ref(false);
const error = ref('');
const runs = ref<SelfCheckRun[]>([]);
const selectedId = ref('');
const selected = computed(() => runs.value.find((row) => row.self_check_run_id === selectedId.value));
const active = computed(() => runs.value.find((row) => row.status === 'pending' || row.status === 'running'));
let timer: ReturnType<typeof setInterval> | null = null;

// An event arriving while a list GET is in flight must not reuse that GET's (older) response.
const refresh = serializedRefresh(async () => {
  if (!props.groupId) { runs.value = []; return; }
  try {
    const { data } = await getRequest<SelfCheckListResponse>(base, { group_id: props.groupId });
    runs.value = data.runs || [];
    if (!selectedId.value || !runs.value.some((row) => row.self_check_run_id === selectedId.value)) selectedId.value = runs.value[0]?.self_check_run_id || '';
  } catch { /* REST is authoritative; retain last visible result */ }
});

async function cancel() {
  if (!active.value) return;
  busy.value = true; error.value = '';
  try { await postRequest(`${base}/${active.value.self_check_run_id}/cancel`, {}); await refresh(); }
  catch (exc: any) { error.value = exc?.response?.data?.error?.code || 'Could not cancel Self-check'; }
  finally { busy.value = false; }
}

async function selectRun() {
  if (!selectedId.value) return;
  try {
    const { data } = await getRequest<SelfCheckRunResponse>(`${base}/${selectedId.value}`);
    runs.value = runs.value.map((row) => row.self_check_run_id === selectedId.value ? data : row);
  } catch { /* refresh will retry (a run linked to its TR meanwhile reads 404 here) */ }
}

function onUpdate(event: Event) {
  // Both a new/updated draft run and its linking to a TR change this group's list.
  const detail = (event as CustomEvent).detail || {};
  if (detail.group_id === props.groupId) refresh();
}

watch(() => props.groupId, () => { selectedId.value = ''; refresh(); });
onMounted(() => { refresh(); window.addEventListener('fg:self_check_run_updated', onUpdate); timer = setInterval(() => { if (active.value) refresh(); }, 1500); });
onBeforeUnmount(() => { window.removeEventListener('fg:self_check_run_updated', onUpdate); if (timer) clearInterval(timer); });
</script>
