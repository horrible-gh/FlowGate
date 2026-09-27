<template>
  <div class="agents-page">
    <div class="agents-heading"><div><h1 class="s-page-title">Agents</h1><p class="s-page-sub">Manage local and remote execution agents.</p></div><button class="btn btn-primary" data-testid="agent-new" @click="startCreate">New Agent</button></div>
    <div v-if="error" class="alert alert-danger">{{ error }}</div>
    <div class="agents-grid">
      <div class="card agents-list"><div class="card-hd"><span class="card-title">Agent registry</span></div>
        <button v-for="agent in agents" :key="agent.agent_id" class="agent-row" :class="{selected:agent.agent_id===selectedId}" @click="select(agent)"><span><strong>{{ agent.name }}</strong><small>{{ agent.location }} · {{ agent.registration_status }}</small></span><span class="badge" :class="agent.online_status==='online'?'badge-green':'badge-gray'">{{ statusLabel(agent) }}</span></button>
        <p v-if="!loading && !agents.length" class="empty">No agents configured.</p>
      </div>
      <form v-if="draft" class="card agent-detail" @submit.prevent><div class="card-hd"><span class="card-title">{{ creating ? 'New Agent' : draft.name }}</span></div><div class="card-bd pad">
        <div class="form-group"><label class="form-label req">Name</label><input data-testid="agent-name" class="form-ctrl" v-model="draft.name" @blur="saveField('name')" /></div>
        <div class="form-row"><div class="form-group"><label class="form-label">Location</label><select data-testid="agent-location" class="form-ctrl" v-model="draft.location" :disabled="!creating" @change="onLocation"><option value="local">Local</option><option value="remote">Remote</option></select></div>
          <div class="form-group"><label class="form-label">Enabled</label><label class="toggle"><input data-testid="agent-enabled" type="checkbox" v-model="draft.enabled" @change="saveField('enabled')"><span class="toggle-track"></span></label></div></div>
        <div v-if="draft.location==='local'" data-testid="local-fields" class="form-group"><label class="form-label">Executable path</label><input class="form-ctrl" v-model="draft.local_executable_path" @blur="saveField('local_executable_path')" placeholder="/opt/flowgate-agent" /></div>
        <div v-else data-testid="remote-fields" class="form-group"><label class="form-label">Connection mode</label><input class="form-ctrl" value="Agent Pull" disabled /><p class="form-hint">The Remote Agent connects outbound. No Host, IP, Port, or TLS endpoint is configured here.</p></div>
        <fieldset class="capabilities"><legend>Configured capabilities</legend><label v-for="(label,key) in capabilityLabels" :key="key"><input type="checkbox" v-model="draft.configured_capabilities[key]" @change="saveCapabilities"> {{ label }}</label></fieldset>
        <button v-if="creating" class="btn btn-primary" data-testid="agent-create" @click="createAgent">Create Agent</button>
        <template v-else><div class="runtime"><h3>Runtime</h3><dl><dt>Status</dt><dd data-testid="online-status">{{ statusLabel(draft) }}</dd><dt>Last seen</dt><dd>{{ draft.last_seen_at || 'Never' }}</dd><dt>Version</dt><dd>{{ draft.agent_version || '—' }}</dd><dt>OS / Architecture</dt><dd>{{ runtimePlatform }}</dd><dt>Protocol</dt><dd>{{ draft.protocol_version ?? '—' }} · {{ draft.protocol_compatibility }}</dd></dl><p class="form-hint">Remote Agents are Online while a compatible heartbeat is within the server's 90-second TTL.</p></div>
          <div class="effective"><strong>Effective capabilities:</strong> {{ effectiveCapabilities }}</div>
          <div class="sensitive-actions"><h3>Actions</h3><button v-if="draft.location==='remote' && draft.enabled" class="btn btn-secondary" data-testid="issue-enrollment" @click="issueEnrollment">Issue enrollment</button><button v-if="draft.credential_status==='active'" class="btn btn-danger" data-testid="revoke-agent" @click="revokeCredential">Revoke credential</button><button v-if="!draft.has_registration_history" class="btn btn-danger" data-testid="delete-agent" @click="deleteAgent">Delete Agent</button><p v-else data-testid="delete-protected" class="form-hint">Deletion is unavailable because this Agent has registration history.</p></div>
          <div v-if="enrollment" class="alert alert-warning enrollment" data-testid="enrollment-secret"><strong>Enrollment token — shown once</strong><code>{{ enrollment.enrollment_token }}</code><span>Expires {{ enrollment.expires_at }}</span></div>
        </template>
      </div></form>
    </div>
  </div>
</template>
<script setup>
import { computed, onMounted, ref } from 'vue'
import { deleteRequest, getRequest, patchRequest, postRequest } from '@shared/api'
const agents=ref([]),selectedId=ref(null),draft=ref(null),creating=ref(false),loading=ref(false),error=ref(''),enrollment=ref(null)
const capabilityLabels={ai_cli:'AI CLI',ai_api:'AI API',storage:'Storage'}
const blank=()=>({name:'',enabled:true,location:'remote',connection_mode:'agent_pull',local_executable_path:null,configured_capabilities:{ai_cli:false,ai_api:false,storage:false}})
const clone=v=>JSON.parse(JSON.stringify(v))
const runtimePlatform=computed(()=>draft.value?[draft.value.os,draft.value.architecture].filter(Boolean).join(' / ')||'—':'—')
const effectiveCapabilities=computed(()=>draft.value?Object.entries(draft.value.effective_capabilities||{}).filter(([,v])=>v).map(([k])=>capabilityLabels[k]).join(', ')||'None':'None')
function statusLabel(a){return a.location==='local'?'Not applicable':a.online_status==='online'?'Online':'Offline'}
async function load(){loading.value=true;error.value='';try{const r=await getRequest('/api/v1/system/agents');agents.value=r.data.agents||[];if(selectedId.value){const found=agents.value.find(a=>a.agent_id===selectedId.value);if(found)draft.value=clone(found)}}catch(e){error.value='Failed to load Agents.'}finally{loading.value=false}}
function select(a){creating.value=false;enrollment.value=null;selectedId.value=a.agent_id;draft.value=clone(a)}
function startCreate(){creating.value=true;selectedId.value=null;enrollment.value=null;draft.value=blank()}
function onLocation(){draft.value.connection_mode=draft.value.location==='remote'?'agent_pull':null;draft.value.local_executable_path=null}
async function createAgent(){if(!draft.value.name.trim())return;try{const r=await postRequest('/api/v1/system/agents',draft.value);creating.value=false;selectedId.value=r.data.agent_id;await load()}catch(e){error.value='Failed to create Agent.'}}
async function patch(payload){try{const r=await patchRequest('/api/v1/system/agents/'+selectedId.value,payload);draft.value=clone(r.data);const i=agents.value.findIndex(a=>a.agent_id===selectedId.value);if(i>=0)agents.value[i]=clone(r.data)}catch(e){error.value='Failed to save Agent setting.';await load()}}
function saveField(key){if(!creating.value&&selectedId.value)patch({[key]:draft.value[key]})}
function saveCapabilities(){if(!creating.value)patch({configured_capabilities:clone(draft.value.configured_capabilities)})}
async function issueEnrollment(){if(!confirm('Issue a new enrollment token? Any previous pending token will be revoked.'))return;try{const r=await postRequest('/api/v1/system/agents/'+selectedId.value+'/enrollments',{});enrollment.value=r.data;await load()}catch(e){error.value='Failed to issue enrollment.'}}
async function revokeCredential(){if(!confirm('Revoke this Agent credential?'))return;try{await postRequest('/api/v1/system/agents/'+selectedId.value+'/credential/revoke',{});enrollment.value=null;await load()}catch(e){error.value='Failed to revoke credential.'}}
async function deleteAgent(){if(!confirm('Delete this unregistered Agent?'))return;try{await deleteRequest('/api/v1/system/agents/'+selectedId.value);selectedId.value=null;draft.value=null;await load()}catch(e){error.value='Failed to delete Agent.'}}
onMounted(load)
</script>
<style scoped>
.agents-heading{display:flex;align-items:center;justify-content:space-between;margin-bottom:18px}.agents-grid{display:grid;grid-template-columns:minmax(240px,32%) 1fr;gap:16px}.agents-list{overflow:hidden}.agent-row{width:100%;border:0;border-bottom:1px solid var(--border);background:transparent;color:inherit;padding:13px 15px;display:flex;align-items:center;justify-content:space-between;text-align:left;cursor:pointer}.agent-row.selected{background:var(--primary-l,rgba(59,130,246,.1))}.agent-row small{display:block;color:var(--text-m);margin-top:4px;text-transform:capitalize}.empty{padding:18px;color:var(--text-m)}.capabilities{border:1px solid var(--border);border-radius:8px;padding:12px;margin:16px 0}.capabilities label{margin-right:18px}.runtime{margin-top:22px;border-top:1px solid var(--border);padding-top:15px}.runtime dl{display:grid;grid-template-columns:130px 1fr;gap:7px 12px}.runtime dt{color:var(--text-m)}.runtime dd{margin:0}.effective{margin:14px 0}.sensitive-actions{border-top:1px solid var(--border);padding-top:15px}.sensitive-actions .btn{margin-right:8px}.enrollment{margin-top:15px;display:grid;gap:6px}.enrollment code{word-break:break-all;user-select:all}@media(max-width:800px){.agents-grid{grid-template-columns:1fr}}
</style>
