<template>
  <SettingsShell scope="workspace">
    <template #nav>
      <SettingsSubnav
        :title="detail?.name ?? ''"
        :items="tabItems"
        :active="tab"
        :back="{ to: '/settings/workspaces', label: t('gui.settings.workspaces.list') }"
        @select="selectTab"
      >
        <template #mark><div class="workspace-mark">{{ initials(detail?.name) }}</div></template>
      </SettingsSubnav>
    </template>

    <LoadingState v-if="loading && !detail" :label="t('gui.common.loading')" />
    <ErrorState v-else-if="error && !detail" :title="t('gui.settings.errors.load_failed')" :message="error">
      <template #action>
        <Button variant="secondary" @click="load">{{ t('gui.common.retry') }}</Button>
      </template>
    </ErrorState>

    <template v-else-if="detail">
      <!-- ── 멤버 ── -->
      <section v-if="tab === 'members'" class="fgs-panel" data-panel="members">
        <div class="fgs-phead">
          <span class="fgs-tico teal"><SettingsIcon name="users" size="lg" /></span>
          <h2>{{ t('gui.settings.detail.members') }}</h2>
          <div class="fgs-push">
            <button v-if="detail.can_manage" type="button" class="fgs-btn pri" data-testid="member-add" @click="openAdd">
              <SettingsIcon name="plus" />{{ t('gui.settings.detail.add_member') }}
            </button>
          </div>
        </div>
        <div class="fgs-panel-body">
          <div class="fgs-glance" style="--n:3">
            <div class="fgs-tile">
              <span class="fgs-tico teal"><SettingsIcon name="users" /></span>
              <div class="tbody">
                <div class="big" data-testid="member-total">{{ detail.members.length }}</div>
                <div class="fgs-sbar">
                  <i class="fgs-c-pur" :style="{ width: pct(roleCount.owner) }" />
                  <i class="fgs-c-blue" :style="{ width: pct(roleCount.admin) }" />
                  <i class="fgs-c-member" :style="{ width: pct(roleCount.member) }" />
                </div>
                <div class="fgs-legend">
                  <span><span class="fgs-dot fgs-c-pur" />Owner {{ roleCount.owner }}</span>
                  <span><span class="fgs-dot fgs-c-blue" />Admin {{ roleCount.admin }}</span>
                  <span><span class="fgs-dot fgs-c-member" />Member {{ roleCount.member }}</span>
                </div>
              </div>
            </div>
            <div class="fgs-tile">
              <span class="fgs-tico ok"><SettingsIcon name="check" /></span>
              <div class="tbody">
                <div class="big">{{ stateCount.active }}<small>/ {{ detail.members.length }}</small></div>
                <div class="fgs-sbar">
                  <i class="fgs-c-ok" :style="{ width: pct(stateCount.active) }" />
                  <i class="fgs-c-warn" :style="{ width: pct(stateCount.invited) }" />
                </div>
                <div class="fgs-legend">
                  <span><span class="fgs-dot fgs-c-ok" />{{ t('gui.settings.state.active') }} {{ stateCount.active }}</span>
                  <span><span class="fgs-dot fgs-c-warn" />{{ t('gui.settings.state.invited') }} {{ stateCount.invited }}</span>
                  <span v-if="stateCount.disabled"><span class="fgs-dot fgs-c-off" />{{ t('gui.settings.state.disabled') }} {{ stateCount.disabled }}</span>
                </div>
              </div>
            </div>
            <button type="button" class="fgs-tile" data-testid="to-projects" @click="selectTab('projects')">
              <span class="fgs-tico"><SettingsIcon name="folder" /></span>
              <div class="tbody">
                <div class="big">{{ detail.project_count }}</div>
                <div class="lbl">{{ t('gui.settings.workspaces.projects') }} <SettingsIcon name="chev" size="sm" /></div>
              </div>
            </button>
          </div>

          <div class="fgs-bar">
            <label class="fgs-search">
              <SettingsIcon name="search" class="fgs-search-icon" />
              <input v-model="memberQuery" class="fgs-input" :placeholder="t('gui.settings.search_people')" data-testid="member-search" />
            </label>
            <div class="fgs-chips">
              <button
                v-for="chip in roleChips"
                :key="chip.value"
                type="button"
                class="fgs-chip"
                :class="{ on: roleFilter === chip.value }"
                :data-role-filter="chip.value || 'all'"
                @click="roleFilter = chip.value"
              >
                <SettingsIcon v-if="chip.icon" :name="chip.icon" size="sm" />{{ chip.label }}
              </button>
            </div>
            <span class="fgs-vsep" />
            <button type="button" class="fgs-chip warn" :class="{ on: invitedOnly }" data-testid="invited-filter" @click="invitedOnly = !invitedOnly">
              <span class="fgs-dot fgs-c-warn" />{{ t('gui.settings.state.invited') }}
            </button>
            <span class="fgs-spacer" />
            <button type="button" class="fgs-iconbtn" :title="t('gui.settings.csv')" :aria-label="t('gui.settings.csv')" @click="exportMembers">
              <SettingsIcon name="download" />
            </button>
          </div>

          <div class="fgs-dtable" style="--cols: minmax(230px, 1.6fr) 120px 100px 110px 110px 24px">
            <div class="fgs-dtr head">
              <div>{{ t('gui.settings.detail.col_member') }}</div>
              <div>{{ t('gui.settings.detail.col_role') }}</div>
              <div>{{ t('gui.settings.detail.col_state') }}</div>
              <div>{{ t('gui.settings.workspaces.projects') }}</div>
              <div>{{ t('gui.settings.detail.col_activity') }}</div>
              <div />
            </div>
            <div
              v-for="member in filteredMembers"
              :key="member.user_id"
              class="fgs-dtr row"
              :class="{ static: !detail.can_manage, dim: member.state === 'disabled' }"
              :data-member="member.user_id"
              @click="openMember(member)"
            >
              <div class="fgs-who">
                <div class="fgs-av">{{ initials(member.username) }}</div>
                <div><b>{{ member.username }}</b><span>{{ member.email }}</span></div>
              </div>
              <div><span class="fgs-pill" :class="rolePillClass(member.role)"><SettingsIcon :name="roleIcon(member.role)" size="sm" />{{ roleLabel(member.role) }}</span></div>
              <div><span class="fgs-st"><span class="fgs-dot" :class="stateDot(member.state)" />{{ t(`gui.settings.state.${member.state}`) }}</span></div>
              <div>
                <span class="pdots" :title="t('gui.settings.detail.projects_of', { n: member.project_count, total: detail.active_project_count })">
                  <template v-if="detail.active_project_count <= 8">
                    <i v-for="n in detail.active_project_count" :key="n" :class="{ on: n <= member.project_count }" />
                  </template>
                  <span v-else class="fgs-muted">{{ member.project_count }} / {{ detail.active_project_count }}</span>
                </span>
              </div>
              <div class="fgs-muted">{{ relativeTime(member.last_login_at, locale) }}</div>
              <div class="fgs-chev"><SettingsIcon v-if="detail.can_manage" name="chev" /></div>
            </div>
            <div v-if="!filteredMembers.length" class="fgs-empty">{{ t('gui.settings.no_match') }}</div>
          </div>
          <div class="fgs-tablefoot" data-testid="member-foot">{{ filteredMembers.length }} / {{ detail.members.length }}</div>
        </div>
      </section>

      <!-- ── 프로젝트 접근 ── -->
      <section v-else-if="tab === 'projects'" class="fgs-panel" data-panel="projects">
        <div class="fgs-phead">
          <span class="fgs-tico"><SettingsIcon name="folder" size="lg" /></span>
          <h2>{{ t('gui.settings.detail.project_access') }}</h2>
          <div class="fgs-push">
            <span class="fgs-autosave" :class="autosave.state.value"><span class="fgs-dot" />{{ autosaveLabel }}</span>
          </div>
        </div>
        <div class="fgs-panel-body">
          <div class="fgs-card">
            <div class="fgs-card-h"><span class="fgs-tico sm"><SettingsIcon name="gear" /></span><b>{{ t('gui.settings.detail.default_access') }}</b></div>
            <div v-for="role in ROLES" :key="role" class="fgs-line" :data-default-role="role">
              <div class="k"><span class="fgs-pill" :class="rolePillClass(role)"><SettingsIcon :name="roleIcon(role)" size="sm" />{{ roleLabel(role) }}</span></div>
              <div class="v end">
                <div class="fgs-segment" role="radiogroup">
                  <button
                    v-for="level in ACCESS"
                    :key="level"
                    type="button"
                    :class="{ on: detail.access.defaults[role] === level }"
                    :title="t(`gui.settings.access_title.${level}`)"
                    :disabled="!detail.can_manage || busy"
                    :data-access="level"
                    @click="saveDefault(role, level)"
                  >
                    <SettingsIcon :name="accessIcon(level)" size="sm" />{{ t(`gui.settings.access.${level}`) }}
                  </button>
                </div>
              </div>
            </div>
          </div>

          <div class="fgs-card">
            <div class="fgs-card-h">
              <span class="fgs-tico sm warn"><SettingsIcon name="user" /></span>
              <b>{{ t('gui.settings.detail.exceptions') }}</b>
              <span class="fgs-pill warn" data-testid="exception-count">{{ exceptionCount }}</span>
              <div class="fgs-push">
                <div class="fgs-chips">
                  <button
                    v-for="project in detail.projects"
                    :key="project.project_id"
                    type="button"
                    class="fgs-chip"
                    :class="{ on: project.project_id === selectedProject }"
                    :data-project="project.project_id"
                    @click="selectedProject = project.project_id"
                  >
                    <SettingsIcon v-if="project.project_id === selectedProject" name="folder" size="sm" />{{ project.project_name }}
                  </button>
                </div>
              </div>
            </div>
            <p v-if="!detail.projects.length" class="fgs-empty">{{ t('gui.settings.detail.no_projects') }}</p>
            <div v-for="member in (selectedProject ? detail.members : [])" :key="member.user_id" class="fgs-line" :data-exception-member="member.user_id">
              <div class="k"><div class="fgs-who"><div class="fgs-av sm">{{ initials(member.username) }}</div><b>{{ member.username }}</b></div></div>
              <div class="v">
                <span class="fgs-pill" :class="rolePillClass(member.role)"><SettingsIcon :name="roleIcon(member.role)" size="sm" />{{ roleLabel(member.role) }}</span>
                <span class="fgs-spacer" />
                <span class="excmark"><span v-if="exceptionOf(member.user_id)" class="exc" :title="t('gui.settings.detail.exception')" /></span>
                <div class="fgs-segment" role="radiogroup">
                  <button
                    type="button"
                    :class="{ on: !exceptionOf(member.user_id) }"
                    :title="t('gui.settings.detail.role_default')"
                    :disabled="!detail.can_manage || busy"
                    data-access="default"
                    @click="saveException(member.user_id, 'default')"
                  >
                    {{ t('gui.settings.detail.default_is', { access: t(`gui.settings.access.${detail.access.defaults[member.role]}`) }) }}
                  </button>
                  <button
                    v-for="level in ACCESS"
                    :key="level"
                    type="button"
                    :class="{ on: exceptionOf(member.user_id) === level }"
                    :title="t(`gui.settings.access_title.${level}`)"
                    :disabled="!detail.can_manage || busy"
                    :data-access="level"
                    @click="saveException(member.user_id, level)"
                  >
                    <SettingsIcon :name="accessIcon(level)" size="sm" />{{ t(`gui.settings.access.${level}`) }}
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- ── 공용 정책 ── -->
      <section v-else class="fgs-panel" data-panel="policy">
        <div class="fgs-phead">
          <span class="fgs-tico"><SettingsIcon name="shield" size="lg" /></span>
          <h2>{{ t('gui.settings.detail.policy') }}</h2>
          <div class="fgs-push">
            <span class="fgs-autosave" :class="autosave.state.value"><span class="fgs-dot" />{{ autosaveLabel }}</span>
          </div>
        </div>
        <div class="fgs-panel-body">
          <div class="fgs-card">
            <div class="fgs-card-h">
              <span class="fgs-tico sm"><SettingsIcon name="bot" /></span>
              <b>{{ t('gui.settings.detail.ai_usable') }}</b>
              <div class="fgs-push">
                <div class="fgs-segment" role="radiogroup">
                  <button type="button" :class="{ on: detail.policy.ai_mode === 'all' }" :disabled="!detail.can_manage || busy" data-ai-mode="all" @click="savePolicy({ ai_mode: 'all' })">
                    <SettingsIcon name="globe" size="sm" />{{ t('gui.settings.detail.ai_all') }}
                  </button>
                  <button type="button" :class="{ on: detail.policy.ai_mode === 'allow' }" :disabled="!detail.can_manage || busy" data-ai-mode="allow" @click="savePolicy({ ai_mode: 'allow' })">
                    <SettingsIcon name="check" size="sm" />{{ t('gui.settings.detail.ai_selected') }}
                  </button>
                </div>
              </div>
            </div>
            <template v-if="detail.policy.ai_mode === 'allow'">
              <div v-for="kind in RESOURCE_KINDS" :key="kind.type" class="fgs-line" :data-allow-kind="kind.type">
                <div class="k"><SettingsIcon :name="kind.icon" />{{ kind.label }}</div>
                <div class="v">
                  <span v-for="entry in allowOf(kind.type)" :key="entry.resource_id" class="allowchip" :class="{ unavailable: !entry.available }">
                    {{ entry.name }}
                    <button
                      v-if="detail.can_manage"
                      type="button"
                      :title="t('gui.settings.detail.remove_chip')"
                      :aria-label="t('gui.settings.detail.remove_chip')"
                      :disabled="busy"
                      @click="revoke(entry.resource_type, entry.resource_id)"
                    ><SettingsIcon name="x" size="sm" /></button>
                  </span>
                  <span v-if="!allowOf(kind.type).length" class="fgs-muted">{{ t('gui.settings.detail.none_allowed') }}</span>
                  <button v-if="detail.can_manage" type="button" class="addchip" :disabled="busy" :data-add-kind="kind.type" @click="openPicker(kind.type)">
                    <SettingsIcon name="plus" size="sm" />{{ t('gui.settings.detail.add') }}
                  </button>
                </div>
              </div>
            </template>
            <p v-else class="policy-note fgs-muted">{{ t('gui.settings.detail.ai_all_note') }}</p>
          </div>

          <div class="fgs-card">
            <div class="fgs-card-h"><span class="fgs-tico sm"><SettingsIcon name="folder" /></span><b>{{ t('gui.settings.detail.project_creators') }}</b></div>
            <div class="fgs-line">
              <div class="fgs-choice-grid" role="radiogroup">
                <button
                  v-for="choice in CREATE_CHOICES"
                  :key="choice.value"
                  type="button"
                  class="fgs-choice"
                  :class="{ on: detail.policy.project_create === choice.value }"
                  :disabled="!detail.can_manage || busy"
                  :data-project-create="choice.value"
                  @click="savePolicy({ project_create: choice.value })"
                >
                  <span class="fgs-mini"><i v-for="n in 4" :key="n" :class="{ t: n <= choice.bars }" /></span>
                  <b>{{ t(`gui.settings.detail.create.${choice.value}`) }}</b>
                </button>
              </div>
            </div>
          </div>
        </div>
      </section>
    </template>

    <!-- 멤버 추가 -->
    <ModalShell :open="addOpen" size="sm" :aria-label="t('gui.settings.detail.add_member')" @close="addOpen = false">
      <template #header><h2 class="fgs-modal-title">{{ t('gui.settings.detail.add_member') }}</h2></template>
      <div class="fgs-formfield">
        <span class="label">{{ t('gui.settings.detail.user') }}</span>
        <label class="fgs-search" style="flex: none">
          <SettingsIcon name="search" class="fgs-search-icon" />
          <input v-model="candidateQuery" class="fgs-input" :placeholder="t('gui.settings.search_people')" data-testid="candidate-search" @input="searchCandidates" />
        </label>
        <div class="candidates" role="listbox">
          <button
            v-for="candidate in candidates"
            :key="candidate.user_id"
            type="button"
            class="candidate"
            role="option"
            :class="{ on: candidate.user_id === pickedCandidate }"
            :aria-selected="candidate.user_id === pickedCandidate"
            :data-candidate="candidate.user_id"
            @click="pickedCandidate = candidate.user_id"
          >
            <span class="fgs-av sm">{{ initials(candidate.username) }}</span>
            <span><b>{{ candidate.username }}</b><small>{{ candidate.email }}</small></span>
          </button>
          <p v-if="!candidates.length" class="fgs-muted">{{ t('gui.settings.no_match') }}</p>
        </div>
      </div>
      <div class="fgs-formfield">
        <span class="label">{{ t('gui.settings.detail.col_role') }}</span>
        <div class="fgs-chips">
          <button type="button" class="fgs-chip" :class="{ on: addRole === 'member' }" data-add-role="member" @click="addRole = 'member'">
            <SettingsIcon name="user" size="sm" />Member
          </button>
          <button type="button" class="fgs-chip" :class="{ on: addRole === 'admin' }" data-add-role="admin" @click="addRole = 'admin'">
            <SettingsIcon name="star" size="sm" />Admin
          </button>
        </div>
      </div>
      <RouterLink v-if="user?.isAdmin" to="/settings/system/users?create=1" class="fgs-link">
        <SettingsIcon name="plus" size="sm" />{{ t('gui.settings.detail.new_account') }}
      </RouterLink>
      <p v-if="formError" class="fgs-error" role="alert">{{ formError }}</p>
      <template #footer>
        <div class="fgs-modal-actions">
          <button type="button" class="fgs-btn" @click="addOpen = false">{{ t('gui.common.cancel') }}</button>
          <button type="button" class="fgs-btn pri" :disabled="busy || !pickedCandidate" data-testid="member-add-confirm" @click="submitAdd">
            {{ t('gui.settings.detail.add_confirm') }}
          </button>
        </div>
      </template>
    </ModalShell>

    <!-- 멤버 메뉴 -->
    <ModalShell :open="!!memberMenu" size="sm" :aria-label="memberMenu?.username" @close="memberMenu = null">
      <template #header>
        <div class="member-head"><div class="fgs-av">{{ initials(memberMenu?.username) }}</div><h2 class="fgs-modal-title" style="padding: 0">{{ memberMenu?.username }}</h2></div>
      </template>
      <div v-if="memberMenu" class="fgs-actlist">
        <div class="fgs-actrow">
          <span class="fgs-tico sm"><SettingsIcon name="star" /></span>
          <b>{{ t('gui.settings.detail.col_role') }}</b>
          <span class="fgs-spacer" />
          <div class="fgs-chips">
            <button
              v-for="role in (['admin', 'member'] as const)"
              :key="role"
              type="button"
              class="fgs-chip"
              :class="{ on: memberMenu.role === role }"
              :disabled="memberMenu.role === 'owner' || busy"
              :data-member-role="role"
              @click="changeRole(role)"
            >
              <SettingsIcon :name="roleIcon(role)" size="sm" />{{ roleLabel(role) }}
            </button>
          </div>
        </div>
        <div class="fgs-actrow">
          <span class="fgs-tico sm danger"><SettingsIcon name="logout" /></span>
          <b>{{ t('gui.settings.detail.remove') }}</b>
          <span class="fgs-spacer" />
          <button type="button" class="fgs-btn sm danger" :disabled="memberMenu.role === 'owner' || busy" data-testid="member-remove" @click="confirmRemove = true">
            {{ t('gui.settings.detail.remove_short') }}
          </button>
        </div>
        <p v-if="memberMenu.role === 'owner'" class="fgs-muted">{{ t('gui.settings.detail.owner_locked') }}</p>
        <p v-if="formError" class="fgs-error" role="alert">{{ formError }}</p>
      </div>
      <template #footer>
        <div class="fgs-modal-actions"><button type="button" class="fgs-btn" @click="memberMenu = null">{{ t('gui.common.close') }}</button></div>
      </template>
    </ModalShell>

    <ConfirmDialog
      :open="confirmRemove"
      danger
      :title="t('gui.settings.detail.remove_title', { name: memberMenu?.username ?? '' })"
      :message="t('gui.settings.detail.remove_message')"
      :confirm-label="t('gui.settings.detail.remove_short')"
      :cancel-label="t('gui.common.cancel')"
      @confirm="submitRemove"
      @cancel="confirmRemove = false"
    />

    <!-- AI / Agent 추가 -->
    <ModalShell :open="!!pickerKind" size="sm" :aria-label="t('gui.settings.detail.add')" @close="pickerKind = null">
      <template #header><h2 class="fgs-modal-title">{{ pickerKind === 'agent' ? 'Agent' : 'AI' }} · {{ t('gui.settings.detail.add') }}</h2></template>
      <p class="fgs-muted">{{ t('gui.settings.detail.picker_hint') }}</p>
      <div class="candidates">
        <button
          v-for="resource in pickerOptions"
          :key="resource.resource_id"
          type="button"
          class="candidate"
          :disabled="busy"
          :data-resource="resource.resource_id"
          @click="allow(resource.resource_type, resource.resource_id)"
        >
          <SettingsIcon :name="resource.resource_type === 'agent' ? 'agent' : 'bot'" />
          <span><b>{{ resource.name }}</b><small v-if="!resource.enabled">{{ t('gui.settings.detail.resource_disabled') }}</small></span>
        </button>
        <p v-if="!pickerOptions.length" class="fgs-muted">{{ t('gui.settings.detail.no_resources') }}</p>
      </div>
      <template #footer>
        <div class="fgs-modal-actions"><button type="button" class="fgs-btn" @click="pickerKind = null">{{ t('gui.common.close') }}</button></div>
      </template>
    </ModalShell>

    <ToastHost :toasts="toasts" :dismiss-label="t('gui.common.close')" placement="top" @dismiss="dismissToast" />
  </SettingsShell>
</template>

<script setup lang="ts">
// 시안 12 워크스페이스 상세. 멤버: role/state/project counts, add (existing users), change role,
// remove (also leaves every project of the workspace). 프로젝트 접근: role default access and
// per-person exceptions per project — saved at once and materialised by the server into the
// project roles. 공용 정책: AI usable in the workspace (모두 / 선택한 것만 + allowlist) and who may
// create projects. Plain members see everything read-only.
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import Button from '@/components/ui/Button.vue'
import ConfirmDialog from '@/components/ui/ConfirmDialog.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import LoadingState from '@/components/ui/LoadingState.vue'
import ModalShell from '@/components/ui/ModalShell.vue'
import ToastHost from '@/components/ui/ToastHost.vue'
import { useCurrentUser } from '@/core/auth/currentUser'
import SettingsIcon from '../SettingsIcon.vue'
import SettingsShell from '../components/SettingsShell.vue'
import SettingsSubnav, { type SubnavItem } from '../components/SettingsSubnav.vue'
import { useAutosave, useSettingsToasts } from '../components/useSettingsToasts'
import type { SettingsIconName } from '../icons'
import {
  addMember,
  allowResource,
  changeMemberRole,
  getWorkspace,
  listMemberCandidates,
  listPolicyResources,
  removeMember,
  revokeResource,
  setDefaultAccess,
  setProjectAccess,
  setWorkspacePolicy,
} from '../api'
import { downloadCsv, initials, relativeTime, toCsv } from '../format'
import type {
  MemberCandidate,
  PolicyResource,
  ProjectAccess,
  ProjectCreatePolicy,
  ResourceType,
  UserState,
  WorkspaceDetail,
  WorkspaceMember,
  WorkspaceRole,
} from '../types'

type DetailTab = 'members' | 'projects' | 'policy'

const ROLES: WorkspaceRole[] = ['owner', 'admin', 'member']
const ACCESS: ProjectAccess[] = ['admin', 'dev', 'review', 'none']
const CREATE_CHOICES: { value: ProjectCreatePolicy; bars: number }[] = [
  { value: 'owner', bars: 1 },
  { value: 'owner_admin', bars: 2 },
  { value: 'all', bars: 4 },
]

const props = defineProps<{ workspaceId: string }>()
const { t, locale } = useI18n()
const route = useRoute()
const router = useRouter()
const { user } = useCurrentUser()
const { toasts, pushToast, dismissToast } = useSettingsToasts()
const autosave = useAutosave()

const detail = ref<WorkspaceDetail | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)
const busy = ref(false)
const formError = ref<string | null>(null)

const tab = computed<DetailTab>(() => {
  const value = route.query.tab
  return value === 'projects' || value === 'policy' ? value : 'members'
})

const tabItems = computed<SubnavItem[]>(() => [
  { key: 'members', label: t('gui.settings.detail.members'), icon: 'users' },
  { key: 'projects', label: t('gui.settings.detail.project_access'), icon: 'folder' },
  { key: 'policy', label: t('gui.settings.detail.policy'), icon: 'shield' },
])

function selectTab(key: string) {
  void router.replace({ query: { ...route.query, tab: key === 'members' ? undefined : key } })
}

async function load() {
  loading.value = true
  error.value = null
  try {
    detail.value = await getWorkspace(props.workspaceId)
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    loading.value = false
  }
}

/** Run a mutation that answers the fresh detail; errors become a toast. */
async function apply(task: () => Promise<WorkspaceDetail>, message?: string, withAutosave = false): Promise<boolean> {
  busy.value = true
  formError.value = null
  try {
    detail.value = withAutosave ? await autosave.run(task) : await task()
    if (message) pushToast(message)
    return true
  } catch (err) {
    const text = err instanceof Error ? err.message : String(err)
    formError.value = text
    pushToast(text, 'danger')
    return false
  } finally {
    busy.value = false
  }
}

const autosaveLabel = computed(() => {
  if (autosave.state.value === 'saving') return t('gui.settings.saving')
  if (autosave.state.value === 'error') return t('gui.settings.save_failed')
  return t('gui.settings.autosave')
})

// ── labels ──
const roleLabel = (role: WorkspaceRole) => t(`gui.settings.roles.${role}`)
const roleIcon = (role: WorkspaceRole): SettingsIconName => (role === 'owner' ? 'crown' : role === 'admin' ? 'star' : 'user')
const rolePillClass = (role: WorkspaceRole) => (role === 'member' ? '' : role)
const stateDot = (state: UserState) => (state === 'active' ? 'fgs-c-ok' : state === 'invited' ? 'fgs-c-warn' : 'fgs-c-off')
const accessIcon = (level: ProjectAccess): SettingsIconName =>
  level === 'admin' ? 'key' : level === 'dev' ? 'code' : level === 'review' ? 'eye' : 'ban'

// ── members ──
const memberQuery = ref('')
const roleFilter = ref<'' | WorkspaceRole>('')
const invitedOnly = ref(false)

const roleChips = computed(() => [
  { value: '' as const, label: t('gui.settings.filter_all'), icon: null },
  { value: 'owner' as const, label: roleLabel('owner'), icon: 'crown' as SettingsIconName },
  { value: 'admin' as const, label: roleLabel('admin'), icon: 'star' as SettingsIconName },
  { value: 'member' as const, label: roleLabel('member'), icon: 'user' as SettingsIconName },
])

const roleCount = computed(() => {
  const members = detail.value?.members ?? []
  return {
    owner: members.filter((m) => m.role === 'owner').length,
    admin: members.filter((m) => m.role === 'admin').length,
    member: members.filter((m) => m.role === 'member').length,
  }
})

const stateCount = computed(() => {
  const members = detail.value?.members ?? []
  return {
    active: members.filter((m) => m.state === 'active').length,
    invited: members.filter((m) => m.state === 'invited').length,
    disabled: members.filter((m) => m.state === 'disabled').length,
  }
})

function pct(n: number): string {
  const total = detail.value?.members.length ?? 0
  return total ? `${(n / total) * 100}%` : '0%'
}

const filteredMembers = computed(() => {
  const q = memberQuery.value.trim().toLowerCase()
  return (detail.value?.members ?? []).filter((m) =>
    (!q || `${m.username} ${m.email ?? ''}`.toLowerCase().includes(q))
    && (!roleFilter.value || m.role === roleFilter.value)
    && (!invitedOnly.value || m.state === 'invited'),
  )
})

function exportMembers() {
  if (!detail.value) return
  const csv = toCsv(
    ['name', 'email', 'role', 'state', 'projects', 'last_login_at'],
    filteredMembers.value.map((m) => [m.username, m.email, m.role, m.state, m.project_count, m.last_login_at]),
  )
  downloadCsv(`${detail.value.name}-members.csv`, csv)
}

// add
const addOpen = ref(false)
const candidateQuery = ref('')
const candidates = ref<MemberCandidate[]>([])
const pickedCandidate = ref<string | null>(null)
const addRole = ref<'member' | 'admin'>('member')
let searchSeq = 0

async function searchCandidates() {
  const seq = ++searchSeq
  try {
    const found = await listMemberCandidates(props.workspaceId, candidateQuery.value.trim())
    if (seq === searchSeq) candidates.value = found
  } catch (err) {
    if (seq === searchSeq) formError.value = err instanceof Error ? err.message : String(err)
  }
}

function openAdd() {
  candidateQuery.value = ''
  pickedCandidate.value = null
  addRole.value = 'member'
  formError.value = null
  addOpen.value = true
  void searchCandidates()
}

async function submitAdd() {
  const picked = candidates.value.find((c) => c.user_id === pickedCandidate.value)
  if (!picked) return
  if (await apply(() => addMember(props.workspaceId, picked.user_id, addRole.value), t('gui.settings.detail.added', { name: picked.username }))) {
    addOpen.value = false
  }
}

// member menu
const memberMenu = ref<WorkspaceMember | null>(null)
const confirmRemove = ref(false)

function openMember(member: WorkspaceMember) {
  if (!detail.value?.can_manage) return
  formError.value = null
  memberMenu.value = member
}

async function changeRole(role: 'admin' | 'member') {
  const member = memberMenu.value
  if (!member || member.role === role) return
  if (await apply(() => changeMemberRole(props.workspaceId, member.user_id, role), t('gui.settings.detail.role_changed'))) {
    memberMenu.value = detail.value?.members.find((m) => m.user_id === member.user_id) ?? null
  }
}

async function submitRemove() {
  confirmRemove.value = false
  const member = memberMenu.value
  if (!member) return
  if (await apply(() => removeMember(props.workspaceId, member.user_id), t('gui.settings.detail.removed', { name: member.username }))) {
    memberMenu.value = null
  }
}

// ── project access ──
const selectedProject = ref<string | null>(null)

watch(detail, (value) => {
  if (!value) return
  if (!selectedProject.value || !value.projects.some((p) => p.project_id === selectedProject.value)) {
    selectedProject.value = value.projects[0]?.project_id ?? null
  }
})

const exceptionCount = computed(() =>
  (detail.value?.access.exceptions ?? []).filter((e) => e.project_id === selectedProject.value).length,
)

function exceptionOf(userId: string): ProjectAccess | null {
  return detail.value?.access.exceptions.find((e) => e.project_id === selectedProject.value && e.user_id === userId)?.access ?? null
}

function saveDefault(role: WorkspaceRole, level: ProjectAccess) {
  if (detail.value?.access.defaults[role] === level) return
  void apply(() => setDefaultAccess(props.workspaceId, { [role]: level }), undefined, true)
}

function saveException(userId: string, level: ProjectAccess | 'default') {
  const project = selectedProject.value
  if (!project) return
  const current = exceptionOf(userId)
  if ((level === 'default' && !current) || current === level) return
  void apply(() => setProjectAccess(props.workspaceId, project, userId, level), undefined, true)
}

// ── policy ──
const RESOURCE_KINDS = computed(() => [
  { type: 'provider' as ResourceType, label: 'AI', icon: 'bot' as SettingsIconName },
  { type: 'agent' as ResourceType, label: 'Agent', icon: 'agent' as SettingsIconName },
])

const allowOf = (type: ResourceType) => (detail.value?.policy.allowlist ?? []).filter((e) => e.resource_type === type)

function savePolicy(patch: { ai_mode?: 'all' | 'allow'; project_create?: ProjectCreatePolicy }) {
  const policy = detail.value?.policy
  if (!policy) return
  if ((patch.ai_mode && patch.ai_mode === policy.ai_mode) || (patch.project_create && patch.project_create === policy.project_create)) return
  void apply(() => setWorkspacePolicy(props.workspaceId, patch), undefined, true)
}

const pickerKind = ref<ResourceType | null>(null)
const resources = ref<{ providers: PolicyResource[]; agents: PolicyResource[] }>({ providers: [], agents: [] })

const pickerOptions = computed(() => {
  if (!pickerKind.value) return []
  const allowed = new Set(allowOf(pickerKind.value).map((e) => e.resource_id))
  const list = pickerKind.value === 'agent' ? resources.value.agents : resources.value.providers
  return list.filter((r) => !allowed.has(r.resource_id))
})

async function openPicker(kind: ResourceType) {
  pickerKind.value = kind
  try {
    resources.value = await listPolicyResources(props.workspaceId)
  } catch (err) {
    pushToast(err instanceof Error ? err.message : String(err), 'danger')
  }
}

async function allow(type: ResourceType, id: string) {
  if (await apply(() => allowResource(props.workspaceId, type, id), undefined, true)) pickerKind.value = null
}

function revoke(type: ResourceType, id: string) {
  void apply(() => revokeResource(props.workspaceId, type, id), undefined, true)
}

watch(() => props.workspaceId, () => void load())
onMounted(load)
</script>

<style scoped>
.workspace-mark {
  width: 34px;
  height: 34px;
  flex: 0 0 34px;
  border-radius: 9px;
  display: grid;
  place-items: center;
  font-size: 12px;
  font-weight: 800;
  background: #11383e;
  color: #5bdabb;
}

.pdots {
  display: inline-flex;
  gap: 4px;
}

.pdots i {
  width: 9px;
  height: 9px;
  border-radius: 3px;
  background: #22324a;
}

.pdots i.on {
  background: #35c7a4;
}

.excmark {
  width: 10px;
  display: inline-grid;
  place-items: center;
}

.exc {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #f5b84b;
  box-shadow: 0 0 8px rgba(245, 184, 75, 0.5);
}

.allowchip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  border: 1px solid #2f76c7;
  border-radius: 999px;
  background: #102b4c;
  color: #d8e8fb;
  padding: 0 6px 0 12px;
  font-size: 12px;
}

.allowchip.unavailable {
  border-style: dashed;
  opacity: 0.7;
}

.allowchip button {
  border: 0;
  background: transparent;
  color: #8fb1d3;
  display: grid;
  place-items: center;
  cursor: pointer;
  padding: 2px;
}

.addchip {
  height: 32px;
  border: 1px dashed var(--fg2-line-strong);
  border-radius: 999px;
  background: transparent;
  color: #8fb1d3;
  padding: 0 12px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}

.policy-note {
  margin: 0;
  padding: 14px 18px;
}

.candidates {
  display: grid;
  gap: 6px;
  max-height: 260px;
  overflow-y: auto;
  margin-top: 6px;
}

.candidate {
  display: flex;
  align-items: center;
  gap: 10px;
  border: 1px solid var(--fg2-line);
  border-radius: 9px;
  background: #0b1929;
  color: var(--fg2-text);
  padding: 8px 10px;
  text-align: left;
  font: inherit;
  cursor: pointer;
}

.candidate small {
  display: block;
  color: #7d92a8;
  font-size: 11px;
}

.candidate.on,
.candidate:hover:not(:disabled) {
  border-color: #2f76c7;
  background: #12304f;
}

.member-head {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 18px 22px 0;
}
</style>
