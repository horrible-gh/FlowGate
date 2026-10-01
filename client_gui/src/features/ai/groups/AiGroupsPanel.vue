<template>
  <div class="ai-groups-panel">
    <div class="detail-head">
      <div>
        <div class="page-title">{{ t('gui.ai.groups.title') }}</div>
      </div>
      <!-- A group of system AI is an administrator's; everyone reads and starts with it (CH0006 #48). -->
      <div v-if="canEdit" class="detail-actions">
        <Button variant="primary" @click="openCreate">＋ {{ t('gui.ai.groups.add_group') }}</Button>
      </div>
    </div>

    <div v-if="error" class="panel-error-alert" role="alert">{{ error }}</div>

    <div class="groups-layout">
      <!-- Team index -->
      <aside class="group-index">
        <label class="detail-search">
          ⌕ <input v-model="searchQuery" :placeholder="t('gui.ai.groups.search_placeholder')" />
        </label>
        <div v-if="filteredGroups.length === 0" class="empty-index">{{ t('gui.ai.groups.empty_list') }}</div>
        <button
          v-for="g in filteredGroups"
          :key="g.ai_group_id"
          type="button"
          class="group-index-item"
          :class="{ active: selectedId === g.ai_group_id, off: !g.enabled }"
          @click="selectedId = g.ai_group_id"
        >
          <span class="index-item-title">
            <span v-if="g.is_default" class="star" :title="t('gui.ai.groups.default_team')">★</span>
            <b>{{ g.name }}</b>
            <span v-if="!g.enabled" class="off-tag">OFF</span>
            <span v-if="g.status === 'attention'" class="warn-dot" :title="t('gui.ai.groups.attention_hint')">!</span>
          </span>
          <small>{{ indexLine(g) }}</small>
        </button>
      </aside>

      <!-- Team detail -->
      <section v-if="selected" class="group-detail">
        <div class="group-detail-head">
          <div class="head-info">
            <h2>{{ selected.name }}</h2>
            <p v-if="selected.description">{{ selected.description }}</p>
          </div>
          <div class="head-actions">
            <span v-if="selected.is_default" class="team-badge">★ {{ t('gui.ai.groups.default_team') }}</span>
            <IconButton
              v-else-if="canEdit"
              :label="t('gui.ai.groups.make_default')"
              :title="t('gui.ai.groups.make_default')"
              size="sm"
              :disabled="busy || !selected.enabled"
              @click="setState({ is_default: true })"
            >
              <AiIcon name="star" />
            </IconButton>
            <Toggle
              v-if="canEdit"
              :model-value="selected.enabled"
              :disabled="busy"
              :aria-label="t('gui.ai.groups.enabled_aria', { name: selected.name })"
              @update:model-value="(value) => setState({ enabled: value })"
            />
            <IconButton v-if="canEdit" :label="t('gui.common.edit')" :title="t('gui.common.edit')" size="sm" :disabled="busy" @click="openEdit">
              <AiIcon name="edit" />
            </IconButton>
            <IconButton
              v-if="canEdit"
              class="danger-icon"
              :label="t('gui.common.delete')"
              :title="t('gui.common.delete')"
              size="sm"
              :disabled="busy"
              @click="deleting = selected"
            >
              <AiIcon name="trash" />
            </IconButton>
          </div>
        </div>

        <!-- Members: roles and Supervisor are picked right on the member -->
        <div class="block">
          <div class="block-head">
            <span class="block-title">{{ t('gui.ai.groups.members') }}</span>
            <span class="block-count">{{ selected.member_count }}</span>
            <Dropdown v-if="canEdit">
              <template #trigger="{ toggle, triggerAttrs }">
                <Button
                  v-bind="triggerAttrs"
                  class="block-action"
                  variant="secondary"
                  size="sm"
                  :disabled="busy || !addableProviders.length"
                  :title="addableProviders.length ? undefined : t('gui.ai.groups.no_addable')"
                  @click="toggle"
                >
                  ＋ {{ t('gui.ai.groups.add_member') }}
                </Button>
              </template>
              <template #default="{ close }">
                <button
                  v-for="p in addableProviders"
                  :key="p.id"
                  type="button"
                  role="menuitem"
                  class="menu-item"
                  @click="close(); addMember(p.id)"
                >
                  <b>{{ p.name }}</b><small>{{ p.kind }}</small>
                </button>
              </template>
            </Dropdown>
          </div>

          <div v-if="!selected.members.length" class="block-empty">{{ t('gui.ai.groups.no_members') }}</div>
          <div
            v-for="member in orderedMembers"
            :key="member.member_id"
            class="member-row"
            :class="{ 'is-supervisor': isSupervisor(member) }"
          >
            <span class="avatar-round" :class="getAvatarClass(memberName(member))">{{ getInitials(memberName(member)) }}</span>
            <div class="member-info">
              <div class="name-line">
                <b>{{ memberName(member) }}</b>
                <span v-if="isSupervisor(member)" class="supervisor-tag">{{ t('gui.ai.groups.supervisor') }}</span>
                <Badge v-if="member.status !== 'available'" :tone="member.status === 'broken' ? 'danger' : 'warning'" size="sm">
                  {{ t(`gui.ai.groups.status_${member.status}`) }}
                </Badge>
              </div>
              <small class="muted">{{ evaluationLabel(member.latest_evaluation) }}</small>
            </div>

            <div class="role-tags">
              <button
                v-for="role in rolesOf(member)"
                :key="role.role_id"
                type="button"
                class="role indigo role-chip"
                :title="canEdit ? t('gui.ai.groups.unassign_role', { role: role.name }) : undefined"
                :disabled="busy || !canEdit"
                @click="toggleMapping(member, role.role_id, false)"
              >
                {{ role.name }}<span v-if="canEdit" aria-hidden="true">×</span>
              </button>
              <Dropdown v-if="canEdit">
                <template #trigger="{ toggle, open }">
                  <button
                    type="button"
                    aria-haspopup="menu"
                    :aria-expanded="open"
                    class="role role-add"
                    :aria-label="t('gui.ai.groups.assign_role_aria', { name: memberName(member) })"
                    :disabled="busy"
                    @click="toggle"
                  >
                    ＋ {{ t('gui.ai.groups.role_short') }}
                  </button>
                </template>
                <template #default="{ close }">
                  <label v-for="role in selected.roles" :key="role.role_id" class="menu-check" role="menuitemcheckbox" :aria-checked="member.role_ids.includes(role.role_id)">
                    <input
                      type="checkbox"
                      :checked="member.role_ids.includes(role.role_id)"
                      :disabled="busy"
                      @change="toggleMapping(member, role.role_id, ($event.target as HTMLInputElement).checked)"
                    />
                    {{ role.name }}
                  </label>
                  <button type="button" role="menuitem" class="menu-item menu-item--new" @click="close(); openRoleCreate()">
                    ＋ {{ t('gui.ai.groups.add_role') }}
                  </button>
                </template>
              </Dropdown>
            </div>

            <!-- Always rendered so the columns line up whether or not a fit exists. -->
            <strong class="fit" :title="typeof member.fit_score === 'number' ? t('gui.ai.groups.fit_hint') : undefined">
              {{ typeof member.fit_score === 'number' ? `${formatScore(member.fit_score)} fit` : '' }}
            </strong>
            <IconButton
              v-if="canEdit"
              class="crown"
              :class="{ on: isSupervisor(member) }"
              :label="isSupervisor(member) ? t('gui.ai.groups.unset_supervisor') : t('gui.ai.groups.set_supervisor')"
              :title="isSupervisor(member) ? t('gui.ai.groups.unset_supervisor') : t('gui.ai.groups.set_supervisor')"
              :aria-pressed="isSupervisor(member)"
              size="sm"
              :disabled="busy"
              @click="setSupervisor(isSupervisor(member) ? '' : member.member_id)"
            >
              <AiIcon name="crown" />
            </IconButton>
            <IconButton
              v-if="canEdit"
              class="danger-icon"
              :label="t('gui.ai.groups.remove_member')"
              :title="t('gui.ai.groups.remove_member')"
              size="sm"
              :disabled="busy"
              @click="removeMember(member.member_id)"
            >
              <AiIcon name="close" />
            </IconButton>
          </div>
        </div>

        <!-- Roles of this team -->
        <div class="block">
          <div class="block-head">
            <span class="block-title">{{ t('gui.ai.groups.roles') }}</span>
            <span class="block-count">{{ selected.role_count }}</span>
            <Button v-if="canEdit" class="block-action" variant="secondary" size="sm" :disabled="busy" @click="openRoleCreate">
              ＋ {{ t('gui.ai.groups.add_role') }}
            </Button>
          </div>
          <div v-if="!selected.roles.length" class="block-empty">{{ t('gui.ai.groups.no_roles') }}</div>
          <div v-else class="role-grid">
            <div v-for="role in selected.roles" :key="role.role_id" class="role-card">
              <div class="role-card-main">
                <b>{{ role.name }}</b>
                <small>{{ roleMemberNames(role) }}</small>
                <small class="criteria-line">{{ criteriaLabel(role) }}</small>
              </div>
              <div v-if="canEdit" class="role-card-actions">
                <IconButton :label="t('gui.common.edit')" :title="t('gui.common.edit')" size="sm" :disabled="busy" @click="openRoleEdit(role)">
                  <AiIcon name="edit" />
                </IconButton>
                <IconButton
                  class="danger-icon"
                  :label="t('gui.common.delete')"
                  :title="t('gui.common.delete')"
                  size="sm"
                  :disabled="busy"
                  @click="deletingRole = role"
                >
                  <AiIcon name="trash" />
                </IconButton>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section v-else class="group-detail empty-detail">
        <p>{{ groups.length ? t('gui.ai.groups.select_group_prompt') : t('gui.ai.groups.create_first') }}</p>
      </section>
    </div>

    <AiGroupEditDialog
      :open="groupDialog.open"
      kind="group"
      :initial="groupDialog.initial"
      :saving="busy"
      :error="dialogError"
      @close="groupDialog.open = false"
      @save="onSaveGroup"
    />
    <AiGroupEditDialog
      :open="roleDialog.open"
      kind="role"
      :initial="roleDialog.initial"
      :saving="busy"
      :error="dialogError"
      @close="roleDialog.open = false"
      @save="onSaveRole"
    />

    <ConfirmDialog
      :open="!!deleting"
      :title="t('gui.ai.groups.delete_confirm_title')"
      :message="t('gui.ai.groups.delete_confirm_msg', { name: deleting?.name })"
      danger
      @confirm="onDeleteGroup"
      @cancel="deleting = null"
    />
    <ConfirmDialog
      :open="!!deletingRole"
      :title="t('gui.ai.groups.role_delete_title')"
      :message="t('gui.ai.groups.role_delete_msg', { name: deletingRole?.name })"
      danger
      @confirm="onDeleteRole"
      @cancel="deletingRole = null"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Badge from '@/components/ui/Badge.vue'
import Button from '@/components/ui/Button.vue'
import ConfirmDialog from '@/components/ui/ConfirmDialog.vue'
import Dropdown from '@/components/ui/Dropdown.vue'
import IconButton from '@/components/ui/IconButton.vue'
import Toggle from '@/components/ui/Toggle.vue'
import {
  addAiGroupMember,
  createAiGroup,
  createAiGroupRole,
  deleteAiGroup,
  deleteAiGroupRole,
  listAiGroups,
  removeAiGroupMember,
  setAiGroupMemberRoles,
  setAiGroupState,
  updateAiGroup,
  updateAiGroupRole,
} from '../api'
import {
  formatScore,
  type AiGroup,
  type AiGroupInput,
  type AiGroupMember,
  type AiGroupRole,
  type AiGroupRoleInput,
  type AiGroupStateInput,
  type AiLatestEvaluation,
  type ProviderItem,
} from '../types'
import { useCurrentUser } from '@/core/auth/currentUser'
import AiIcon from '../AiIcon.vue'
import AiGroupEditDialog from './AiGroupEditDialog.vue'

// A team: members (AI connections), user-defined roles mapped onto members, an optional
// Supervisor, a default flag and an ON/OFF switch. Every change is saved as it is made.
const props = defineProps<{
  providers: ProviderItem[]
  initialGroups: AiGroup[]
}>()

const emit = defineEmits<{ changed: [] }>()
const { t } = useI18n()
const { user } = useCurrentUser()
// Every group holds system AI only (a user's own AI cannot join yet), so only an administrator edits.
const canEdit = computed(() => !!user.value?.isAdmin)

const groups = ref<AiGroup[]>([...props.initialGroups])
const selectedId = ref<string | null>(groups.value[0]?.ai_group_id ?? null)
const searchQuery = ref('')
const error = ref('')
const dialogError = ref<string | null>(null)
const busy = ref(false)
const deleting = ref<AiGroup | null>(null)
const deletingRole = ref<AiGroupRole | null>(null)
const groupDialog = reactive<{ open: boolean; initial: { name: string; description: string | null } | null }>({ open: false, initial: null })
const roleDialog = reactive<{ open: boolean; roleId: string | null; initial: AiGroupRoleInput | null }>({
  open: false,
  roleId: null,
  initial: null,
})

watch(
  () => props.initialGroups,
  (value) => {
    groups.value = [...value]
    if (!groups.value.some((g) => g.ai_group_id === selectedId.value)) selectedId.value = groups.value[0]?.ai_group_id ?? null
  },
  { deep: true }
)

const selected = computed(() => groups.value.find((g) => g.ai_group_id === selectedId.value) ?? null)

const filteredGroups = computed(() => {
  const q = searchQuery.value.trim().toLowerCase()
  if (!q) return groups.value
  return groups.value.filter((g) => g.name.toLowerCase().includes(q) || (g.description || '').toLowerCase().includes(q))
})

// Supervisor first, then the order the members were added in.
const orderedMembers = computed(() => {
  const members = selected.value?.members ?? []
  return [...members.filter(isSupervisor), ...members.filter((m) => !isSupervisor(m))]
})

const addableProviders = computed(() => {
  const taken = new Set((selected.value?.members ?? []).map((m) => m.provider_id))
  return props.providers.filter((p) => !taken.has(p.id))
})

function isSupervisor(member: AiGroupMember): boolean {
  return selected.value?.supervisor?.member_id === member.member_id
}

function memberName(member: { provider_name: string | null; provider_id: string }): string {
  return member.provider_name || member.provider_id
}

function indexLine(group: AiGroup): string {
  const counts = t('gui.ai.groups.counts', { members: group.member_count, roles: group.role_count })
  return group.supervisor ? `${memberName(group.supervisor)} · ${counts}` : counts
}

function rolesOf(member: AiGroupMember): AiGroupRole[] {
  return (selected.value?.roles ?? []).filter((role) => member.role_ids.includes(role.role_id))
}

function roleMemberNames(role: AiGroupRole): string {
  const names = (selected.value?.members ?? []).filter((m) => role.member_ids.includes(m.member_id)).map(memberName)
  return names.length ? names.join(', ') : t('gui.ai.groups.role_unassigned')
}

function criteriaLabel(role: AiGroupRole): string {
  const keys = role.criteria?.length ? role.criteria : null
  return keys
    ? keys.map((key) => t(`gui.ai.evaluation.criteria_short.${key}`)).join(' · ')
    : t('gui.ai.groups.criteria_all')
}

// Latest completed evaluation of the member's AI connection, on the 10-point scale.
function evaluationLabel(latest?: AiLatestEvaluation | null): string {
  return typeof latest?.total_score === 'number'
    ? t('gui.ai.groups.evaluation_score', { score: formatScore(latest.total_score) })
    : t('gui.ai.groups.not_evaluated')
}

function replaceGroup(updated: AiGroup): void {
  const index = groups.value.findIndex((g) => g.ai_group_id === updated.ai_group_id)
  if (index >= 0) groups.value.splice(index, 1, updated)
  else groups.value = [updated, ...groups.value]
  selectedId.value = updated.ai_group_id
}

async function run(action: () => Promise<AiGroup | void>, onError: 'panel' | 'dialog' = 'panel'): Promise<boolean> {
  if (busy.value) return false
  busy.value = true
  error.value = ''
  dialogError.value = null
  try {
    const result = await action()
    if (result) replaceGroup(result)
    emit('changed')
    return true
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : t('gui.ai.groups.save_failed')
    if (onError === 'dialog') dialogError.value = message
    else error.value = message
    return false
  } finally {
    busy.value = false
  }
}

function openCreate(): void {
  dialogError.value = null
  groupDialog.initial = null
  groupDialog.open = true
}

function openEdit(): void {
  if (!selected.value) return
  dialogError.value = null
  groupDialog.initial = { name: selected.value.name, description: selected.value.description }
  groupDialog.open = true
}

async function onSaveGroup(input: AiGroupInput): Promise<void> {
  const current = selected.value
  const ok = await run(
    () => (groupDialog.initial && current
      ? updateAiGroup(current.ai_group_id, {
        ...input,
        supervisor_member_id: current.supervisor?.member_id ?? null,
      })
      : createAiGroup(input)),
    'dialog',
  )
  if (ok) groupDialog.open = false
}

async function onDeleteGroup(): Promise<void> {
  const target = deleting.value
  deleting.value = null
  if (!target) return
  await run(async () => {
    await deleteAiGroup(target.ai_group_id)
    groups.value = groups.value.filter((g) => g.ai_group_id !== target.ai_group_id)
    selectedId.value = groups.value[0]?.ai_group_id ?? null
  })
}

async function setState(input: AiGroupStateInput): Promise<void> {
  const current = selected.value
  if (!current) return
  await run(() => setAiGroupState(current.ai_group_id, input))
  // Only one default team per project: refresh the others' flags.
  if (input.is_default || input.enabled === false) await reload()
}

async function setSupervisor(memberId: string): Promise<void> {
  const current = selected.value
  if (!current) return
  await run(() => updateAiGroup(current.ai_group_id, {
    name: current.name,
    description: current.description,
    supervisor_member_id: memberId || null,
  }))
}

async function addMember(providerId: string): Promise<void> {
  const current = selected.value
  if (!current || !providerId) return
  await run(() => addAiGroupMember(current.ai_group_id, providerId))
}

async function removeMember(memberId: string): Promise<void> {
  const current = selected.value
  if (!current) return
  await run(() => removeAiGroupMember(current.ai_group_id, memberId))
}

async function toggleMapping(member: AiGroupMember, roleId: string, checked: boolean): Promise<void> {
  const current = selected.value
  if (!current) return
  const next = checked ? [...new Set([...member.role_ids, roleId])] : member.role_ids.filter((id) => id !== roleId)
  await run(() => setAiGroupMemberRoles(current.ai_group_id, member.member_id, next))
}

function openRoleCreate(): void {
  dialogError.value = null
  roleDialog.roleId = null
  roleDialog.initial = null
  roleDialog.open = true
}

function openRoleEdit(role: AiGroupRole): void {
  dialogError.value = null
  roleDialog.roleId = role.role_id
  roleDialog.initial = { name: role.name, description: role.description, criteria: [...(role.criteria ?? [])] }
  roleDialog.open = true
}

async function onSaveRole(input: AiGroupRoleInput): Promise<void> {
  const current = selected.value
  if (!current) return
  const roleId = roleDialog.roleId
  const ok = await run(
    () => (roleId
      ? updateAiGroupRole(current.ai_group_id, roleId, input)
      : createAiGroupRole(current.ai_group_id, input)),
    'dialog',
  )
  if (ok) roleDialog.open = false
}

async function onDeleteRole(): Promise<void> {
  const role = deletingRole.value
  const current = selected.value
  deletingRole.value = null
  if (!role || !current) return
  await run(() => deleteAiGroupRole(current.ai_group_id, role.role_id))
}

async function reload(): Promise<void> {
  try {
    groups.value = await listAiGroups()
    if (!groups.value.some((g) => g.ai_group_id === selectedId.value)) selectedId.value = groups.value[0]?.ai_group_id ?? null
  } catch (err: unknown) {
    error.value = err instanceof Error ? err.message : t('gui.ai.groups.load_failed')
  }
}

function getInitials(name: string): string {
  if (!name) return 'AI'
  const parts = name.trim().split(/\s+/)
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase()
  return name.slice(0, 2).toUpperCase()
}

function getAvatarClass(name: string): string {
  const n = name.toLowerCase()
  if (n.includes('sonnet') || n.includes('claude') || n.includes('opus') || n.includes('haiku')) return 'av-cs'
  if (n.includes('gpt') || n.includes('openai') || n.includes('codex')) return 'av-og'
  if (n.includes('gemini')) return 'av-gp'
  if (n.includes('llama')) return 'av-l3'
  return 'av-cs'
}
</script>

<style scoped>
.ai-groups-panel {
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.detail-head {
  display: flex;
  align-items: flex-start;
  gap: 20px;
  margin-bottom: 16px;
}

.page-title {
  font-size: 21px;
  font-weight: 850;
  margin: 0 0 6px;
  color: var(--fg2-text, #edf3fa);
}

.page-sub {
  color: #b9c8d8;
  font-size: 14.5px;
}

.detail-actions {
  margin-left: auto;
}

.groups-layout {
  display: grid;
  grid-template-columns: 260px minmax(0, 1fr);
  gap: 14px;
  min-height: 0;
}

.group-index,
.group-detail {
  border: 1px solid var(--fg2-line);
  border-radius: 12px;
  background: linear-gradient(180deg, rgba(13, 26, 40, 0.94), rgba(10, 22, 35, 0.92));
}

.group-index {
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  align-self: start;
}

.detail-search {
  height: 34px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 11px;
  margin-bottom: 6px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: 8px;
  background: #0c1b2c;
  color: #8399b0;
  font-size: 13px;
}

.detail-search input {
  min-width: 0;
  flex: 1;
  border: 0;
  outline: 0;
  background: transparent;
  color: #dce7f3;
  font-size: 12.5px;
}

.empty-index {
  padding: 18px 6px;
  color: var(--fg2-muted);
  font-size: 12.5px;
  text-align: center;
}

.group-index-item {
  display: grid;
  gap: 3px;
  padding: 9px 10px;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  color: #dce7f3;
  text-align: left;
  cursor: pointer;
}

.group-index-item:hover {
  background: #102238;
}

.group-index-item.active {
  border-color: rgba(75, 163, 255, 0.45);
  background: #13304f;
}

.group-index-item.off {
  opacity: 0.6;
}

.index-item-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
}

.group-index-item small {
  font-size: 11px;
  color: var(--fg2-muted);
}

.star {
  color: #f5c451;
}

.off-tag {
  padding: 1px 7px;
  border-radius: 999px;
  font-size: 10.5px;
  font-weight: 700;
  background: rgba(152, 169, 187, 0.18);
  color: #98a9bb;
}

.warn-dot {
  width: 16px;
  height: 16px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  background: rgba(245, 158, 11, 0.2);
  color: #f2c46d;
  font-size: 10.5px;
  font-weight: 800;
}

.group-detail {
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  gap: 22px;
}

.empty-detail {
  align-items: center;
  justify-content: center;
  color: var(--fg2-muted);
  min-height: 200px;
}

.group-detail-head {
  display: flex;
  align-items: center;
  gap: 16px;
}

.head-info {
  flex: 1;
  min-width: 0;
}

.head-info h2 {
  margin: 0;
  font-size: 18px;
  color: #eaf1f8;
}

.head-info p {
  margin: 6px 0 0;
  color: #b9c8d8;
  font-size: 13px;
}

.head-actions {
  display: flex;
  align-items: center;
  gap: 6px;
}

.team-badge {
  padding: 3px 10px;
  border-radius: 999px;
  background: rgba(40, 110, 201, 0.2);
  color: #9cc6ff;
  font-size: 11.5px;
  font-weight: 700;
}

.block {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.block-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--fg2-line);
}

.block-title {
  font-size: 13.5px;
  font-weight: 800;
  color: #eaf1f8;
}

.block-count {
  min-width: 20px;
  height: 20px;
  padding: 0 6px;
  border-radius: 999px;
  display: inline-grid;
  place-items: center;
  background: #11263c;
  color: #9fb3c8;
  font-size: 11px;
  font-weight: 700;
}

.block-action {
  margin-left: auto;
}

.block-empty {
  padding: 10px 2px;
  font-size: 12.5px;
  color: var(--fg2-muted);
}

.member-row {
  display: grid;
  grid-template-columns: 32px minmax(140px, 220px) minmax(0, 1fr) auto auto auto;
  align-items: center;
  gap: 12px;
  padding: 8px 10px;
  border: 1px solid rgba(154, 181, 210, 0.1);
  border-radius: 8px;
  background: #0d1b2b;
}

.member-row.is-supervisor {
  border-color: rgba(245, 196, 81, 0.35);
  background: #13202c;
}

.member-info {
  min-width: 0;
  display: grid;
  gap: 3px;
}

.name-line {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  min-width: 0;
}

.name-line b {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.supervisor-tag {
  padding: 1px 7px;
  border-radius: 999px;
  background: rgba(245, 196, 81, 0.16);
  color: #f5c451;
  font-size: 10.5px;
  font-weight: 700;
  flex: none;
}

.muted {
  font-size: 11.5px;
  color: var(--fg2-muted);
}

.avatar-round {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  font-weight: 800;
  font-size: 11px;
  color: #fff;
  flex: none;
}

.av-cs { background: linear-gradient(145deg, #d98466, #8d3f31); }
.av-og { background: linear-gradient(145deg, #67a2ff, #3667d7); }
.av-gp { background: linear-gradient(145deg, #9b79ff, #6f49d7); }
.av-l3 { background: linear-gradient(145deg, #58adff, #2e77c6); }

.role-tags {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 5px;
  min-width: 0;
}

.role {
  height: 24px;
  padding: 0 8px;
  border-radius: 6px;
  border: 1px solid rgba(112, 151, 193, 0.2);
  background: #11263c;
  color: #dce9f8;
  font-size: 11.5px;
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.role.indigo {
  background: #132744;
  border-color: rgba(91, 146, 255, 0.22);
  color: #b4d1ff;
}

.role-chip {
  cursor: pointer;
}

.role-chip span {
  opacity: 0;
  font-size: 12px;
  transition: opacity 0.12s ease;
}

.role-chip:hover span,
.role-chip:focus-visible span {
  opacity: 0.8;
}

.role-add {
  border-style: dashed;
  background: transparent;
  color: #8fa6bf;
  cursor: pointer;
}

.role-add:hover:not(:disabled) {
  color: #dce9f8;
  border-color: rgba(112, 151, 193, 0.45);
}

.fit {
  font-size: 13px;
  color: #7ee0c3;
  white-space: nowrap;
}

.crown {
  color: #5f7690;
}

.crown.on {
  color: #f5c451;
}

.danger-icon:hover:not(:disabled) {
  color: #fca5a5;
}

.menu-item,
.menu-check {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 10px;
  border: 0;
  border-radius: 6px;
  background: none;
  color: #dce7f3;
  font-size: 12.5px;
  text-align: left;
  cursor: pointer;
}

.menu-item small {
  margin-left: auto;
  color: var(--fg2-muted);
  font-size: 11px;
}

.menu-item:hover,
.menu-check:hover {
  background: rgba(255, 255, 255, 0.06);
}

.menu-item--new {
  margin-top: 4px;
  border-top: 1px solid var(--fg2-line);
  border-radius: 0 0 6px 6px;
  color: #9cc6ff;
}

.role-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 8px;
}

.role-card {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 10px 10px 10px 12px;
  border: 1px solid rgba(154, 181, 210, 0.1);
  border-radius: 8px;
  background: #0d1b2b;
}

.role-card-main {
  flex: 1;
  min-width: 0;
  display: grid;
  gap: 3px;
}

.role-card-main b {
  font-size: 13px;
  color: #b4d1ff;
}

.role-card-main small {
  font-size: 11.5px;
  color: #b9c8d8;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.role-card-main .criteria-line {
  color: var(--fg2-muted);
}

.role-card-actions {
  display: flex;
  opacity: 0.55;
  transition: opacity 0.12s ease;
}

.role-card:hover .role-card-actions,
.role-card:focus-within .role-card-actions {
  opacity: 1;
}

.panel-error-alert {
  padding: 10px 14px;
  border-radius: 8px;
  background: rgba(239, 68, 68, 0.15);
  border: 1px solid rgba(239, 68, 68, 0.3);
  color: #fca5a5;
  font-size: 12px;
  margin-bottom: 12px;
}

@media (max-width: 1100px) {
  .member-row {
    grid-template-columns: 32px minmax(0, 1fr) auto auto auto;
  }

  .member-row .role-tags {
    grid-column: 2 / -1;
    grid-row: 2;
  }
}

@media (max-width: 900px) {
  .groups-layout { grid-template-columns: 1fr; }
}
</style>
