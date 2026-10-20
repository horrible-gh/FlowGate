<template>
  <SettingsShell :crumbs="crumbs" scope="system">
    <template #nav>
      <SettingsSubnav :title="t('gui.settings.scope.system')" :items="systemNav" active="agents" />
    </template>

    <section class="fgs-panel" data-panel="system-agents">
      <div class="fgs-phead">
        <span class="fgs-tico"><SettingsIcon name="agent" size="lg" /></span>
        <h2>{{ t('gui.settings.system_nav.agents') }}</h2>
        <div class="fgs-push">
          <button type="button" class="fgs-btn pri" :disabled="busy || !agents" data-testid="agent-new" @click="startCreate">
            <SettingsIcon name="plus" />{{ t('gui.settings.agents.create') }}
          </button>
        </div>
      </div>

      <div class="fgs-panel-body">
        <LoadingState v-if="loading && !agents" :label="t('gui.common.loading')" />
        <ErrorState v-else-if="listError && !agents" :title="t('gui.settings.agents.errors.load_failed')" :message="listError" data-testid="agents-error">
          <template #action>
            <Button variant="secondary" data-testid="agents-retry" @click="load">{{ t('gui.common.retry') }}</Button>
          </template>
        </ErrorState>

        <template v-else-if="agents">
          <!-- A failed re-read with a list on screen: the list is the last one read, not the current state. -->
          <div v-if="listError" class="fgs-refresh-error" data-testid="agents-refresh-error">
            <ErrorState compact :title="t('gui.settings.agents.errors.refresh_failed')" :message="listError">
              <template #action>
                <Button variant="secondary" data-testid="agents-refresh-retry" :disabled="loading" @click="load">{{ t('gui.common.retry') }}</Button>
              </template>
            </ErrorState>
          </div>

          <div class="agents-grid">
            <div class="fgs-card agents-list" data-testid="agent-list">
              <p v-if="!agents.length" class="fgs-empty" data-testid="agents-empty">{{ t('gui.settings.agents.empty') }}</p>
              <button
                v-for="row in agents"
                :key="row.agent_id"
                type="button"
                class="agent-row"
                :class="{ sel: mode === 'view' && row.agent_id === selectedId }"
                :disabled="busy"
                :data-agent="row.agent_id"
                @click="select(row)"
              >
                <span class="who">
                  <b>{{ row.name }}</b>
                  <small>{{ t(`gui.settings.agents.location.${row.location}`) }} · {{ t(`gui.settings.agents.registration.${row.registration_status}`) }}</small>
                </span>
                <span class="badges">
                  <span v-if="!row.enabled" class="fgs-pill off" data-testid="agent-row-disabled">{{ t('gui.settings.agents.enabled.off') }}</span>
                  <span class="fgs-pill" :class="RUNTIME_TONE[runtimeState(row)]" :data-runtime="runtimeState(row)">{{ t(`gui.settings.agents.runtime.${runtimeState(row)}`) }}</span>
                </span>
              </button>
            </div>

            <!-- 등록: POST /system/agents -->
            <form v-if="mode === 'create'" class="fgs-card agent-detail" data-testid="agent-create-form" @submit.prevent="submitCreate">
              <div class="fgs-card-h"><span class="fgs-tico sm"><SettingsIcon name="plus" /></span><b>{{ t('gui.settings.agents.create') }}</b></div>
              <div class="pad">
                <div class="fgs-formfield">
                  <label for="agent-new-name">{{ t('gui.settings.agents.fields.name') }}</label>
                  <input id="agent-new-name" v-model="form.name" class="fgs-input" maxlength="100" autocomplete="off" :disabled="busy" data-testid="agent-name" />
                  <span v-if="form.name && createNameProblem" class="fgs-error" data-testid="agent-name-invalid">{{ createNameProblem }}</span>
                </div>
                <div class="fgs-formfield">
                  <span class="label">{{ t('gui.settings.agents.fields.location') }}</span>
                  <div class="fgs-chips" data-testid="agent-location">
                    <button v-for="loc in LOCATIONS" :key="loc" type="button" class="fgs-chip" :class="{ on: form.location === loc }" :disabled="busy" :data-location="loc" @click="form.location = loc">
                      {{ t(`gui.settings.agents.location.${loc}`) }}
                    </button>
                  </div>
                  <span class="fgs-muted">{{ t('gui.settings.agents.location_fixed') }}</span>
                </div>
                <div v-if="form.location === 'remote'" class="fgs-formfield" data-testid="agent-remote-fields">
                  <span class="label">{{ t('gui.settings.agents.fields.connection') }}</span>
                  <span class="mono">{{ t('gui.settings.agents.connection.agent_pull') }}</span>
                  <span class="fgs-muted">{{ t('gui.settings.agents.connection_hint') }}</span>
                </div>
                <div v-else class="fgs-formfield" data-testid="agent-local-fields">
                  <label for="agent-new-path">{{ t('gui.settings.agents.fields.path') }}</label>
                  <input id="agent-new-path" v-model="form.path" class="fgs-input mono" autocomplete="off" :disabled="busy" placeholder="/opt/flowgate-agent" data-testid="agent-path" />
                </div>
                <div class="fgs-formfield">
                  <span class="label">{{ t('gui.settings.agents.fields.capabilities') }}</span>
                  <div class="caps">
                    <label v-for="cap in CAPS" :key="cap" class="cap"><input v-model="form.caps[cap]" type="checkbox" :disabled="busy" :data-cap="cap" /> {{ t(`gui.settings.agents.caps.${cap}`) }}</label>
                  </div>
                  <span class="fgs-muted">{{ t('gui.settings.agents.storage_hint') }}</span>
                </div>
                <div class="fgs-formfield">
                  <label class="cap"><input v-model="form.enabled" type="checkbox" :disabled="busy" data-testid="agent-enabled" /> {{ t('gui.settings.agents.create_enabled') }}</label>
                </div>
                <p v-if="formError" class="fgs-error" role="alert" data-testid="agent-form-error">{{ formError }}</p>
                <div class="fgs-modal-actions">
                  <button type="button" class="fgs-btn" :disabled="busy" data-testid="agent-create-cancel" @click="cancelCreate">{{ t('gui.common.cancel') }}</button>
                  <button type="button" class="fgs-btn pri" :disabled="!createCanSave" data-testid="agent-create-submit" @click="submitCreate">{{ busy ? t('gui.settings.saving') : t('gui.settings.agents.create_submit') }}</button>
                </div>
              </div>
            </form>

            <!-- 선택한 Agent -->
            <div v-else-if="selected" class="fgs-card agent-detail" data-testid="agent-detail" :data-agent="selected.agent_id">
              <div class="fgs-card-h">
                <span class="fgs-tico sm"><SettingsIcon name="agent" /></span>
                <b data-testid="agent-detail-name">{{ selected.name }}</b>
                <span class="fgs-pill off">{{ t(`gui.settings.agents.location.${selected.location}`) }}</span>
                <span class="fgs-pill" :class="selected.enabled ? 'ok' : 'off'" data-testid="agent-enabled-state">{{ t(`gui.settings.agents.enabled.${selected.enabled ? 'on' : 'off'}`) }}</span>
              </div>

              <div v-if="actionError" class="fgs-line">
                <span class="fgs-error" role="alert" data-testid="agent-action-error">{{ actionError }}</span>
              </div>

              <!-- 이름 · 실행 경로 · capability: PATCH 에는 바꾼 필드만 -->
              <div class="sec">
                <h3>{{ t('gui.settings.agents.sections.settings') }}</h3>
                <div class="fgs-formfield">
                  <label for="agent-edit-name">{{ t('gui.settings.agents.fields.name') }}</label>
                  <input id="agent-edit-name" v-model="edit.name" class="fgs-input" maxlength="100" autocomplete="off" :disabled="busy" data-testid="edit-name" />
                  <span v-if="editNameProblem" class="fgs-error" data-testid="edit-name-invalid">{{ editNameProblem }}</span>
                </div>
                <div class="fgs-formfield">
                  <span class="label">{{ t('gui.settings.agents.fields.location') }}</span>
                  <span data-testid="edit-location">{{ t(`gui.settings.agents.location.${selected.location}`) }}<template v-if="selected.location === 'remote'"> · {{ t('gui.settings.agents.connection.agent_pull') }}</template></span>
                  <span class="fgs-muted">{{ t('gui.settings.agents.location_fixed') }}</span>
                </div>
                <div v-if="selected.location === 'local'" class="fgs-formfield">
                  <label for="agent-edit-path">{{ t('gui.settings.agents.fields.path') }}</label>
                  <input id="agent-edit-path" v-model="edit.path" class="fgs-input mono" autocomplete="off" :disabled="busy" placeholder="/opt/flowgate-agent" data-testid="edit-path" />
                </div>
                <div class="fgs-formfield">
                  <span class="label">{{ t('gui.settings.agents.fields.capabilities') }}</span>
                  <div class="caps">
                    <label v-for="cap in CAPS" :key="cap" class="cap"><input v-model="edit.caps[cap]" type="checkbox" :disabled="busy" :data-edit-cap="cap" /> {{ t(`gui.settings.agents.caps.${cap}`) }}</label>
                  </div>
                  <span class="fgs-muted">{{ t('gui.settings.agents.storage_hint') }}</span>
                </div>
                <div class="row">
                  <button type="button" class="fgs-btn pri sm" :disabled="!editCanSave" data-testid="edit-save" @click="saveEdit">{{ saving ? t('gui.settings.saving') : t('gui.settings.general.save') }}</button>
                  <span v-if="editSaved && !editDirty" class="fgs-muted" data-testid="edit-saved">{{ t('gui.settings.saved') }}</span>
                </div>
              </div>

              <!-- 사용 여부: enabled 만 PATCH. 삭제 · 자격 해지와 다르다 -->
              <div class="sec">
                <h3>{{ t('gui.settings.agents.sections.enabled') }}</h3>
                <div class="row">
                  <span>{{ t(`gui.settings.agents.enabled.${selected.enabled ? 'on' : 'off'}`) }}</span>
                  <span class="fgs-spacer" />
                  <button v-if="selected.enabled" type="button" class="fgs-btn sm" :disabled="busy" data-testid="agent-disable" @click="askDisable">
                    <SettingsIcon name="pause" size="sm" />{{ t('gui.settings.agents.disable') }}
                  </button>
                  <button v-else type="button" class="fgs-btn sm pri" :disabled="busy" data-testid="agent-enable" @click="enableAgent">
                    <SettingsIcon name="power" size="sm" />{{ t('gui.settings.agents.enable') }}
                  </button>
                </div>
                <span class="fgs-muted">{{ t('gui.settings.agents.disable_hint') }}</span>
              </div>

              <!-- 상태: 관리 GET 의 값 그대로 -->
              <div class="sec">
                <h3>{{ t('gui.settings.agents.sections.status') }}</h3>
                <dl class="kv" data-testid="agent-status">
                  <dt>{{ t('gui.settings.agents.fields.registration') }}</dt>
                  <dd data-field="registration_status">{{ t(`gui.settings.agents.registration.${selected.registration_status}`) }}</dd>
                  <dt>{{ t('gui.settings.agents.fields.credential') }}</dt>
                  <dd data-field="credential_status">{{ t(`gui.settings.agents.credential.${selected.credential_status}`) }}</dd>
                  <dt>{{ t('gui.settings.agents.fields.enrollment') }}</dt>
                  <dd data-field="enrollment_status">
                    <template v-if="selected.enrollment_status === 'pending'">{{ t('gui.settings.agents.enrollment.pending', { at: selected.enrollment_expires_at ?? '—' }) }}</template>
                    <template v-else>{{ t('gui.settings.agents.enrollment.none') }}</template>
                  </dd>
                  <dt>{{ t('gui.settings.agents.fields.online') }}</dt>
                  <dd data-field="online_status">{{ t(`gui.settings.agents.online.${selected.online_status}`) }}</dd>
                  <dt>{{ t('gui.settings.agents.fields.last_seen') }}</dt>
                  <dd data-field="last_seen_at" class="mono">{{ selected.last_seen_at ?? t('gui.settings.agents.never') }}</dd>
                  <dt>{{ t('gui.settings.agents.fields.protocol') }}</dt>
                  <dd data-field="protocol_compatibility">
                    {{ selected.protocol_version ?? '—' }} · {{ t(`gui.settings.agents.protocol.${selected.protocol_compatibility}`) }}
                    <template v-if="selected.protocol_compatibility === 'incompatible'"> · {{ t('gui.settings.agents.protocol_rejected', { version: selected.last_rejected_protocol_version ?? '—', at: selected.last_protocol_rejection_at ?? '—' }) }}</template>
                  </dd>
                  <dt>{{ t('gui.settings.agents.fields.version') }}</dt>
                  <dd data-field="agent_version">{{ selected.agent_version ?? '—' }}</dd>
                  <dt>{{ t('gui.settings.agents.fields.platform') }}</dt>
                  <dd data-field="platform">{{ [selected.os, selected.architecture].filter(Boolean).join(' / ') || '—' }}</dd>
                </dl>
                <div class="caps-table" data-testid="agent-caps">
                  <div class="caps-tr head">
                    <div />
                    <div>{{ t('gui.settings.agents.caps_cols.configured') }}</div>
                    <div>{{ t('gui.settings.agents.caps_cols.reported') }}</div>
                    <div>{{ t('gui.settings.agents.caps_cols.effective') }}</div>
                  </div>
                  <div v-for="cap in CAPS" :key="cap" class="caps-tr" :data-cap-row="cap">
                    <div>{{ t(`gui.settings.agents.caps.${cap}`) }}</div>
                    <div data-col="configured">{{ onOff(selected.configured_capabilities[cap]) }}</div>
                    <div data-col="reported">{{ onOff(selected.reported_capabilities[cap]) }}</div>
                    <div data-col="effective">{{ onOff(selected.effective_capabilities[cap]) }}</div>
                  </div>
                </div>
                <span class="fgs-muted" data-testid="agent-online-note">{{ t(selected.location === 'local' ? 'gui.settings.agents.local_note' : 'gui.settings.agents.online_note') }}</span>
              </div>

              <!-- 등록 토큰 · 자격 해지 -->
              <div class="sec">
                <h3>{{ t('gui.settings.agents.sections.registration') }}</h3>
                <div class="row">
                  <button v-if="selected.location === 'remote' && selected.enabled" type="button" class="fgs-btn sm" :disabled="busy" data-testid="agent-issue" @click="askIssue">
                    <SettingsIcon name="key" size="sm" />{{ t('gui.settings.agents.issue') }}
                  </button>
                  <button v-if="selected.credential_status === 'active'" type="button" class="fgs-btn sm danger" :disabled="busy" data-testid="agent-revoke" @click="askRevoke">
                    <SettingsIcon name="ban" size="sm" />{{ t('gui.settings.agents.revoke') }}
                  </button>
                </div>
                <span v-if="selected.location === 'local'" class="fgs-muted" data-testid="issue-local">{{ t('gui.settings.agents.issue_local') }}</span>
                <span v-else-if="!selected.enabled" class="fgs-muted" data-testid="issue-disabled">{{ t('gui.settings.agents.issue_disabled') }}</span>

                <!-- 발급 응답 때만 이 메모리에서 보인다. GET 으로는 다시 얻을 수 없다. -->
                <div v-if="shownEnrollment" ref="secretRef" class="enrollment" data-testid="enrollment-secret">
                  <span class="fgs-hint"><SettingsIcon name="alert" size="sm" />{{ t('gui.settings.agents.token_once') }}</span>
                  <div class="fgs-secret" data-testid="enrollment-token">{{ shownEnrollment.enrollment_token }}</div>
                  <span class="fgs-muted" data-testid="enrollment-expires">{{ t('gui.settings.agents.token_expires', { at: shownEnrollment.expires_at }) }}</span>
                  <div class="row">
                    <button type="button" class="fgs-link" data-testid="enrollment-copy" @click="copyToken"><SettingsIcon name="check" size="sm" />{{ t('gui.settings.users.copy') }}</button>
                    <button type="button" class="fgs-link" data-testid="enrollment-dismiss" @click="clearEnrollment"><SettingsIcon name="x" size="sm" />{{ t('gui.settings.agents.token_hide') }}</button>
                  </div>
                </div>
              </div>

              <!-- 삭제: 등록 이력이 없는 Agent 만 -->
              <div class="sec">
                <h3>{{ t('gui.settings.agents.sections.delete') }}</h3>
                <div v-if="!selected.has_registration_history" class="row">
                  <button type="button" class="fgs-btn sm danger" :disabled="busy" data-testid="agent-delete" @click="askDelete">
                    <SettingsIcon name="trash" size="sm" />{{ t('gui.settings.agents.delete') }}
                  </button>
                </div>
                <span v-else class="fgs-muted" data-testid="delete-protected">{{ t('gui.settings.agents.delete_protected') }}</span>
              </div>
            </div>

            <div v-else class="fgs-card agent-detail" data-testid="agent-none"><p class="fgs-empty">{{ t('gui.settings.agents.pick') }}</p></div>
          </div>
        </template>
      </div>
    </section>

    <!-- 민감 동작 확인: 취소하면 요청 0건, 확인 후 응답까지 다시 누를 수 없다 -->
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
// 시스템 › 시스템 Agent (flowgate.v02.0076 T#4, D §2.6). The old client/src/settings AgentsView moved
// here on the same single API family, /system/agents (api/v1/agent_routes.py):
//
// - GET /system/agents lists every Agent with its state; the list answer is the detail of each
//   Agent (agent_registry.detail), so a selection reads it from there.
// - POST /system/agents registers local (no connection mode, optional executable path) or remote
//   (agent_pull, no path) with the ai_cli / ai_api / storage capabilities. Storage is a setting only:
//   nothing here runs a storage job, an AI job or a personal Agent.
// - PATCH /system/agents/{aid} carries only what changed — name / executable path / capabilities
//   from the form, or enabled alone from 사용 중지 · 다시 사용. The location is fixed and never sent.
// - POST …/enrollments and …/credential/revoke send the route's Empty body ({}); DELETE removes an
//   Agent that was never registered (the server answers 409 for one with registration history).
//
// Every change is confirmed by its answer AND a re-read of the list; a refused or failed request
// (403 / 409 / 503 / …) never shows as done — its message stays and the list is read again. The
// sensitive actions (token, revoke, delete, 사용 중지) are asked first with their impact: cancel
// sends nothing, and while a request is out nothing can be sent again. Online means a recent
// compatible heartbeat only — never "can run" or "running" an AI job.
//
// The enrollment token lives in this component's memory only, from the 201 answer until the
// person switches Agent, changes it, revokes, deletes or leaves the screen. It never reaches a
// log, browser storage, the URL or a toast, and no GET can bring it back. /agent/enrollments and
// /agent/heartbeat belong to the Agent runtime and are never called from here.
//
// Every request belongs to the epoch it was sent in. Leaving the screen, losing the token, another
// person signing in or the administrator role going away ends the epoch: an answer arriving after
// that is dropped — no token kept, no re-read, no notice.
//
// The form keeps the confirmed values it was filled from apart from what was typed. A re-read moves
// the fields nobody touched to the server's new value (another administrator's change shows and is
// never sent back), keeps the touched ones, and PATCH carries the touched fields only.
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import Button from '@/components/ui/Button.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import LoadingState from '@/components/ui/LoadingState.vue'
import ModalShell from '@/components/ui/ModalShell.vue'
import ToastHost from '@/components/ui/ToastHost.vue'
import { useCurrentUser } from '@/core/auth/currentUser'
import { copyToClipboard } from '@/core/interaction/clipboard'
import { currentSettingsAccess } from '../access'
import SettingsIcon from '../SettingsIcon.vue'
import SettingsShell from '../components/SettingsShell.vue'
import SettingsSubnav from '../components/SettingsSubnav.vue'
import { useSettingsToasts } from '../components/useSettingsToasts'
import { useSystemNav } from '../components/systemNav'
import { useUnsavedLeaveGuard } from '../components/useUnsavedLeaveGuard'
import type { SettingsIconName } from '../icons'
import {
  createSystemAgent,
  deleteSystemAgent,
  issueAgentEnrollment,
  listSystemAgents,
  revokeAgentCredential,
  updateSystemAgent,
} from '../api'
import type { AgentCapabilities, AgentCapability, AgentEnrollment, AgentLocation, SystemAgent, SystemAgentChanges } from '../types'

const CAPS: readonly AgentCapability[] = ['ai_cli', 'ai_api', 'storage']
const LOCATIONS: readonly AgentLocation[] = ['remote', 'local']
const NAME_MAX = 100

type RuntimeState = 'not_applicable' | 'unregistered' | 'revoked' | 'incompatible' | 'online' | 'offline'
const RUNTIME_TONE: Record<RuntimeState, string> = {
  not_applicable: 'off',
  unregistered: 'off',
  revoked: 'warn',
  incompatible: 'warn',
  online: 'ok',
  offline: 'off',
}

const { t } = useI18n()
const systemNav = useSystemNav()
const { toasts, pushToast, dismissToast } = useSettingsToasts()

const crumbs = computed(() => [
  { label: t('gui.settings.title'), to: '/settings' },
  { label: t('gui.settings.scope.system') },
  { label: t('gui.settings.system_nav.agents') },
])

const message = (err: unknown) => (err instanceof Error ? err.message : String(err))

function isAgent(value: unknown): value is SystemAgent {
  const a = value as Partial<SystemAgent> | null
  return !!a && typeof a.agent_id === 'string' && typeof a.name === 'string' && typeof a.enabled === 'boolean'
    && (a.location === 'local' || a.location === 'remote')
    && !!a.configured_capabilities && !!a.reported_capabilities && !!a.effective_capabilities
    && typeof a.registration_status === 'string' && typeof a.credential_status === 'string'
    && typeof a.online_status === 'string' && typeof a.has_registration_history === 'boolean'
}

/** One badge for the list: the runtime facts in the order they decide what the Agent can do. */
function runtimeState(a: SystemAgent): RuntimeState {
  if (a.location === 'local') return 'not_applicable'
  if (a.registration_status === 'unregistered') return 'unregistered'
  if (a.registration_status === 'revoked') return 'revoked'
  if (a.protocol_compatibility === 'incompatible') return 'incompatible'
  return a.online_status === 'online' ? 'online' : 'offline'
}

function onOff(value: boolean | null): string {
  return value === null ? t('gui.settings.agents.caps_none') : t(value ? 'gui.settings.agents.on' : 'gui.settings.agents.off')
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
const { user } = useCurrentUser()
/** Thrown by live() for an answer of an ended epoch; the caller just stops. */
const STALE = Symbol('stale')
let epoch = 0
let disposed = false

// The same token check the auth guard makes (core/router/guard).
const hasToken = () => !!(window.__accessToken__ || sessionStorage.getItem('fg_access_token'))

/** Epoch `at` is still this screen, this person and an administrator with a token. */
function sameSession(at: number): boolean {
  if (disposed || at !== epoch) return false
  if (hasToken() && currentSettingsAccess(route).kind === 'allow') return true
  expire()
  return false
}

/** End the epoch: answers still out are dropped, and the token and the open question go. */
function expire() {
  epoch += 1
  clearEnrollment()
  pending.value = null
  busy.value = false
  saving.value = false
  loading.value = false
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
watch(() => `${user.value?.username ?? ''}|${currentSettingsAccess(route).kind}`, () => expire(), { flush: 'sync' })

/**
 * GET /system/agents in epoch `at`. False when it failed: the error is kept (above a list already
 * on screen, or instead of the list on first load) and the list on screen is the last one read.
 * Retrying repeats only this GET, never a change. Throws STALE when the epoch ended.
 */
async function read(at: number): Promise<boolean> {
  loading.value = true
  listError.value = ''
  try {
    const list = await live(at, listSystemAgents())
    if (!Array.isArray(list) || !list.every(isAgent)) throw new Error(t('gui.settings.agents.errors.bad_response'))
    agents.value = list
    const current = list.find((a) => a.agent_id === selectedId.value)
    if (selectedId.value && !current) {
      selectedId.value = null
      clearEnrollment()
    } else if (current && enrollment.value?.agent_id === current.agent_id && current.enrollment_status !== 'pending') {
      clearEnrollment() // consumed or revoked meanwhile: the token is of no use any more
    }
    if (current && mode.value === 'view') rebaseEdit(current)
    return true
  } catch (err) {
    if (err === STALE) throw err
    listError.value = message(err)
    return false
  } finally {
    if (at === epoch) loading.value = false
  }
}

/** The first load and the retry buttons. */
function load(): Promise<boolean> {
  return quiet(read(epoch), false)
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
  clearEnrollment()
  syncEdit()
}

// ── 등록 ──
const form = reactive<{ name: string; location: AgentLocation; path: string; enabled: boolean; caps: AgentCapabilities }>(blankForm())
const formError = ref('')

function blankForm() {
  return { name: '', location: 'remote' as AgentLocation, path: '', enabled: true, caps: { ai_cli: false, ai_api: false, storage: false } }
}

const createDirty = computed(() => {
  const blank = blankForm()
  return mode.value === 'create' && (form.name.trim() !== '' || form.location !== blank.location || form.path.trim() !== ''
    || form.enabled !== blank.enabled || CAPS.some((cap) => form.caps[cap]))
})
const createNameProblem = computed(() => nameProblem(form.name))
const createCanSave = computed(() => mode.value === 'create' && !createNameProblem.value && !busy.value)

async function startCreate() {
  if (busy.value || mode.value === 'create') return
  const at = epoch
  if (formDirty.value && !(await ask())) return
  if (!sameSession(at)) return
  discardEdit() // 저장하지 않고 나가기: the typed values are gone, not parked behind the form
  Object.assign(form, blankForm())
  formError.value = ''
  actionError.value = ''
  editSaved.value = false
  clearEnrollment()
  mode.value = 'create'
}

function cancelCreate() {
  if (busy.value) return
  mode.value = 'view'
  formError.value = ''
  syncEdit() // back on the Agent shown before: its confirmed values as last read
}

async function submitCreate() {
  if (!createCanSave.value) return
  const remote = form.location === 'remote'
  const at = epoch
  busy.value = true
  saving.value = true
  formError.value = ''
  try {
    const created = await live(at, createSystemAgent({
      name: form.name.trim(),
      enabled: form.enabled,
      location: form.location,
      connection_mode: remote ? 'agent_pull' : null,
      local_executable_path: remote ? null : form.path.trim() || null,
      configured_capabilities: { ...form.caps },
    }))
    // The server answered 201: leave the form so the same Agent is never registered twice.
    mode.value = 'view'
    Object.assign(form, blankForm())
    if (!isAgent(created)) {
      selectedId.value = null
      actionError.value = ''
      await reloadAfterChange(at)
      pushToast(t('gui.settings.agents.errors.unconfirmed'), 'warning')
      return
    }
    selectedId.value = created.agent_id
    actionError.value = ''
    if (!(await reloadAfterChange(at))) return
    if (!selected.value) {
      actionError.value = t('gui.settings.agents.errors.reread_mismatch')
      return
    }
    syncEdit()
    pushToast(t('gui.settings.agents.created', { name: created.name }))
  } catch (err) {
    if (err === STALE) return
    formError.value = message(err) // the form stays as typed
    await quiet(read(at), false)
  } finally {
    if (at === epoch) {
      busy.value = false
      saving.value = false
    }
  }
}

// ── 이름 · 실행 경로 · capability ──
const edit = reactive<{ name: string; path: string; caps: AgentCapabilities }>({ name: '', path: '', caps: { ai_cli: false, ai_api: false, storage: false } })
const editSaved = ref(false)

/** The confirmed values the form stands on: the server's last answer for the Agent in the form. */
interface EditValues { name: string; path: string | null; caps: AgentCapabilities }
const editBase = ref<{ agentId: string; values: EditValues } | null>(null)

function confirmedValues(a: SystemAgent): EditValues {
  return { name: a.name, path: a.local_executable_path, caps: { ...a.configured_capabilities } }
}

const nameTouched = (base: EditValues) => edit.name.trim() !== base.name
const pathTouched = (base: EditValues) => (edit.path.trim() || null) !== base.path
const capTouched = (base: EditValues, cap: AgentCapability) => edit.caps[cap] !== base.caps[cap]

/** Put the confirmed values of the selected Agent into the form; nothing typed is kept. */
function syncEdit() {
  const a = selected.value
  if (!a) return
  const values = confirmedValues(a)
  editBase.value = { agentId: a.agent_id, values }
  edit.name = values.name
  edit.path = values.path ?? ''
  edit.caps = { ...values.caps }
}

/** 저장하지 않고 나가기: the typed values are thrown away. */
function discardEdit() {
  editBase.value = null
  edit.name = ''
  edit.path = ''
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
  if (a.location === 'local' && pathTouched(base.values)) changes.local_executable_path = edit.path.trim() || null
  if (CAPS.some((cap) => capTouched(base.values, cap))) changes.configured_capabilities = { ...edit.caps }
  return changes
})

const editDirty = computed(() => Object.keys(editChanges.value).length > 0)
const editNameProblem = computed(() => (selected.value ? nameProblem(edit.name) : ''))
const editCanSave = computed(() => editDirty.value && !editNameProblem.value && !busy.value)

/** The Agent shows every change that was asked for. */
function applied(a: SystemAgent, changes: SystemAgentChanges): boolean {
  return (changes.name === undefined || a.name === changes.name)
    && (changes.enabled === undefined || a.enabled === changes.enabled)
    && (changes.local_executable_path === undefined || a.local_executable_path === changes.local_executable_path)
    && (changes.configured_capabilities === undefined
      || CAPS.every((cap) => a.configured_capabilities[cap] === changes.configured_capabilities![cap]))
}

/** One PATCH with `changes`; done only when its answer and the re-read both show them. */
async function patchAgent(at: number, a: SystemAgent, changes: SystemAgentChanges): Promise<boolean> {
  clearEnrollment() // the Agent is being changed: a token shown for it goes
  const answered = await live(at, updateSystemAgent(a.agent_id, changes))
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
  const changes = editChanges.value
  const at = epoch
  busy.value = true
  saving.value = true
  actionError.value = ''
  editSaved.value = false
  try {
    if (await patchAgent(at, a, changes)) {
      syncEdit()
      editSaved.value = true
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

// ── 사용 중지 · 다시 사용 (enabled 만) ──
async function setEnabled(at: number, next: boolean) {
  const a = selected.value
  if (!a) return
  saving.value = true
  try {
    if (await patchAgent(at, a, { enabled: next })) {
      pushToast(t(next ? 'gui.settings.agents.enabled_done' : 'gui.settings.agents.disabled_done', { name: a.name }))
    }
  } finally {
    if (at === epoch) saving.value = false
  }
}

async function enableAgent() {
  if (busy.value) return
  const at = epoch
  busy.value = true
  actionError.value = ''
  try {
    await setEnabled(at, true)
  } catch (err) {
    if (err === STALE) return
    actionError.value = message(err)
    await quiet(read(at), false)
  } finally {
    if (at === epoch) busy.value = false
  }
}

// ── 확인이 필요한 동작 ──
interface Pending {
  kind: 'disable' | 'issue' | 'revoke' | 'delete'
  icon: SettingsIconName
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

function askDisable() {
  const a = selected.value
  if (!a || busy.value) return
  pending.value = {
    kind: 'disable',
    icon: 'pause',
    tone: 'warn',
    title: `${a.name} · ${t('gui.settings.agents.disable')}`,
    effects: [
      t('gui.settings.agents.effects.disable_blocks'),
      t('gui.settings.agents.effects.disable_keeps'),
      t('gui.settings.agents.effects.disable_no_cancel'),
    ],
    label: t('gui.settings.agents.disable'),
    run: (at) => setEnabled(at, false),
  }
}

function askIssue() {
  const a = selected.value
  if (!a || busy.value) return
  pending.value = {
    kind: 'issue',
    icon: 'key',
    tone: 'warn',
    title: `${a.name} · ${t('gui.settings.agents.issue')}`,
    effects: [
      t(a.enrollment_status === 'pending' ? 'gui.settings.agents.effects.issue_replaces_pending' : 'gui.settings.agents.effects.issue_replaces_any'),
      ...(a.credential_status === 'active' ? [t('gui.settings.agents.effects.issue_replaces_credential')] : []),
      t('gui.settings.agents.effects.issue_once'),
    ],
    label: t('gui.settings.agents.issue_confirm'),
    run: async (at) => {
      const issued = await live(at, issueAgentEnrollment(a.agent_id))
      if (!issued || typeof issued.enrollment_token !== 'string' || issued.agent_id !== a.agent_id) {
        actionError.value = t('gui.settings.agents.errors.unconfirmed')
        await read(at)
        return
      }
      // Shown once, for the Agent it was issued for, whatever the re-read does: no GET returns it.
      // live() already dropped an answer that came after the screen or the session was gone.
      if (mode.value === 'view' && selectedId.value === a.agent_id) {
        enrollment.value = { agent_id: issued.agent_id, enrollment_token: issued.enrollment_token, expires_at: issued.expires_at }
      }
      await reloadAfterChange(at)
    },
  }
}

function askRevoke() {
  const a = selected.value
  if (!a || busy.value) return
  pending.value = {
    kind: 'revoke',
    icon: 'ban',
    tone: 'danger',
    title: `${a.name} · ${t('gui.settings.agents.revoke')}`,
    effects: [
      t('gui.settings.agents.effects.revoke_credential'),
      t('gui.settings.agents.effects.revoke_pending'),
      t('gui.settings.agents.effects.revoke_reenroll'),
      t('gui.settings.agents.effects.revoke_history'),
    ],
    label: t('gui.settings.agents.revoke'),
    run: async (at) => {
      clearEnrollment()
      const answered = await live(at, revokeAgentCredential(a.agent_id))
      if (!isAgent(answered) || answered.credential_status === 'active' || answered.enrollment_status === 'pending') {
        actionError.value = t('gui.settings.agents.errors.unconfirmed')
        await read(at)
        return
      }
      if (!(await reloadAfterChange(at))) return
      const fresh = selected.value
      if (!fresh || fresh.credential_status === 'active' || fresh.enrollment_status === 'pending') {
        actionError.value = t('gui.settings.agents.errors.reread_mismatch')
        return
      }
      pushToast(t('gui.settings.agents.revoked_done', { name: a.name }))
    },
  }
}

function askDelete() {
  const a = selected.value
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
      clearEnrollment()
      await live(at, deleteSystemAgent(a.agent_id))
      if (!(await reloadAfterChange(at))) return
      if (agents.value?.some((x) => x.agent_id === a.agent_id)) {
        actionError.value = t('gui.settings.agents.errors.reread_mismatch')
        return
      }
      pushToast(t('gui.settings.agents.deleted_done', { name: a.name }))
    },
  }
}

// ── enrollment token: this memory only ──
const enrollment = ref<AgentEnrollment | null>(null)
const secretRef = ref<HTMLElement | null>(null)

const shownEnrollment = computed(() =>
  enrollment.value && selected.value && enrollment.value.agent_id === selected.value.agent_id ? enrollment.value : null)

function clearEnrollment() {
  enrollment.value = null
}

async function copyToken() {
  const value = shownEnrollment.value?.enrollment_token
  if (!value) return
  const at = epoch
  const copied = await copyToClipboard(value, secretRef.value)
  if (!sameSession(at)) return
  if (copied) {
    pushToast(t('gui.settings.users.copied'))
  } else {
    pushToast(t('gui.settings.users.copy_failed'), 'warning')
  }
}

// ── 미저장 이탈 보호 ──
const formDirty = computed(() => (mode.value === 'create' ? createDirty.value : editDirty.value))
const { open: leaveOpen, answer: answerLeave, ask } = useUnsavedLeaveGuard(() => formDirty.value || saving.value)

onMounted(() => {
  void load()
})

onBeforeUnmount(() => {
  disposed = true
  expire() // the token goes with the screen, and so does every answer still out
})
</script>

<style scoped>
.fgs-refresh-error {
  margin-bottom: 12px;
}

.agents-grid {
  display: grid;
  grid-template-columns: minmax(240px, 32%) 1fr;
  gap: 16px;
  align-items: start;
}

.agents-list {
  overflow: hidden;
}

.agent-row {
  width: 100%;
  border: 0;
  border-bottom: 1px solid var(--fg2-border-subtle, #22344a);
  background: transparent;
  color: inherit;
  padding: 12px 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  text-align: left;
  cursor: pointer;
}

.agent-row.sel {
  background: rgba(59, 130, 246, 0.12);
}

.agent-row:disabled {
  cursor: default;
}

.agent-row .who {
  display: grid;
  gap: 3px;
  min-width: 0;
}

.agent-row small {
  color: #8398ae;
}

.agent-row .badges {
  display: inline-flex;
  gap: 6px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.agent-detail .pad,
.agent-detail .sec {
  padding: 14px 16px;
}

.agent-detail .sec {
  border-top: 1px solid var(--fg2-border-subtle, #22344a);
  display: grid;
  gap: 10px;
}

.agent-detail .sec h3 {
  margin: 0;
  font-size: 13px;
  font-weight: 700;
}

.row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.mono {
  font-family: var(--fg2-font-mono, ui-monospace, monospace);
  word-break: break-all;
}

.caps {
  display: flex;
  gap: 18px;
  flex-wrap: wrap;
}

.cap {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
}

.kv {
  display: grid;
  grid-template-columns: 150px 1fr;
  gap: 7px 12px;
  margin: 0;
  font-size: 13px;
}

.kv dt {
  color: #8398ae;
}

.kv dd {
  margin: 0;
}

.caps-table {
  display: grid;
  font-size: 12px;
}

.caps-tr {
  display: grid;
  grid-template-columns: 110px repeat(3, 1fr);
  gap: 8px;
  padding: 5px 0;
  border-bottom: 1px solid var(--fg2-border-subtle, #22344a);
}

.caps-tr.head {
  color: #8398ae;
}

.enrollment {
  display: grid;
  gap: 8px;
}

.enrollment .fgs-secret {
  user-select: all;
  word-break: break-all;
}

.confirm {
  display: grid;
  justify-items: center;
  gap: 12px;
  padding: 10px 0 4px;
  text-align: center;
}

.conf-ico {
  width: 64px;
  height: 64px;
  border-radius: 18px;
  display: grid;
  place-items: center;
}

.conf-ico.warn { background: #2a210e; color: #e2c76f; }
.conf-ico.danger { background: #2a1419; color: #ff8c94; }

.conf-who {
  font-size: 15px;
  font-weight: 700;
}

.conf-text {
  margin: 0;
  font-size: 13px;
  line-height: 1.5;
}

.effects {
  margin: 0;
  padding-left: 18px;
  text-align: left;
  font-size: 13px;
  line-height: 1.6;
}

@media (max-width: 800px) {
  .agents-grid {
    grid-template-columns: 1fr;
  }
}
</style>
