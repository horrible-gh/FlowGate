<template>
  <section class="security-sessions">
    <div class="heading"><div><h1>{{ copy.title }}</h1><p>{{ copy.description }}</p></div><button class="danger" :disabled="busy" @click="revokeOthers">{{ copy.revokeOthers }}</button></div>
    <p v-if="error" class="error">{{ error }}</p>
    <div v-if="loading">{{ copy.loading }}</div>
    <div v-else class="sessions">
      <article v-for="session in sessions" :key="session.session_id" class="session">
        <div><strong>{{ session.device_label || copy.unknownDevice }}</strong><span v-if="session.is_current" class="badge">{{ copy.current }}</span>
          <div class="meta">{{ session.ip_display || '—' }} · {{ formatDate(session.last_used_at) }}</div></div>
        <button v-if="!session.is_current" :disabled="busy" @click="revoke(session.session_id)">{{ copy.signOut }}</button>
      </article>
    </div>
  </section>
</template>
<script setup lang="ts">
import { computed,onMounted,ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { deleteRequest,getRequest,postRequest } from '@shared/api'
interface Session {session_id:string;device_label:string|null;ip_display:string|null;created_at:string;last_used_at:string;is_current:boolean}
const {locale}=useI18n(); const sessions=ref<Session[]>([]); const loading=ref(true); const busy=ref(false); const error=ref('')
const messages:any={ko:{title:'보안 및 로그인 세션',description:'현재 로그인된 기기를 확인하고 원격으로 로그아웃할 수 있습니다.',revokeOthers:'다른 모든 세션 로그아웃',unknownDevice:'알 수 없는 기기',current:'현재 세션',signOut:'로그아웃',loading:'세션을 불러오는 중…',confirm:'현재 기기를 제외한 모든 세션에서 로그아웃할까요?',failed:'세션 작업에 실패했습니다.'},en:{title:'Security and login sessions',description:'Review signed-in devices and sign them out remotely.',revokeOthers:'Sign out all other sessions',unknownDevice:'Unknown device',current:'Current session',signOut:'Sign out',loading:'Loading sessions…',confirm:'Sign out all sessions except this device?',failed:'Session operation failed.'},ja:{title:'セキュリティとログインセッション',description:'ログイン中の端末を確認し、リモートでログアウトできます。',revokeOthers:'他のすべてのセッションをログアウト',unknownDevice:'不明なデバイス',current:'現在のセッション',signOut:'ログアウト',loading:'セッションを読み込み中…',confirm:'この端末以外のすべてのセッションをログアウトしますか？',failed:'セッション操作に失敗しました。'}}
const copy=computed(()=>messages[locale.value]||messages.en)
const load=async()=>{loading.value=true;error.value='';try{sessions.value=(await getRequest<{sessions:Session[]}>('/auth/sessions')).data.sessions}catch{error.value=copy.value.failed}finally{loading.value=false}}
const revoke=async(id:string)=>{busy.value=true;try{await deleteRequest('/auth/sessions/'+encodeURIComponent(id))}catch(e:any){if(e?.response?.status!==404)error.value=copy.value.failed}finally{busy.value=false;await load()}}
const revokeOthers=async()=>{if(!window.confirm(copy.value.confirm))return;busy.value=true;try{await postRequest('/auth/sessions/revoke-others',{})}catch{error.value=copy.value.failed}finally{busy.value=false;await load()}}
const formatDate=(value:string)=>new Intl.DateTimeFormat(locale.value,{dateStyle:'medium',timeStyle:'short'}).format(new Date(value))
onMounted(load)
</script>
<style scoped>
.heading,.session{display:flex;justify-content:space-between;gap:16px;align-items:center}.sessions{display:grid;gap:10px}.session{padding:16px;border:1px solid var(--border-color,#ddd);border-radius:8px}.meta{margin-top:6px;color:#777}.badge{margin-left:8px;padding:2px 7px;border-radius:10px;background:#e8f2ff;color:#1769aa;font-size:12px}.danger{color:#b42318}.error{color:#b42318}
</style>
