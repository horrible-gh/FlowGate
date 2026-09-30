<template>
  <div class="card mb-4">
    <div class="card-hd"><span class="card-title">TR Self-check</span></div>
    <div class="card-bd pad">
      <label class="form-label" for="tr-self-check-enabled">
        <input id="tr-self-check-enabled" v-model="enabled" type="checkbox" :disabled="loading || saving" />
        TR Self-check
      </label>
      <p class="form-hint">TR 작업자가 현재 managed worktree에서 제한된 로컬 검증 커맨드를 실행할 수 있도록 허용</p>
      <p class="form-hint">기본값은 켜짐입니다. 결과는 참고용이며 공식 TS/TSR 판정에 사용되지 않습니다.</p>
      <div class="flex" style="justify-content:flex-end;gap:10px">
        <button type="button" class="btn btn-secondary" :disabled="loading || saving" @click="load">Reset</button>
        <button type="button" class="btn btn-primary" :disabled="loading || saving" @click="save">Save</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue';
import { getRequest, patchRequest } from '@shared/api';
import { useSettingsStore } from '../../stores/settings.js';
import { useToast } from '../../../main/components/common/useToast';

const settings = useSettingsStore();
const { showToast } = useToast();
const projectId = computed(() => settings.currentProjectId);
const enabled = ref(false);
const loading = ref(false);
const saving = ref(false);

async function load() {
  if (!projectId.value) return;
  loading.value = true;
  try {
    const { data } = await getRequest(`/api/v1/projects/${projectId.value}/settings`);
    enabled.value = Boolean((data.data || data).tr_self_check_enabled);
  } catch {
    showToast('Could not load TR Self-check setting', 'danger');
  } finally {
    loading.value = false;
  }
}

async function save() {
  if (!projectId.value) return;
  saving.value = true;
  try {
    const { data } = await patchRequest(`/api/v1/projects/${projectId.value}/settings`, { tr_self_check_enabled: enabled.value });
    enabled.value = Boolean((data.data || data).tr_self_check_enabled);
    showToast('TR Self-check setting saved', 'success');
  } catch {
    showToast('Could not save TR Self-check setting', 'danger');
  } finally {
    saving.value = false;
  }
}

watch(projectId, load);
onMounted(load);
</script>
