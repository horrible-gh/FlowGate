<template>
  <SettingsShell :scope="personal ? 'me' : 'system'">
    <template #nav><SettingsSubnav :items="personal ? meNav : systemNav" active="agents" /></template>
    <section :data-panel="personal ? 'personal-agents' : 'system-agents'">
      <header class="agent-page-head">
        <h2>{{ t(personal ? 'gui.agent_screen.title' : 'gui.settings.system_nav.agents') }}</h2>
        <Button variant="primary" :disabled="busy || !agents" data-testid="agent-new" @click="startCreate">
          <SettingsIcon name="plus" size="sm" />{{ t('gui.agent_screen.register') }}
        </Button>
      </header>
      <section class="fgs-panel">
        <div class="fgs-phead"><h3>{{ t(personal ? 'gui.agent_screen.list_title' : 'gui.agent_screen.system_list_title') }}</h3></div>
        <div class="fgs-panel-body">
          <LoadingState v-if="loading && !agents" :label="t('gui.common.loading')" />
          <ErrorState v-else-if="listError && !agents" :title="t('gui.settings.agents.errors.load_failed')" :message="listError" data-testid="agents-error">
            <template #action><Button variant="secondary" data-testid="agents-retry" @click="load">{{ t('gui.common.retry') }}</Button></template>
          </ErrorState>
          <template v-else-if="agents">
            <ErrorState v-if="listError" compact :title="t('gui.settings.agents.errors.refresh_failed')" :message="listError" data-testid="agents-refresh-error">
              <template #action><Button variant="secondary" :disabled="loading" data-testid="agents-refresh-retry" @click="load">{{ t('gui.common.retry') }}</Button></template>
            </ErrorState>
            <p v-if="actionError && !selected" class="fgs-error" role="alert" data-testid="agent-action-error">{{ actionError }}</p>
            <div class="agent-list" data-testid="agent-list">
              <div v-for="row in agents" :key="row.agent_id" class="agent-row" :data-agent="row.agent_id">
                <span class="agent-symbol" aria-hidden="true"><SettingsIcon name="bot" /></span>
                <div class="agent-info">
                  <div class="agent-title"><strong>{{ row.name }}</strong><span class="fgs-state" :class="STATE_TONE[agentState(row)]" :data-runtime="agentState(row)">{{ stateLabel(row) }}</span></div>
                  <p :title="row.last_seen_at ?? undefined">{{ t('gui.agent_screen.last_seen') }} {{ row.last_seen_at ? ago(row.last_seen_at) : t('gui.agent_screen.before_connection') }}</p>
                </div>
                <div class="agent-actions">
                  <Button variant="ghost" class="agent-icon" :disabled="busy" :title="t('gui.agent_screen.edit')" :aria-label="t('gui.agent_screen.edit_name', { name: row.name })" :data-edit-agent="row.agent_id" @click="select(row)"><SettingsIcon name="edit" size="sm" /></Button>
                  <span :title="row.has_registration_history ? t('gui.settings.agents.delete_protected_short') : t('gui.settings.agents.delete_short')">
                    <Button variant="ghost" class="agent-icon" :disabled="busy || row.has_registration_history" :aria-label="t('gui.agent_screen.delete_name', { name: row.name })" :data-delete-agent="row.agent_id" @click="askDelete(row)"><SettingsIcon name="trash" size="sm" /></Button>
                  </span>
                </div>
              </div>
              <div v-if="!agents.length" class="fgs-empty" data-testid="agents-empty">{{ t('gui.settings.agents.empty') }}</div>
            </div>
          </template>
        </div>
      </section>
    </section>

    <ModalShell :open="mode === 'create'" surface-class="agent-registration-drawer fgs-drawer narrow" close-on-backdrop :aria-label="t('gui.settings.agents.create')" @close="closeEditor">
      <template #header="{ titleId }">
          <div class="fgs-d-head">
            <div>
              <h2 :id="titleId">{{ t('gui.settings.agents.create') }}</h2>
              <p>{{ t('gui.settings.agents.create_sub') }}</p>
            </div>
            <button type="button" class="fgs-iconbtn" :title="t('gui.common.close')" :aria-label="t('gui.common.close')" :disabled="busy" data-testid="agent-editor-close" @click="closeEditor">
              <SettingsIcon name="x" />
            </button>
          </div>
      </template>
          <form class="fgs-d-main" data-testid="agent-create-form" @submit.prevent="submitCreate">
            <div class="fgs-d-body">
              <div class="fgs-formfield">
                <label for="agent-new-name">{{ t('gui.settings.agents.fields.name') }}</label>
                <input id="agent-new-name" v-model="form.name" class="fgs-input" maxlength="100" autocomplete="off" data-modal-autofocus :disabled="busy" placeholder="debian-dev-02" data-testid="agent-name" />
                <span v-if="form.name && createNameProblem" class="fgs-error" data-testid="agent-name-invalid">{{ createNameProblem }}</span>
                <span v-else class="fgs-hintline">{{ t('gui.settings.agents.name_hint', { max: NAME_MAX }) }}</span>
              </div>
              <div class="fgs-formfield">
                <span class="label">{{ t('gui.settings.agents.create_location') }}</span>
                <div class="fgs-segment" role="group" :aria-label="t('gui.settings.agents.create_location')" data-testid="agent-location">
                  <button v-for="loc in LOCATIONS" :key="loc" type="button" :class="{ on: form.location === loc }" :aria-pressed="form.location === loc ? 'true' : 'false'" :disabled="busy" :data-location="loc" @click="form.location = loc">
                    {{ t(`gui.settings.agents.location_option.${loc}`) }}
                  </button>
                </div>
                <span v-if="form.location === 'remote'" class="fgs-hintline" data-testid="agent-remote-fields">{{ t('gui.settings.agents.fields.connection') }} {{ t('gui.settings.agents.connection.agent_pull') }} · {{ t('gui.settings.agents.connection_hint') }}</span>
                <span v-else class="fgs-hintline">{{ t('gui.settings.agents.local_hint') }}</span>
                <span class="fgs-hintline">{{ t('gui.settings.agents.location_fixed') }}</span>
              </div>
              <div v-if="form.location === 'local'" class="fgs-formfield" data-testid="agent-local-fields">
                <label for="agent-new-path">{{ t('gui.settings.agents.fields.path') }}</label>
                <input id="agent-new-path" v-model="form.path" class="fgs-input mono" autocomplete="off" :disabled="busy" placeholder="/opt/flowgate-agent" data-testid="agent-path" />
              </div>
              <div class="fgs-formfield">
                <span class="label">{{ t('gui.settings.agents.create_caps') }}</span>
                <div class="fgs-checks">
                  <label v-for="cap in createCaps" :key="cap" class="fgs-check-row">
                    <input v-model="form.caps[cap]" type="checkbox" :disabled="busy" :data-cap="cap" />
                    <span>
                      <strong>{{ t(`gui.settings.agents.caps.${cap}`) }}</strong>
                      <small>{{ t(`gui.settings.agents.caps_desc.${cap}`) }}</small>
                    </span>
                  </label>
                </div>
                <span class="fgs-hintline">{{ t('gui.settings.agents.caps_hint') }} {{ t('gui.settings.agents.storage_hint') }}</span>
              </div>
              <div class="fgs-formfield">
                <div class="fgs-checks">
                  <label class="fgs-check-row">
                    <input v-model="form.enabled" type="checkbox" :disabled="busy" data-testid="agent-enabled" />
                    <span><strong>{{ t('gui.settings.agents.create_enabled') }}</strong></span>
                  </label>
                </div>
              </div>
              <p v-if="formError" class="fgs-error" role="alert" data-testid="agent-form-error">{{ formError }}</p>
            </div>
            <div class="fgs-d-foot">
              <div class="l" />
              <div class="r">
                <button type="button" class="fgs-btn ghost" :disabled="busy" data-testid="agent-create-cancel" @click="closeEditor">{{ t('gui.common.cancel') }}</button>
                <button type="button" class="fgs-btn pri" :disabled="!createCanSave" data-testid="agent-create-submit" @click="submitCreate">{{ busy ? t('gui.settings.saving') : t('gui.settings.agents.create_submit') }}</button>
              </div>
            </div>
          </form>
    </ModalShell>
    <AgentEditDialog v-if="selected" :creating="false" :agent="selected" :name="mode === 'create' ? form.name : edit.name" :workdir="edit.workdir" :caps="mode === 'create' ? form.caps : edit.caps" :busy="busy" :can-save="mode === 'create' ? createCanSave : editCanSave" :name-problem="mode === 'create' ? createNameProblem : editNameProblem" :error="mode === 'create' ? formError : actionError" :saved="editSaved" :state-label="selected ? stateLabel(selected) : ''" @update:name="setName" @update:workdir="setWorkdir" @update:cap="setCap" @submit="mode === 'create' ? submitCreate() : saveEdit()" @close="closeEditor" />
    <AgentEnrollmentDialog v-if="enrollmentAgent" :agent="enrollmentAgent" :enrollment="enrollment" :busy="busy" :error="enrollmentError" :refresh-error="listError" :state-label="stateLabel(enrollmentAgent)" :expired="enrollmentExpired" @retry="retryEnrollment" @refresh="load" @close="closeEnrollment" @copied="onCopied" />
    <ModalShell :open="!!pending" size="sm" :aria-label="pending?.title" @close="closePending">
      <div v-if="pending" class="confirm" data-testid="agent-confirm" :data-action="pending.kind">
        <div class="conf-ico" :class="pending.tone"><SettingsIcon :name="pending.icon" size="xl" /></div>
        <div class="conf-who">{{ pending.title }}</div>
        <ul class="effects">
          <li v-for="effect in pending.effects" :key="effect">{{ effect }}</li>
        </ul>
      </div>
      <template #footer>
        <div class="fgs-modal-actions">
          <button type="button" class="fgs-btn" :disabled="busy" data-testid="agent-confirm-cancel" @click="closePending">{{ t('gui.common.cancel') }}</button>
          <button type="button" class="fgs-btn" :class="pending?.tone === 'danger' ? 'danger' : 'pri'" :disabled="busy" data-testid="agent-confirm-ok" @click="runPending">{{ pending?.label }}</button>
        </div>
      </template>
    </ModalShell>

    <!-- 미저장 이탈 보호: 계속 편집하면 이동 취소, 요청 0건 -->
    <ModalShell :open="leaveOpen" size="sm" :aria-label="t('gui.settings.general.leave.title')" @close="answerLeave(false)">
      <div class="confirm" data-testid="leave-confirm">
        <div class="conf-ico warn"><SettingsIcon name="alert" size="xl" /></div>
        <div class="conf-who">{{ t('gui.settings.general.leave.title') }}</div>
        <p class="conf-text">{{ t('gui.settings.agents.leave_unsaved') }}</p>
        <p v-if="saving" class="conf-text fgs-muted" data-testid="leave-saving">{{ t('gui.settings.general.leave.saving') }}</p>
      </div>
      <template #footer>
        <div class="fgs-modal-actions">
          <button type="button" class="fgs-btn" data-testid="leave-stay" @click="answerLeave(false)">{{ t('gui.settings.general.leave.stay') }}</button>
          <button type="button" class="fgs-btn danger" data-testid="leave-discard" @click="answerLeave(true)">{{ t('gui.settings.general.leave.discard') }}</button>
        </div>
      </template>
    </ModalShell>

    <ToastHost :toasts="toasts" :dismiss-label="t('gui.common.close')" placement="top" @dismiss="dismissToast" />
  </SettingsShell>
</template>

<script setup lang="ts">
// Shared interactions; each route has its own API, scope and component lifetime.
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import Button from '@/components/ui/Button.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import LoadingState from '@/components/ui/LoadingState.vue'
import ModalShell from '@/components/ui/ModalShell.vue'
import ToastHost from '@/components/ui/ToastHost.vue'
import { useCurrentUser } from '@/core/auth/currentUser'
import { currentSettingsAccess } from '../access'
import { relativeTime } from '../format'
import { AGENT_CAPS, AGENT_STATE_TONE, agentState } from '../agentState'
import SettingsIcon from '../SettingsIcon.vue'
import SettingsShell from '../components/SettingsShell.vue'
import SettingsSubnav from '../components/SettingsSubnav.vue'
import AgentEditDialog from '../components/AgentEditDialog.vue'
import AgentEnrollmentDialog from '../components/AgentEnrollmentDialog.vue'
import { useSettingsToasts } from '../components/useSettingsToasts'
import { useMeNav } from '../components/meNav'
import { useSystemNav } from '../components/systemNav'
import { useUnsavedLeaveGuard } from '../components/useUnsavedLeaveGuard'
import { createSystemAgent, deleteSystemAgent, issueAgentEnrollment, listSystemAgents, updateSystemAgent,
  createPersonalAgent, deletePersonalAgent, issuePersonalAgentEnrollment, listPersonalAgents, updatePersonalAgent } from '../api'
import type { AgentCapabilities, AgentCapability, AgentEnrollment, SystemAgent, SystemAgentChanges } from '../types'

const props = defineProps<{ personal?: boolean }>()
const listAgents = props.personal ? listPersonalAgents : listSystemAgents
const createAgent = props.personal ? createPersonalAgent : createSystemAgent
const updateAgent = props.personal ? updatePersonalAgent : updateSystemAgent
const deleteAgent = props.personal ? deletePersonalAgent : deleteSystemAgent
const issueEnrollmentToken = props.personal ? issuePersonalAgentEnrollment : issueAgentEnrollment
const CAPS = AGENT_CAPS
const NAME_MAX = 100
const LOCATIONS = computed(() => props.personal ? ['remote'] as const : ['remote', 'local'] as const)
const createCaps = computed(() => props.personal ? CAPS.filter(cap => cap !== 'storage') : CAPS)
const STATE_TONE = AGENT_STATE_TONE
const { t, locale } = useI18n()
const meNav = useMeNav()
const systemNav = useSystemNav()
const { toasts, pushToast, dismissToast } = useSettingsToasts()
const message = (err: unknown) => err instanceof Error ? err.message : String(err)
const ago = (value: string | null) => relativeTime(value, locale.value)
const stateLabel = (a: SystemAgent) => t(`gui.settings.agents.state.${agentState(a) === 'unregistered' ? 'pending' : agentState(a)}`)
function isAgent(value: unknown): value is SystemAgent {
  const a = value as Partial<SystemAgent> | null
  return !!a && typeof a.agent_id === 'string' && typeof a.name === 'string' && typeof a.enabled === 'boolean'
    && (a.location === 'local' || a.location === 'remote')
    && !!a.configured_capabilities && !!a.reported_capabilities && !!a.effective_capabilities
    && typeof a.registration_status === 'string' && typeof a.credential_status === 'string'
    && typeof a.online_status === 'string' && typeof a.has_registration_history === 'boolean'
}

function nameProblem(name: string): string {
  const trimmed = name.trim()
  if (!trimmed) return t('gui.settings.agents.errors.name_required')
  if (trimmed.length > NAME_MAX) return t('gui.settings.agents.errors.name_too_long', { max: NAME_MAX })
  return ''
}

// ── list ──
const agents = ref<SystemAgent[] | null>(null)
const loading = ref(false)
const listError = ref('')
/** A write (or its re-read) is out: every change control is off until it settles. */
const busy = ref(false)
/** The write out is a save of the form (create / edit / enabled): leaving asks first. */
const saving = ref(false)
const mode = ref<'view' | 'create'>('view')
const selectedId = ref<string | null>(null)
const actionError = ref('')

const selected = computed(() =>
  mode.value === 'view' ? agents.value?.find((a) => a.agent_id === selectedId.value) ?? null : null)

// ── 응답의 수명: 화면 이탈 · 세션/권한 변경 뒤에 도착한 응답은 버린다 ──
const route = useRoute()
const router = useRouter()
const { user, status } = useCurrentUser()
const accessAllowed = () => status.value === 'ready' && !!user.value && currentSettingsAccess(route).kind === 'allow'
/** Thrown by live() for an answer of an ended epoch; the caller just stops. */
const STALE = Symbol('stale')
let epoch = 0
let disposed = false

// The same token check the auth guard makes (core/router/guard).
const hasToken = () => !!(window.__accessToken__ || sessionStorage.getItem('fg_access_token'))

/** Epoch `at` is still this screen, this person and an administrator with a token. */
function sameSession(at: number): boolean {
  if (disposed || at !== epoch) return false
  if (hasToken() && accessAllowed()) return true
  expire()
  return false
}

/** End the epoch: answers still out are dropped, and the token and the open question go. */
function expire() {
  epoch += 1
  closeEnrollment()
  selectedId.value = null
  mode.value = 'view'
  discardEdit()
  Object.assign(form, blankForm())
  formError.value = ''
  actionError.value = ''
  pending.value = null
  busy.value = false
  saving.value = false
  loading.value = false
  editSaved.value = false
}

/** The answer of a request sent in epoch `at`, or STALE when that epoch ended meanwhile. */
async function live<T>(at: number, request: Promise<T>): Promise<T> {
  let answer: T
  try {
    answer = await request
  } catch (err) {
    if (!sameSession(at)) throw STALE
    throw err
  }
  if (!sameSession(at)) throw STALE
  return answer
}

/** `task`, with STALE turned into `fallback` (a click handler, a re-read after a failure). */
async function quiet<T>(task: Promise<T>, fallback: T): Promise<T> {
  try {
    return await task
  } catch (err) {
    if (err === STALE) return fallback
    throw err
  }
}

// Another person, a lost role or a pending /auth/me: what was asked before is not theirs.
watch(() => `${user.value?.username ?? ''}|${status.value}|${currentSettingsAccess(route).kind}`, () => {
  expire()
  agents.value = null
  if (!disposed && hasToken() && accessAllowed()) void load()
}, { flush: 'sync' })

// At most one GET per session; focus and polling share it. Writes wait for an older
// GET before sending so a pre-write response cannot confirm a later mutation.
let reading: { at: number; promise: Promise<boolean> } | null = null
function read(at: number): Promise<boolean> {
  if (!sameSession(at)) return Promise.reject(STALE)
  if (reading?.at === at) return reading.promise
  const request = { at, promise: performRead(at) }
  reading = request
  return request.promise.finally(() => { if (reading === request) reading = null })
}

async function performRead(at: number): Promise<boolean> {
  loading.value = true
  listError.value = ''
  try {
    const list = await live(at, listAgents())
    if (!Array.isArray(list) || !list.every(isAgent)) throw new Error(t('gui.settings.agents.errors.bad_response'))
    agents.value = list
    const current = list.find((a) => a.agent_id === selectedId.value)
    if (selectedId.value && !current) { selectedId.value = null; discardEdit() }
    if (current && mode.value === 'view') rebaseEdit(current)
    if (enrollmentAgent.value) {
      const fresh = list.find((a) => a.agent_id === enrollmentAgent.value?.agent_id)
      if (!fresh) closeEnrollment()
      else {
        enrollmentAgent.value = fresh
        if (fresh.enrollment_status !== 'pending' || fresh.credential_status === 'active') clearEnrollment()
        if (fresh.credential_status === 'active') {
          enrollmentError.value = ''
          enrollmentExpired.value = false
        }
      }
    }
    return true
  } catch (err) {
    if (err === STALE) throw err
    listError.value = message(err)
    return false
  } finally {
    if (at === epoch) loading.value = false
  }
}

function load(): Promise<boolean> { return quiet(read(epoch), false) }
async function waitForRead(at: number) {
  if (reading?.at === at) await quiet(reading.promise, false)
  if (!sameSession(at)) throw STALE
}
/**
 * Re-read after a change. A change is reported done only when this re-read succeeds; when it fails
 * the outcome is unconfirmed: no success notice, a warning instead, the re-read error stays above
 * the list.
 */
async function reloadAfterChange(at: number): Promise<boolean> {
  if (await read(at)) return true
  pushToast(t('gui.settings.agents.refresh_unconfirmed'), 'warning')
  return false
}

// ── selection ──
async function select(row: SystemAgent) {
  if (busy.value || (mode.value === 'view' && row.agent_id === selectedId.value)) return
  const at = epoch
  if (formDirty.value && !(await ask())) return
  if (!sameSession(at)) return
  mode.value = 'view'
  selectedId.value = row.agent_id
  actionError.value = ''
  editSaved.value = false
  closeEnrollment()
  syncEdit()
}

// Restore the original registration form; personal Agents retain their own API scope.
const blankForm = () => ({ name: '', location: 'remote' as 'remote' | 'local', path: '', enabled: true, caps: { ai_cli: false, ai_api: false, storage: false } })
const form = reactive(blankForm())
const formError = ref('')
const createDirty = computed(() => mode.value === 'create' && (form.name.trim() !== '' || form.location !== 'remote'
  || form.path.trim() !== '' || !form.enabled || CAPS.some(cap => form.caps[cap])))
const createNameProblem = computed(() => nameProblem(form.name))
const createCanSave = computed(() => mode.value === 'create' && !createNameProblem.value && !busy.value)

function setName(value: string) { if (mode.value === 'create') form.name = value; else edit.name = value }
function setWorkdir(value: string) { if (mode.value !== 'create') edit.workdir = value }
function setCap(cap: 'ai_cli' | 'ai_api', value: boolean) {
  if (mode.value === 'create') form.caps[cap] = value
  else edit.caps[cap] = value
}

async function startCreate() {
  if (busy.value || mode.value === 'create') return
  const at = epoch
  if (formDirty.value && !(await ask())) return
  if (!sameSession(at)) return
  discardEdit()
  selectedId.value = null
  Object.assign(form, blankForm())
  formError.value = ''
  actionError.value = ''
  editSaved.value = false
  closeEnrollment()
  mode.value = 'create'
}

async function closeEditor() {
  if (busy.value) return
  const at = epoch
  if (formDirty.value && !(await ask())) return
  if (!sameSession(at)) return
  mode.value = 'view'
  Object.assign(form, blankForm())
  selectedId.value = null
  formError.value = ''
  actionError.value = ''
  editSaved.value = false
  discardEdit()
}

async function submitCreate() {
  if (!createCanSave.value) return
  const at = epoch
  busy.value = true
  saving.value = true
  formError.value = ''
  try {
    await waitForRead(at)
    const created = await live(at, createAgent({
      name: form.name.trim(), enabled: form.enabled, location: form.location,
      connection_mode: form.location === 'remote' ? 'agent_pull' : null,
      local_executable_path: form.location === 'local' ? form.path.trim() || null : null,
      configured_capabilities: { ...form.caps },
    }))
    // A successful POST must never leave a submit-able create form, even if confirmation fails.
    mode.value = 'view'
    Object.assign(form, blankForm())
    if (!isAgent(created)) {
      await reloadAfterChange(at)
      actionError.value = t('gui.settings.agents.errors.unconfirmed')
      return
    }
    if (created.location === 'local' || !created.enabled) {
      if (!(await reloadAfterChange(at))) return
      if (!agents.value?.some(agent => agent.agent_id === created.agent_id)) {
        actionError.value = t('gui.settings.agents.errors.reread_mismatch')
        return
      }
      pushToast(t('gui.settings.agents.created', { name: created.name }))
      return
    }
    enrollmentAgent.value = created
    enrollmentError.value = ''
    enrollmentExpired.value = false
    saving.value = false
    await issueEnrollment(at, created.agent_id, enrollmentEpoch)
  } catch (err) {
    if (err === STALE) return
    formError.value = message(err)
    await quiet(read(at), false)
  } finally {
    if (at === epoch) { busy.value = false; saving.value = false; schedulePoll() }
  }
}

// ── 이름 · 실행 경로 · capability ──
const edit = reactive<{ name: string; path: string; workdir: string; caps: AgentCapabilities }>({ name: '', path: '', workdir: '', caps: { ai_cli: false, ai_api: false, storage: false } })
const editSaved = ref(false)

/** The confirmed values the form stands on: the server's last answer for the Agent in the form. */
interface EditValues { name: string; path: string | null; workdir: string | null; caps: AgentCapabilities }
const editBase = ref<{ agentId: string; values: EditValues } | null>(null)

function confirmedValues(a: SystemAgent): EditValues {
  return { name: a.name, path: a.local_executable_path, workdir: a.remote_workdir ?? null, caps: { ...a.configured_capabilities } }
}

const nameTouched = (base: EditValues) => edit.name.trim() !== base.name
const pathTouched = (base: EditValues) => (edit.path.trim() || null) !== base.path
const workdirTouched = (base: EditValues) => (edit.workdir.trim() || null) !== base.workdir
const capTouched = (base: EditValues, cap: AgentCapability) => edit.caps[cap] !== base.caps[cap]

/** Which inputs differ from the confirmed values (marked as unsaved on screen). */
const touched = computed(() => {
  const base = editBase.value?.values
  return {
    name: !!base && nameTouched(base),
    path: !!base && pathTouched(base),
    workdir: !!base && workdirTouched(base),
    caps: Object.fromEntries(CAPS.map((cap) => [cap, !!base && capTouched(base, cap)])) as Record<AgentCapability, boolean>,
  }
})

/** Put the confirmed values of the selected Agent into the form; nothing typed is kept. */
function syncEdit() {
  const a = selected.value
  if (!a) return
  const values = confirmedValues(a)
  editBase.value = { agentId: a.agent_id, values }
  edit.name = values.name
  edit.path = values.path ?? ''
  edit.workdir = values.workdir ?? ''
  edit.caps = { ...values.caps }
}

/** 저장하지 않고 나가기: the typed values are thrown away. */
function discardEdit() {
  editBase.value = null
  edit.name = ''
  edit.path = ''
  edit.workdir = ''
  edit.caps = { ai_cli: false, ai_api: false, storage: false }
}

/**
 * A re-read brought the selected Agent again. A field the person did not touch follows the server
 * (another administrator's change shows and is never sent back as theirs); a touched field stays
 * as typed and is compared with the new server value from now on.
 */
function rebaseEdit(a: SystemAgent) {
  const base = editBase.value
  if (!base || base.agentId !== a.agent_id) {
    syncEdit()
    return
  }
  const fresh = confirmedValues(a)
  if (!nameTouched(base.values)) edit.name = fresh.name
  if (!pathTouched(base.values)) edit.path = fresh.path ?? ''
  if (!workdirTouched(base.values)) edit.workdir = fresh.workdir ?? ''
  for (const cap of CAPS) {
    if (!capTouched(base.values, cap)) edit.caps[cap] = fresh.caps[cap]
  }
  editBase.value = { agentId: a.agent_id, values: fresh }
}

/** Only the fields the person changed from the confirmed values. */
const editChanges = computed<SystemAgentChanges>(() => {
  const a = selected.value
  const base = editBase.value
  const changes: SystemAgentChanges = {}
  if (!a || !base || base.agentId !== a.agent_id) return changes
  if (nameTouched(base.values)) changes.name = edit.name.trim()
  if (workdirTouched(base.values)) changes.remote_workdir = edit.workdir.trim() || null
  if (CAPS.some((cap) => capTouched(base.values, cap))) changes.configured_capabilities = { ...edit.caps }
  return changes
})

const editDirty = computed(() => Object.keys(editChanges.value).length > 0)
const editNameProblem = computed(() => (touched.value.name ? nameProblem(edit.name) : ''))
const editCanSave = computed(() => editDirty.value && !editNameProblem.value && !busy.value)

/** The Agent shows every change that was asked for. */
function applied(a: SystemAgent, changes: SystemAgentChanges): boolean {
  return (changes.name === undefined || a.name === changes.name)
    && (changes.enabled === undefined || a.enabled === changes.enabled)
    && (changes.local_executable_path === undefined || a.local_executable_path === changes.local_executable_path)
    && (changes.remote_workdir === undefined || (a.remote_workdir ?? null) === changes.remote_workdir)
    && (changes.configured_capabilities === undefined
      || CAPS.every((cap) => a.configured_capabilities[cap] === changes.configured_capabilities![cap]))
}

/** One PATCH with `changes`; done only when its answer and the re-read both show them. */
async function patchAgent(at: number, a: SystemAgent, changes: SystemAgentChanges): Promise<boolean> {
  clearEnrollment() // the Agent is being changed: a token shown for it goes
  const answered = await live(at, updateAgent(a.agent_id, changes))
  if (!isAgent(answered) || answered.agent_id !== a.agent_id || !applied(answered, changes)) {
    actionError.value = t('gui.settings.agents.errors.unconfirmed')
    await read(at)
    return false
  }
  if (!(await reloadAfterChange(at))) return false
  const fresh = selected.value
  if (!fresh || !applied(fresh, changes)) {
    actionError.value = t('gui.settings.agents.errors.reread_mismatch')
    return false
  }
  return true
}

async function saveEdit() {
  const a = selected.value
  if (!a || !editCanSave.value) return
  const at = epoch
  busy.value = true
  saving.value = true
  actionError.value = ''
  editSaved.value = false
  try {
    await waitForRead(at)
    const changes = editChanges.value
    if (!Object.keys(changes).length) return
    if (await patchAgent(at, a, changes)) {
      syncEdit()
      editSaved.value = true
      selectedId.value = null
      discardEdit()
    }
  } catch (err) {
    if (err === STALE) return
    actionError.value = message(err) // the inputs stay as typed
    await quiet(read(at), false)
  } finally {
    if (at === epoch) {
      busy.value = false
      saving.value = false
    }
  }
}

interface Pending {
  kind: 'delete'
  icon: 'trash'
  tone: 'warn' | 'danger'
  title: string
  effects: string[]
  label: string
  /** The request of the dialog, sent in epoch `at`. */
  run: (at: number) => Promise<void>
}
const pending = ref<Pending | null>(null)

function closePending() {
  if (!busy.value) pending.value = null
}

async function runPending() {
  const task = pending.value
  if (!task || busy.value) return
  const at = epoch
  busy.value = true
  actionError.value = ''
  try {
    await waitForRead(at)
    await task.run(at)
  } catch (err) {
    if (err === STALE) return
    actionError.value = message(err)
    await quiet(read(at), false)
  } finally {
    if (at === epoch) {
      pending.value = null
      busy.value = false
    }
  }
}

function askDelete(a: SystemAgent) {
  if (!a || busy.value || a.has_registration_history) return
  pending.value = {
    kind: 'delete',
    icon: 'trash',
    tone: 'danger',
    title: `${a.name} · ${t('gui.settings.agents.delete')}`,
    effects: [
      t('gui.settings.agents.effects.delete_removes'),
      t('gui.settings.agents.effects.delete_unregistered'),
    ],
    label: t('gui.settings.agents.delete'),
    run: async (at) => {
      closeEnrollment()
      await live(at, deleteAgent(a.agent_id))
      if (!(await reloadAfterChange(at))) return
      if (agents.value?.some((x) => x.agent_id === a.agent_id)) {
        actionError.value = t('gui.settings.agents.errors.reread_mismatch')
        return
      }
      pushToast(t('gui.settings.agents.deleted_done', { name: a.name }))
    },
  }
}

// One-time token and its created Agent ID remain in memory only. Closing the dialog
// increments its generation, so a late issuance cannot repopulate a closed dialog.
const enrollment = ref<AgentEnrollment | null>(null)
const enrollmentAgent = ref<SystemAgent | null>(null)
const enrollmentError = ref('')
const enrollmentExpired = ref(false)
let enrollmentEpoch = 0
let expiryTimer: ReturnType<typeof setTimeout> | null = null
let pollTimer: ReturnType<typeof setTimeout> | null = null

function clearEnrollment() {
  enrollment.value = null
  if (expiryTimer !== null) clearTimeout(expiryTimer)
  expiryTimer = null
}
function closeEnrollment() {
  enrollmentEpoch += 1
  clearEnrollment()
  enrollmentAgent.value = null
  enrollmentError.value = ''
  enrollmentExpired.value = false
  if (pollTimer !== null) clearTimeout(pollTimer)
  pollTimer = null
}

async function issueEnrollment(at: number, id: string, generation: number) {
  clearEnrollment()
  enrollmentError.value = ''
  enrollmentExpired.value = false
  try {
    const issued = await live(at, issueEnrollmentToken(id))
    if (generation !== enrollmentEpoch || enrollmentAgent.value?.agent_id !== id) return
    const expires = Date.parse(issued?.expires_at)
    if (!issued || issued.agent_id !== id || typeof issued.enrollment_token !== 'string' || !issued.enrollment_token.trim() || !Number.isFinite(expires)) {
      throw new Error(t('gui.settings.agents.errors.unconfirmed'))
    }
    if (expires <= Date.now()) enrollmentExpired.value = true
    else {
      enrollment.value = { agent_id: id, enrollment_token: issued.enrollment_token, expires_at: issued.expires_at }
      expiryTimer = setTimeout(() => {
        clearEnrollment()
        enrollmentExpired.value = true
      }, expires - Date.now())
    }
    await reloadAfterChange(at)
  } catch (err) {
    if (err === STALE) throw err
    if (generation !== enrollmentEpoch) return
    enrollmentError.value = message(err)
    await quiet(read(at), false)
  }
}

async function retryEnrollment() {
  const a = enrollmentAgent.value
  if (busy.value || !a || !a.enabled || a.credential_status === 'active') return
  const at = epoch
  const generation = enrollmentEpoch
  busy.value = true
  try {
    await waitForRead(at)
    if (generation !== enrollmentEpoch) return
    const fresh = enrollmentAgent.value
    if (!fresh || fresh.agent_id !== a.agent_id || !fresh.enabled || fresh.credential_status === 'active') return
    await issueEnrollment(at, fresh.agent_id, generation)
  } catch (err) { if (err !== STALE) throw err }
  finally { if (at === epoch) { busy.value = false; schedulePoll() } }
}

function onCopied(copied: boolean) {
  if (sameSession(epoch) && enrollment.value) pushToast(t(copied ? 'gui.settings.users.copied' : 'gui.settings.users.copy_failed'), copied ? 'success' : 'warning')
}

function schedulePoll() {
  if (pollTimer !== null) clearTimeout(pollTimer)
  pollTimer = null
  if (disposed || !enrollmentAgent.value) return
  pollTimer = setTimeout(async () => {
    pollTimer = null
    if (!sameSession(epoch) || !enrollmentAgent.value) return
    if (!busy.value && document.visibilityState !== 'hidden') await load()
    schedulePoll()
  }, 5000)
}
function onFocus() {
  if (!sameSession(epoch)) return
  if (enrollment.value && Date.parse(enrollment.value.expires_at) <= Date.now()) {
    clearEnrollment()
    enrollmentExpired.value = true
  }
  if (!busy.value) void load()
}
function onVisibility() { if (document.visibilityState !== 'hidden') onFocus() }

// ── 미저장 이탈 보호 ──
const formDirty = computed(() => (mode.value === 'create' ? createDirty.value : editDirty.value))
const { open: leaveOpen, answer: answerLeave, ask } = useUnsavedLeaveGuard(() => formDirty.value || saving.value)

/** 개요 › 확인 필요 opens one Agent here with ?agent=<id>; the query is dropped once it is read. */
function openFromQuery() {
  const id = route.query.agent
  if (typeof id !== 'string' || !id) return
  void router.replace({ query: {} })
  if (!agents.value?.some((a) => a.agent_id === id)) return
  mode.value = 'view'
  selectedId.value = id
  syncEdit()
}

onMounted(() => {
  window.addEventListener('focus', onFocus)
  document.addEventListener('visibilitychange', onVisibility)
  void load().then((ok) => { if (ok) openFromQuery() })
})
onBeforeUnmount(() => {
  window.removeEventListener('focus', onFocus)
  document.removeEventListener('visibilitychange', onVisibility)
  disposed = true
  expire()
})
</script>

<style>
.ui-modal-overlay:has(> .agent-registration-drawer) { padding: 0; align-items: stretch; justify-content: flex-end; }
.ui-modal-surface.agent-registration-drawer { width: min(470px, 100%); max-width: 470px; height: 100%; max-height: 100%; border-radius: 0; padding: 0; }
.agent-registration-drawer .ui-modal-body { padding: 0; flex: 1; min-height: 0; display: flex; }
.agent-registration-drawer .fgs-d-main { width: 100%; }
</style>

<style scoped>
.agent-page-head { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 20px; }
.agent-page-head h2 { margin: 0; font-size: 22px; }
.fgs-phead { padding: 19px 23px; }
.fgs-phead h3 { margin: 0; font-size: 16px; }
.fgs-panel-body { padding: 22px 23px; }
.agent-list { display: grid; gap: 9px; }
.agent-row { display: grid; grid-template-columns: 40px minmax(0, 1fr) auto; align-items: center; gap: 15px; padding: 16px 17px; border: 1px solid var(--fg2-line-strong); border-radius: 10px; background: var(--fg2-control-surface); }
.agent-symbol { width: 40px; height: 40px; border: 1px solid var(--fg2-line-strong); border-radius: 10px; display: grid; place-items: center; color: var(--fg2-accent); }
.agent-title { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.agent-title strong { font-size: 13px; overflow-wrap: anywhere; }
.agent-info p { font-size: 11px; color: var(--fg2-muted); margin: 5px 0 0; }
.agent-actions { display: flex; gap: 7px; }
.agent-icon { width: 33px; min-height: 29px; padding: 0; }
.confirm { display: grid; justify-items: center; gap: 12px; padding: 10px 0 4px; text-align: center; }
.conf-who { font-weight: 700; }
.conf-text { margin: 0; font-size: 13px; }
.effects { padding-left: 18px; text-align: left; font-size: 13px; }
@media (max-width: 580px) {
  .fgs-phead, .fgs-panel-body { padding: 16px; }
  .agent-row { grid-template-columns: 36px minmax(0, 1fr); gap: 10px; }
  .agent-actions { grid-column: 2; }
}
</style>
