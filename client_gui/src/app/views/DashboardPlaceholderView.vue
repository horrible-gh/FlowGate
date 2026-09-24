<template>
  <div class="app-placeholder">
    <div class="app-placeholder-card">
      <div class="app-placeholder-badge">FlowGate v0.2</div>
      <h1>/app bootstrap</h1>
      <p>
        이 화면은 T#1 범위의 최소 placeholder입니다. Dashboard 시안 전개는 T#2 이후에
        진행됩니다.
      </p>
      <p v-if="username" class="app-placeholder-user">{{ username }}로 로그인되어 있습니다.</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { getRequest } from '@shared/api'

interface MeResponse {
  username: string
}

const username = ref<string | null>(null)

onMounted(async () => {
  try {
    const response = await getRequest<MeResponse | { data: MeResponse }>('/auth/me')
    const data = response.data
    const payload = data && typeof data === 'object' && 'data' in data ? (data as { data: MeResponse }).data : (data as MeResponse)
    username.value = payload?.username ?? null
  } catch {
    username.value = null
  }
})
</script>

<style scoped>
.app-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  /* App.vue now renders a persistent Topbar (T0009 §8) above every route, so full
     100vh here would push this card below the fold. */
  min-height: calc(100vh - var(--hdr-h, 52px));
  padding: 24px;
  box-sizing: border-box;
}

.app-placeholder-card {
  max-width: 480px;
  text-align: center;
}

.app-placeholder-badge {
  display: inline-block;
  padding: 4px 10px;
  border-radius: 999px;
  background: var(--accent-bg, #eef2ff);
  color: var(--accent, #4338ca);
  font-size: 12px;
  font-weight: 600;
  margin-bottom: 12px;
}

.app-placeholder-user {
  color: var(--text-m, #6b7280);
  font-size: 13px;
}
</style>
