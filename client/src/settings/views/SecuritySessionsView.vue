<template>
  <section class="security-sessions">
    <div class="heading"><div><h1>{{ t('settings.security_sessions.title') }}</h1><p>{{ t('settings.security_sessions.description') }}</p></div><button class="danger" :disabled="busy" @click="revokeOthers">{{ t('settings.security_sessions.revoke_others') }}</button></div>
    <p v-if="error" class="error">{{ error }}</p>
    <div v-if="loading">{{ t('settings.security_sessions.loading') }}</div>
    <div v-else class="sessions">
      <article v-for="session in sessions" :key="session.session_id" class="session">
        <div><strong>{{ session.device_label || t('settings.security_sessions.unknown_device') }}</strong><span v-if="session.is_current" class="badge">{{ t('settings.security_sessions.current') }}</span>
          <div class="meta">{{ session.ip_display || '—' }} · {{ formatDate(session.last_used_at) }}</div></div>
        <button v-if="!session.is_current" :disabled="busy" @click="revoke(session.session_id)">{{ t('settings.security_sessions.sign_out') }}</button>
      </article>
    </div>
  </section>
</template>
<script setup lang="ts">
import { onMounted,ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { deleteRequest,getRequest,postRequest } from '@shared/api'
interface Session {session_id:string;device_label:string|null;ip_display:string|null;created_at:string;last_used_at:string;is_current:boolean}
const {t,locale}=useI18n(); const sessions=ref<Session[]>([]); const loading=ref(true); const busy=ref(false); const error=ref('')
const load=async()=>{loading.value=true;error.value='';try{sessions.value=(await getRequest<{sessions:Session[]}>('/auth/sessions')).data.sessions}catch{error.value=t('settings.security_sessions.failed')}finally{loading.value=false}}
const revoke=async(id:string)=>{busy.value=true;try{await deleteRequest('/auth/sessions/'+encodeURIComponent(id))}catch(e:any){if(e?.response?.status!==404)error.value=t('settings.security_sessions.failed')}finally{busy.value=false;await load()}}
const revokeOthers=async()=>{if(!window.confirm(t('settings.security_sessions.confirm')))return;busy.value=true;try{await postRequest('/auth/sessions/revoke-others',{})}catch{error.value=t('settings.security_sessions.failed')}finally{busy.value=false;await load()}}
const formatDate=(value:string)=>new Intl.DateTimeFormat(locale.value,{dateStyle:'medium',timeStyle:'short'}).format(new Date(value))
onMounted(load)
</script>
<style scoped>
.heading,.session{display:flex;justify-content:space-between;gap:16px;align-items:center}.sessions{display:grid;gap:10px}.session{padding:16px;border:1px solid var(--border-color,#ddd);border-radius:8px}.meta{margin-top:6px;color:#777}.badge{margin-left:8px;padding:2px 7px;border-radius:10px;background:#e8f2ff;color:#1769aa;font-size:12px}.danger{color:#b42318}.error{color:#b42318}
</style>
