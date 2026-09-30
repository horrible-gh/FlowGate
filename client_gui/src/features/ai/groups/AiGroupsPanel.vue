<template>
  <div class="ai-groups-panel">
    <div class="detail-head">
      <div>
        <div class="page-title">{{ t('gui.ai.groups.title') }}</div>
        <div class="page-sub">{{ t('gui.ai.groups.subtitle') }}</div>
      </div>
      <div class="detail-actions">
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
            <span v-if="g.is_default" class="star">★</span>
            <b>{{ g.name }}</b>
            <span v-if="!g.enabled" class="off-tag">OFF</span>
            <span v-if="g.status === 'attention'" class="warn-dot" :title="t('gui.ai.groups.attention_hint')">!</span>
          </span>
          <small>{{ t('gui.ai.groups.index_supervisor', { name: g.supervisor ? memberName(g.supervisor) : t('gui.ai.groups.no_supervisor') }) }}</small>
          <small>{{ t('gui.ai.groups.counts', { members: g.member_count, roles: g.role_count }) }}</small>
        </button>
      </aside>

      <!-- Team detail -->
      <section v-if="selected" class="group-detail">
        <div class="group-detail-head">
          <div class="head-info">
            <h2>{{ selected.name }}</h2>
            <p>{{ selected.description || t('gui.ai.groups.no_description') }}</p>
          </div>
          <div class="head-actions">
            <span v-if="selected.is_default" class="team-badge">{{ t('gui.ai.groups.default_team') }}</span>
            <Button v-else variant="ghost" size="sm" :disabled="busy || !selected.enabled" @click="setState({ is_default: true })">
              {{ t('gui.ai.groups.make_default') }}
            </Button>
            <Toggle
              :model-value="selected.enabled"
              :disabled="busy"
              :aria-label="t('gui.ai.groups.enabled_aria', { name: selected.name })"
              @update:model-value="(value) => setState({ enabled: value })"
            />
            <Button variant="secondary" size="sm" :disabled="busy" @click="openEdit">{{ t('gui.common.edit') }}</Button>
            <Button variant="danger" size="sm" :disabled="busy" @click="deleting = selected">{{ t('gui.common.delete') }}</Button>
          </div>
        </div>

        <!-- Supervisor (optional) -->
        <div class="section">
          <div class="section-title">
            <span>{{ t('gui.ai.groups.supervisor') }}</span>
          </div>
          <div v-if="selected.supervisor" class="supervisor-detail">
            <span class="avatar-round" :class="getAvatarClass(memberName(selected.supervisor))">
              {{ getInitials(memberName(selected.supervisor)) }}
            </span>
            <div class="member-info">
              <div class="name-line">
                <b>{{ memberName(selected.supervisor) }}</b>
                <Badge v-if="selected.supervisor.status !== 'available'" :tone="selected.supervisor.status === 'broken' ? 'danger' : 'warning'" size="sm">
                  {{ t(`gui.ai.groups.status_${selected.supervisor.status}`) }}
                </Badge>
              </div>
              <small class="muted">{{ t('gui.ai.groups.supervisor_duty') }}</small>
            </div>
            <span class="eval-chip" :class="{ none: !hasScore(selected.supervisor.latest_evaluation) }">
              {{ evaluationLabel(selected.supervisor.latest_evaluation) }}
            </span>
          </div>
          <div class="inline-form">
            <Select
              :model-value="selected.supervisor?.member_id ?? ''"
              :options="supervisorOptions"
              :disabled="busy || !selected.members.length"
              @update:model-value="setSupervisor"
            />
          </div>
        </div>

        <!-- Members -->
        <div class="section">
          <div class="section-title">
            <span>{{ t('gui.ai.groups.members') }} ({{ selected.member_count }})</span>
          </div>
          <div v-if="!selected.members.length" class="section-empty">{{ t('gui.ai.groups.no_members') }}</div>
          <div v-for="member in selected.members" :key="member.member_id" class="member-row">
            <span class="avatar-round" :class="getAvatarClass(memberName(member))">{{ getInitials(memberName(member)) }}</span>
            <div class="member-info">
              <div class="name-line">
                <b>{{ memberName(member) }}</b>
                <Badge v-if="member.status !== 'available'" :tone="member.status === 'broken' ? 'danger' : 'warning'" size="sm">
                  {{ t(`gui.ai.groups.status_${member.status}`) }}
                </Badge>
                <Badge v-if="selected.supervisor?.member_id === member.member_id" tone="info" size="sm">
                  {{ t('gui.ai.groups.supervisor') }}
                </Badge>
                <span class="eval-chip" :class="{ none: !hasScore(member.latest_evaluation) }">
                  {{ evaluationLabel(member.latest_evaluation) }}
                </span>
              </div>
              <div class="role-tags">
                <span v-for="role in rolesOf(member)" :key="role.role_id" class="role indigo">{{ role.name }}</span>
                <span v-if="!member.role_ids.length" class="muted">{{ t('gui.ai.groups.no_member_roles') }}</span>
              </div>
            </div>
            <strong v-if="typeof member.fit_score === 'number'" class="fit" :title="t('gui.ai.groups.fit_hint')">
              {{ formatScore(member.fit_score) }} fit
            </strong>
            <button type="button" class="action-btn action-btn--danger" :disabled="busy" @click="removeMember(member.member_id)">
              {{ t('gui.ai.groups.remove_member') }}
            </button>
          </div>
          <div class="inline-form">
            <Select v-model="newMemberProviderId" :options="addableOptions" :disabled="busy || !addableOptions.length" :placeholder="t('gui.ai.groups.choose_connection')" />
            <Button variant="secondary" size="sm" :disabled="busy || !newMemberProviderId" @click="addMember">
              {{ t('gui.ai.groups.add_member') }}
            </Button>
          </div>
        </div>

        <!-- Custom roles -->
        <div class="section">
          <div class="section-title">
            <span>{{ t('gui.ai.groups.roles') }} ({{ selected.role_count }})</span>
          </div>
          <div v-if="!selected.roles.length" class="section-empty">{{ t('gui.ai.groups.no_roles') }}</div>
          <div v-for="role in selected.roles" :key="role.role_id" class="role-row">
            <div class="role-info">
              <b>{{ role.name }}</b>
              <small v-if="role.description">{{ role.description }}</small>
              <small class="criteria-line">{{ criteriaLabel(role) }}</small>
            </div>
            <span class="muted">{{ t('gui.ai.groups.role_member_count', { count: role.member_ids.length }) }}</span>
            <button type="button" class="action-btn" :disabled="busy" @click="openRoleEdit(role)">{{ t('gui.common.edit') }}</button>
            <button type="button" class="action-btn action-btn--danger" :disabled="busy" @click="deletingRole = role">{{ t('gui.common.delete') }}</button>
          </div>
          <div class="inline-form">
            <Button variant="secondary" size="sm" :disabled="busy" @click="openRoleCreate">＋ {{ t('gui.ai.groups.add_role') }}</Button>
          </div>
        </div>

        <!-- Member x role mapping (many-to-many) -->
        <div class="section">
          <div class="section-title">
            <span>{{ t('gui.ai.groups.role_mapping') }}</span>
          </div>
          <div v-if="!selected.members.length || !selected.roles.length" class="section-empty">
            {{ t('gui.ai.groups.mapping_empty') }}
          </div>
          <div v-else class="role-summary">
            <div v-for="role in selected.roles" :key="role.role_id" class="role-summary-item">
              <span class="role indigo">{{ role.name }}</span>
              <span class="role-summary-members">{{ roleMemberNames(role) }}</span>
            </div>
          </div>
          <div v-if="selected.members.length && selected.roles.length" class="matrix-wrap">
            <table class="matrix">
              <thead>
                <tr>
                  <th></th>
                  <th v-for="role in selected.roles" :key="role.role_id">{{ role.name }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="member in selected.members" :key="member.member_id">
                  <th>{{ memberName(member) }}</th>
                  <td v-for="role in selected.roles" :key="role.role_id">
                    <input
                      type="checkbox"
                      :checked="member.role_ids.includes(role.role_id)"
                      :disabled="busy"
                      :aria-label="`${memberName(member)} · ${role.name}`"
                      @change="toggleMapping(member, role.role_id, ($event.target as HTMLInputElement).checked)"
                    />
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- Runtime slots: separate from the team's roles -->
        <div class="section">
          <div class="section-title">
            <span>{{ t('gui.ai.groups.runtime_title') }}</span>
          </div>
          <div class="runtime-grid">
            <label v-for="slot in RUNTIME_SLOTS" :key="slot">
              <span>{{ t(`gui.ai.groups.slot_${slot}`) }}</span>
              <Select v-model="bindingForm[slot]" :options="bindingOptions(slot)" :disabled="busy || !selected.members.length" />
            </label>
          </div>
          <div class="inline-form">
            <Button variant="secondary" size="sm" :disabled="busy || !bindingDirty" @click="saveBinding">
              {{ t('gui.ai.groups.save_runtime') }}
            </Button>
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
import Select, { type SelectOption } from '@/components/ui/Select.vue'
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
  setAiGroupRuntimeBinding,
  setAiGroupState,
  updateAiGroup,
  updateAiGroupRole,
} from '../api'
import {
  RUNTIME_SLOTS,
  formatScore,
  type AiGroup,
  type AiGroupInput,
  type AiGroupMember,
  type AiGroupRole,
  type AiGroupRoleInput,
  type AiGroupStateInput,
  type AiLatestEvaluation,
  type ProviderItem,
  type RuntimeSlot,
} from '../types'
import AiGroupEditDialog from './AiGroupEditDialog.vue'

const props = defineProps<{
  projectId: string
  providers: ProviderItem[]
  initialGroups: AiGroup[]
}>()

const emit = defineEmits<{ changed: [] }>()
const { t } = useI18n()

const groups = ref<AiGroup[]>([...props.initialGroups])
const selectedId = ref<string | null>(groups.value[0]?.ai_group_id ?? null)
const searchQuery = ref('')
const error = ref('')
const dialogError = ref<string | null>(null)
const busy = ref(false)
const newMemberProviderId = ref('')
const deleting = ref<AiGroup | null>(null)
const deletingRole = ref<AiGroupRole | null>(null)
const groupDialog = reactive<{ open: boolean; initial: { name: string; description: string | null } | null }>({ open: false, initial: null })
const roleDialog = reactive<{ open: boolean; roleId: string | null; initial: AiGroupRoleInput | null }>({
  open: false,
  roleId: null,
  initial: null,
})
const bindingForm = reactive<Record<RuntimeSlot, string>>({ work_executor: '', reviewer: '', reworker: '' })

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

function resetBindingForm(): void {
  for (const slot of RUNTIME_SLOTS) bindingForm[slot] = selected.value?.runtime_binding?.[slot]?.provider_id ?? ''
}

watch(selected, () => {
  newMemberProviderId.value = ''
  resetBindingForm()
}, { immediate: true })

const bindingDirty = computed(() =>
  RUNTIME_SLOTS.some((slot) => bindingForm[slot] !== (selected.value?.runtime_binding?.[slot]?.provider_id ?? ''))
)

const supervisorOptions = computed<SelectOption[]>(() => [
  { value: '', label: t('gui.ai.groups.no_supervisor') },
  ...(selected.value?.members ?? []).map((m) => ({ value: m.member_id, label: memberName(m) })),
])

const addableOptions = computed<SelectOption[]>(() => {
  const taken = new Set((selected.value?.members ?? []).map((m) => m.provider_id))
  return props.providers.filter((p) => !taken.has(p.id)).map((p) => ({ value: p.id, label: `${p.name} (${p.kind})` }))
})

function bindingOptions(slot: RuntimeSlot): SelectOption[] {
  const none = { value: '', label: slot === 'work_executor' ? t('gui.ai.groups.slot_unset') : t('gui.ai.common.none') }
  return [
    none,
    ...(selected.value?.members ?? []).map((m) => ({
      value: m.provider_id,
      label: memberName(m) + (m.is_available ? '' : ` — ${t(`gui.ai.groups.status_${m.status}`)}`),
      disabled: !m.is_available,
    })),
  ]
}

function memberName(member: { provider_name: string | null; provider_id: string }): string {
  return member.provider_name || member.provider_id
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

function hasScore(latest?: AiLatestEvaluation | null): boolean {
  return typeof latest?.total_score === 'number'
}

// Latest completed evaluation of the member's AI connection, on the 10-point scale.
function evaluationLabel(latest?: AiLatestEvaluation | null): string {
  return hasScore(latest)
    ? t('gui.ai.groups.evaluation_score', { score: formatScore(latest?.total_score) })
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
      ? updateAiGroup(props.projectId, current.ai_group_id, input)
      : createAiGroup(props.projectId, input)),
    'dialog',
  )
  if (ok) groupDialog.open = false
}

async function onDeleteGroup(): Promise<void> {
  const target = deleting.value
  deleting.value = null
  if (!target) return
  await run(async () => {
    await deleteAiGroup(props.projectId, target.ai_group_id)
    groups.value = groups.value.filter((g) => g.ai_group_id !== target.ai_group_id)
    selectedId.value = groups.value[0]?.ai_group_id ?? null
  })
}

async function setState(input: AiGroupStateInput): Promise<void> {
  const current = selected.value
  if (!current) return
  await run(() => setAiGroupState(props.projectId, current.ai_group_id, input))
  // Only one default team per project: refresh the others' flags.
  if (input.is_default || input.enabled === false) await reload()
}

async function setSupervisor(memberId: string): Promise<void> {
  const current = selected.value
  if (!current) return
  await run(() => updateAiGroup(props.projectId, current.ai_group_id, {
    name: current.name,
    description: current.description,
    supervisor_member_id: memberId || null,
  }))
}

async function addMember(): Promise<void> {
  const current = selected.value
  const providerId = newMemberProviderId.value
  if (!current || !providerId) return
  if (await run(() => addAiGroupMember(props.projectId, current.ai_group_id, providerId))) newMemberProviderId.value = ''
}

async function removeMember(memberId: string): Promise<void> {
  const current = selected.value
  if (!current) return
  await run(() => removeAiGroupMember(props.projectId, current.ai_group_id, memberId))
}

async function toggleMapping(member: AiGroupMember, roleId: string, checked: boolean): Promise<void> {
  const current = selected.value
  if (!current) return
  const next = checked ? [...new Set([...member.role_ids, roleId])] : member.role_ids.filter((id) => id !== roleId)
  await run(() => setAiGroupMemberRoles(props.projectId, current.ai_group_id, member.member_id, next))
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
      ? updateAiGroupRole(props.projectId, current.ai_group_id, roleId, input)
      : createAiGroupRole(props.projectId, current.ai_group_id, input)),
    'dialog',
  )
  if (ok) roleDialog.open = false
}

async function onDeleteRole(): Promise<void> {
  const role = deletingRole.value
  const current = selected.value
  deletingRole.value = null
  if (!role || !current) return
  await run(() => deleteAiGroupRole(props.projectId, current.ai_group_id, role.role_id))
}

async function saveBinding(): Promise<void> {
  const current = selected.value
  if (!current) return
  await run(() => setAiGroupRuntimeBinding(props.projectId, current.ai_group_id, {
    work_executor_provider_id: bindingForm.work_executor || null,
    reviewer_provider_id: bindingForm.reviewer || null,
    reworker_provider_id: bindingForm.reworker || null,
  }))
  resetBindingForm()
}

async function reload(): Promise<void> {
  try {
    groups.value = await listAiGroups(props.projectId)
    if (!groups.value.some((g) => g.ai_group_id === selectedId.value)) selectedId.value = groups.value[0]?.ai_group_id ?? null
  } catch (err: unknown) {
    error.value = err instanceof Error ? err.message : t('gui.ai.groups.load_failed')
  }
}

watch(() => props.projectId, () => void reload())

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

.star {
  color: #f5c451;
}

.off-tag,
.team-badge {
  padding: 1px 7px;
  border-radius: 999px;
  font-size: 10.5px;
  font-weight: 700;
}

.off-tag {
  background: rgba(152, 169, 187, 0.18);
  color: #98a9bb;
}

.team-badge {
  align-self: center;
  background: rgba(40, 110, 201, 0.2);
  color: #9cc6ff;
  font-size: 11.5px;
  padding: 3px 10px;
}

.group-index-item.off {
  opacity: 0.6;
}

.fit {
  flex: none;
  align-self: center;
  font-size: 13px;
  color: #7ee0c3;
}

.criteria-line {
  color: var(--fg2-muted);
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

.supervisor-detail {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  border: 1px solid rgba(91, 146, 255, 0.22);
  border-radius: 8px;
  background: #0f2037;
}

.eval-chip {
  height: 20px;
  padding: 0 7px;
  border-radius: 6px;
  border: 1px solid rgba(73, 223, 208, 0.22);
  background: #102b2d;
  color: #95eee5;
  font-size: 11px;
  font-weight: 700;
  display: inline-flex;
  align-items: center;
  white-space: nowrap;
}

.eval-chip.none {
  border-color: rgba(112, 151, 193, 0.2);
  background: #11263c;
  color: var(--fg2-muted);
  font-weight: 600;
}

.role-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
}

.role-summary-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #c8d7e7;
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
  gap: 18px;
}

.empty-detail {
  align-items: center;
  justify-content: center;
  color: var(--fg2-muted);
  min-height: 200px;
}

.group-detail-head {
  display: flex;
  align-items: flex-start;
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
  gap: 8px;
}

.section {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-top: 14px;
  border-top: 1px solid var(--fg2-line);
}

.section-title {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 4px 10px;
}

.section-title span {
  font-size: 13.5px;
  font-weight: 800;
  color: #eaf1f8;
}

.section-title small {
  font-size: 11.5px;
  color: var(--fg2-muted);
}

.section-empty {
  font-size: 12.5px;
  color: var(--fg2-muted);
}

.inline-form {
  display: flex;
  align-items: center;
  gap: 8px;
  max-width: 460px;
}

.member-row,
.role-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 10px;
  border: 1px solid rgba(154, 181, 210, 0.1);
  border-radius: 8px;
  background: #0d1b2b;
}

.member-info,
.role-info {
  flex: 1;
  min-width: 0;
  display: grid;
  gap: 5px;
}

.role-info small {
  font-size: 11.5px;
  color: var(--fg2-muted);
}

.name-line {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
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
  gap: 5px;
}

.role {
  height: 22px;
  padding: 0 8px;
  border-radius: 6px;
  border: 1px solid rgba(112, 151, 193, 0.2);
  background: #11263c;
  color: #dce9f8;
  font-size: 11.5px;
  display: inline-flex;
  align-items: center;
}

.role.indigo {
  background: #132744;
  border-color: rgba(91, 146, 255, 0.22);
  color: #b4d1ff;
}

.muted {
  font-size: 11.5px;
  color: var(--fg2-muted);
}

.action-btn {
  height: 28px;
  padding: 0 9px;
  border: 1px solid var(--fg2-line);
  border-radius: 6px;
  background: #102238;
  color: #b8cadc;
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
  flex: none;
}

.action-btn:hover {
  background: #16304d;
}

.action-btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.action-btn--danger {
  color: #fca5a5;
  border-color: rgba(239, 68, 68, 0.25);
}

.matrix-wrap {
  overflow-x: auto;
}

.matrix {
  border-collapse: collapse;
  font-size: 12.5px;
  color: #dce7f3;
}

.matrix th,
.matrix td {
  padding: 7px 12px;
  border: 1px solid rgba(154, 181, 210, 0.12);
  text-align: center;
  white-space: nowrap;
}

.matrix thead th {
  background: #0b1827;
  font-size: 11.5px;
  color: #9fb3c8;
}

.matrix tbody th {
  text-align: left;
  font-weight: 650;
}

.runtime-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}

.runtime-grid label {
  display: grid;
  gap: 6px;
  font-size: 12px;
  color: var(--fg2-control-fg, #c8d7e7);
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

@media (max-width: 900px) {
  .groups-layout { grid-template-columns: 1fr; }
  .runtime-grid { grid-template-columns: 1fr; }
}
</style>
