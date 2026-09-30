<template>
  <div class="ai-groups-panel">
    <div class="detail-head">
      <div>
        <div class="page-title">{{ t('gui.ai.groups.title') }}</div>
        <div class="page-sub">{{ t('gui.ai.groups.subtitle') }}</div>
      </div>
      <div class="detail-actions">
        <Button variant="primary" @click="openCreate">
          ＋ {{ t('gui.ai.groups.add_group') }}
        </Button>
      </div>
    </div>

    <div v-if="error" class="panel-error-alert">{{ error }}</div>

    <div class="groups-layout">
      <!-- Left sidebar: Group index -->
      <aside class="group-index">
        <label class="detail-search">
          ⌕ <input v-model="searchQuery" :placeholder="t('gui.ai.groups.search_placeholder')" />
        </label>

        <div v-if="filteredGroups.length === 0" class="empty-index">
          {{ t('gui.ai.groups.empty_list') }}
        </div>

        <div
          v-for="g in filteredGroups"
          :key="g.ai_group_id"
          class="group-index-item"
          :class="{ active: selectedGroup?.ai_group_id === g.ai_group_id }"
          @click="selectedGroup = g"
        >
          <div class="index-item-title">
            <span class="star">★</span>
            <b>{{ g.name }}</b>
            <span v-if="hasBrokenRole(g)" class="warn-dot" :title="t('gui.ai.groups.has_unavailable_role')">!</span>
          </div>
          <small>
            Supervisor: {{ g.work_executor?.provider_name || g.work_executor?.provider_id || t('gui.ai.common.none') }}
          </small>
        </div>
      </aside>

      <!-- Right main: Group details -->
      <section v-if="selectedGroup" class="group-detail">
        <div class="group-detail-head">
          <div class="head-info">
            <h2>{{ selectedGroup.name }}</h2>
            <p>{{ selectedGroup.description || t('gui.ai.groups.no_description') }}</p>
          </div>
          <div class="head-actions">
            <Button variant="secondary" size="sm" @click="openEdit(selectedGroup)">
              {{ t('gui.common.edit') }}
            </Button>
            <Button variant="danger" size="sm" @click="confirmDelete(selectedGroup)">
              {{ t('gui.common.delete') }}
            </Button>
          </div>
        </div>

        <!-- Supervisor section -->
        <div class="group-section-title">
          <span>Supervisor ({{ t('gui.ai.groups.role_executor') }})</span>
        </div>
        <div class="supervisor-detail">
          <template v-if="selectedGroup.work_executor">
            <span
              class="avatar-round"
              :class="getAvatarClass(selectedGroup.work_executor.provider_name || selectedGroup.work_executor.provider_id)"
            >
              {{ getInitials(selectedGroup.work_executor.provider_name || selectedGroup.work_executor.provider_id) }}
            </span>
            <div class="supervisor-info">
              <div class="name-line">
                <b>{{ selectedGroup.work_executor.provider_name || selectedGroup.work_executor.provider_id }}</b>
                <Badge
                  v-if="!selectedGroup.work_executor.is_available"
                  tone="warning"
                  size="sm"
                >
                  {{ t('gui.ai.groups.unavailable_role') }}
                </Badge>
              </div>
              <small>{{ t('gui.ai.groups.supervisor_desc') }}</small>
            </div>
            <div class="axis-chips">
              <span>{{ t('gui.ai.groups.coordination') }}</span>
              <span>{{ t('gui.ai.groups.context_dispatch') }}</span>
            </div>
          </template>
          <div v-else class="text-muted">
            {{ t('gui.ai.common.none') }}
          </div>
        </div>

        <!-- Members section -->
        <div class="group-section-title">
          <span>Members (Reviewer / Reworker)</span>
        </div>

        <!-- Reviewer row -->
        <div class="member-detail-row">
          <div class="member-left">
            <template v-if="selectedGroup.reviewer">
              <span
                class="avatar-round"
                :class="getAvatarClass(selectedGroup.reviewer.provider_name || selectedGroup.reviewer.provider_id)"
              >
                {{ getInitials(selectedGroup.reviewer.provider_name || selectedGroup.reviewer.provider_id) }}
              </span>
              <div>
                <b>{{ selectedGroup.reviewer.provider_name || selectedGroup.reviewer.provider_id }}</b>
                <small>{{ t('gui.ai.groups.role_reviewer') }}</small>
              </div>
            </template>
            <div v-else class="text-muted">
              {{ t('gui.ai.groups.no_reviewer') }}
            </div>
          </div>
          <div class="role-tags">
            <span class="role cyan">{{ t('gui.ai.groups.role_reviewer') }}</span>
            <Badge
              v-if="selectedGroup.reviewer && !selectedGroup.reviewer.is_available"
              tone="warning"
              size="sm"
            >
              {{ t('gui.ai.groups.unavailable_role') }}
            </Badge>
          </div>
        </div>

        <!-- Reworker row -->
        <div class="member-detail-row">
          <div class="member-left">
            <template v-if="selectedGroup.reworker">
              <span
                class="avatar-round"
                :class="getAvatarClass(selectedGroup.reworker.provider_name || selectedGroup.reworker.provider_id)"
              >
                {{ getInitials(selectedGroup.reworker.provider_name || selectedGroup.reworker.provider_id) }}
              </span>
              <div>
                <b>{{ selectedGroup.reworker.provider_name || selectedGroup.reworker.provider_id }}</b>
                <small>{{ t('gui.ai.groups.role_reworker') }}</small>
              </div>
            </template>
            <div v-else class="text-muted">
              {{ t('gui.ai.groups.no_reworker') }}
            </div>
          </div>
          <div class="role-tags">
            <span class="role purple">{{ t('gui.ai.groups.role_reworker') }}</span>
            <Badge
              v-if="selectedGroup.reworker && !selectedGroup.reworker.is_available"
              tone="warning"
              size="sm"
            >
              {{ t('gui.ai.groups.unavailable_role') }}
            </Badge>
          </div>
        </div>

        <!-- Role mapping section -->
        <div class="group-section-title">
          <span>{{ t('gui.ai.groups.role_mapping') }}</span>
        </div>
        <div class="mapping-row">
          <span>
            <b>{{ t('gui.ai.groups.role_executor') }}:</b>
            {{ selectedGroup.work_executor?.provider_name || selectedGroup.work_executor?.provider_id || t('gui.ai.common.none') }}
          </span>
          <span>
            <b>{{ t('gui.ai.groups.role_reviewer') }}:</b>
            {{ selectedGroup.reviewer?.provider_name || selectedGroup.reviewer?.provider_id || t('gui.ai.common.none') }}
          </span>
          <span>
            <b>{{ t('gui.ai.groups.role_reworker') }}:</b>
            {{ selectedGroup.reworker?.provider_name || selectedGroup.reworker?.provider_id || t('gui.ai.common.none') }}
          </span>
        </div>
      </section>

      <!-- Empty state when no group selected -->
      <section v-else class="group-detail empty-detail">
        <p>{{ t('gui.ai.groups.select_group_prompt') }}</p>
      </section>
    </div>

    <!-- Create/Edit Modal -->
    <AiGroupEditDialog
      :open="dialogOpen"
      :group="editingGroup"
      :providers="providers"
      :saving="saving"
      :error="modalError"
      @close="dialogOpen = false"
      @save="onSaveGroup"
    />

    <!-- Delete Confirm Dialog -->
    <ConfirmDialog
      :open="!!deletingGroup"
      :title="t('gui.ai.groups.delete_confirm_title')"
      :message="t('gui.ai.groups.delete_confirm_msg', { name: deletingGroup?.name })"
      danger
      @confirm="onDeleteConfirmed"
      @cancel="deletingGroup = null"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Badge from '@/components/ui/Badge.vue'
import Button from '@/components/ui/Button.vue'
import ConfirmDialog from '@/components/ui/ConfirmDialog.vue'
import { createAiGroup, deleteAiGroup, listAiGroups, updateAiGroup } from '../api'
import type { AiGroup, AiGroupInput, ProviderItem } from '../types'
import AiGroupEditDialog from './AiGroupEditDialog.vue'

const props = defineProps<{
  projectId: string
  providers: ProviderItem[]
  initialGroups: AiGroup[]
}>()

const emit = defineEmits<{
  changed: []
}>()

const { t } = useI18n()

const groups = ref<AiGroup[]>([...props.initialGroups])
const selectedGroup = ref<AiGroup | null>(groups.value[0] ?? null)
const searchQuery = ref('')
const error = ref('')
const modalError = ref<string | null>(null)
const saving = ref(false)

const dialogOpen = ref(false)
const editingGroup = ref<AiGroup | null>(null)
const deletingGroup = ref<AiGroup | null>(null)

let requestGen = 0

watch(
  () => props.initialGroups,
  (val) => {
    groups.value = [...val]
    if (!selectedGroup.value && groups.value.length > 0) {
      selectedGroup.value = groups.value[0]
    } else if (selectedGroup.value) {
      selectedGroup.value = groups.value.find((g) => g.ai_group_id === selectedGroup.value?.ai_group_id) ?? groups.value[0] ?? null
    }
  },
  { deep: true }
)

const filteredGroups = computed(() => {
  const q = searchQuery.value.trim().toLowerCase()
  if (!q) return groups.value
  return groups.value.filter(
    (g) =>
      g.name.toLowerCase().includes(q) ||
      (g.description || '').toLowerCase().includes(q)
  )
})

function hasBrokenRole(g: AiGroup): boolean {
  return (
    (g.work_executor !== null && !g.work_executor.is_available) ||
    (g.reviewer !== null && !g.reviewer.is_available) ||
    (g.reworker !== null && !g.reworker.is_available)
  )
}

async function load(): Promise<void> {
  const project = props.projectId
  const gen = ++requestGen
  if (!project) return

  try {
    const items = await listAiGroups(project)
    if (gen !== requestGen || props.projectId !== project) return
    groups.value = items
    error.value = ''
    if (selectedGroup.value) {
      selectedGroup.value = items.find((g) => g.ai_group_id === selectedGroup.value?.ai_group_id) ?? items[0] ?? null
    } else {
      selectedGroup.value = items[0] ?? null
    }
  } catch (err: unknown) {
    if (gen === requestGen) {
      error.value = err instanceof Error ? err.message : 'Failed to load AI groups'
    }
  }
}

function openCreate() {
  editingGroup.value = null
  modalError.value = null
  dialogOpen.value = true
}

function openEdit(group: AiGroup) {
  editingGroup.value = group
  modalError.value = null
  dialogOpen.value = true
}

async function onSaveGroup(input: AiGroupInput) {
  if (!props.projectId) return
  saving.value = true
  modalError.value = null

  try {
    if (editingGroup.value) {
      const updated = await updateAiGroup(props.projectId, editingGroup.value.ai_group_id, input)
      dialogOpen.value = false
      selectedGroup.value = updated
    } else {
      const created = await createAiGroup(props.projectId, input)
      dialogOpen.value = false
      selectedGroup.value = created
    }
    await load()
    emit('changed')
  } catch (err: unknown) {
    modalError.value = err instanceof Error ? err.message : 'Failed to save group'
  } finally {
    saving.value = false
  }
}

function confirmDelete(group: AiGroup) {
  deletingGroup.value = group
}

async function onDeleteConfirmed() {
  if (!deletingGroup.value || !props.projectId) return
  const id = deletingGroup.value.ai_group_id
  deletingGroup.value = null

  try {
    await deleteAiGroup(props.projectId, id)
    if (selectedGroup.value?.ai_group_id === id) {
      selectedGroup.value = null
    }
    await load()
    emit('changed')
  } catch (err: unknown) {
    error.value = err instanceof Error ? err.message : 'Failed to delete group'
  }
}

function getInitials(name: string): string {
  if (!name) return 'AI'
  const parts = name.trim().split(/\s+/)
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase()
  return name.slice(0, 2).toUpperCase()
}

function getAvatarClass(name: string): string {
  const n = (name || '').toLowerCase()
  if (n.includes('sonnet') || n.includes('claude')) return 'av-cs'
  if (n.includes('opus')) return 'av-co'
  if (n.includes('haiku')) return 'av-ch'
  if (n.includes('gpt') || n.includes('openai')) return 'av-og'
  if (n.includes('gemini')) return 'av-gp'
  if (n.includes('llama')) return 'av-l3'
  if (n.includes('terra')) return 'av-te'
  if (n.includes('luna')) return 'av-lu'
  return 'av-cs'
}
</script>

<style scoped>
.ai-groups-panel {
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: 100%;
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
  margin: 0;
}

.detail-actions {
  margin-left: auto;
}

.groups-layout {
  display: grid;
  grid-template-columns: 280px minmax(0, 1fr);
  gap: 16px;
  flex: 1;
  min-height: 0;
}

.group-index {
  border: 1px solid var(--fg2-line);
  border-radius: 12px;
  background: #0d1b2b;
  padding: 12px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.detail-search {
  height: 34px;
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 10px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: 8px;
  background: #0c1b2c;
  color: #8399b0;
  font-size: 12.5px;
  margin-bottom: 8px;
}

.detail-search input {
  min-width: 0;
  flex: 1;
  border: 0;
  outline: 0;
  background: transparent;
  color: #dce7f3;
  font-size: 12px;
}

.group-index-item {
  padding: 10px 12px;
  border-radius: 8px;
  border: 1px solid transparent;
  cursor: pointer;
  transition: 0.16s ease;
}

.group-index-item:hover {
  background: #10243a;
}

.group-index-item.active {
  background: linear-gradient(90deg, #173f70, #102a48);
  border-color: #2f6bab;
}

.index-item-title {
  display: flex;
  align-items: center;
  gap: 6px;
}

.index-item-title .star {
  color: #ffcf59;
  font-size: 13px;
}

.group-index-item b {
  font-size: 13px;
  color: #edf3fa;
}

.group-index-item small {
  margin-top: 3px;
  color: var(--fg2-muted);
  font-size: 10.5px;
  display: block;
}

.warn-dot {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: #f59e0b;
  color: #111;
  font-size: 9px;
  font-weight: 900;
  margin-left: auto;
}

.empty-index {
  padding: 24px 12px;
  text-align: center;
  color: var(--fg2-muted);
  font-size: 12px;
}

.group-detail {
  border: 1px solid var(--fg2-line);
  border-radius: 12px;
  background: #0d1b2b;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
}

.empty-detail {
  align-items: center;
  justify-content: center;
  padding: 48px 24px;
  color: var(--fg2-muted);
}

.group-detail-head {
  min-height: 68px;
  padding: 12px 18px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid var(--fg2-line);
  flex: none;
}

.head-info h2 {
  margin: 0;
  font-size: 17px;
  font-weight: 850;
  color: #edf3fa;
}

.head-info p {
  margin: 4px 0 0;
  color: var(--fg2-muted);
  font-size: 11.5px;
}

.head-actions {
  display: flex;
  gap: 8px;
}

.group-section-title {
  min-height: 38px;
  padding: 0 16px;
  display: flex;
  align-items: center;
  color: #9eb4ca;
  font-size: 11.5px;
  font-weight: 800;
  background: #0a1726;
  border-top: 1px solid rgba(154, 181, 210, 0.09);
  border-bottom: 1px solid rgba(154, 181, 210, 0.09);
  flex: none;
}

.supervisor-detail {
  display: grid;
  grid-template-columns: 40px minmax(0, 1fr) auto;
  gap: 12px;
  align-items: center;
  padding: 14px 18px;
  background: #0c1928;
}

.supervisor-info .name-line {
  display: flex;
  align-items: center;
  gap: 8px;
}

.supervisor-info b {
  font-size: 13.5px;
  color: #edf3fa;
}

.supervisor-info small {
  margin-top: 3px;
  color: var(--fg2-muted);
  font-size: 11px;
  display: block;
}

.axis-chips {
  display: flex;
  gap: 6px;
}

.axis-chips span {
  height: 24px;
  padding: 0 8px;
  border-radius: 6px;
  background: #11263b;
  border: 1px solid rgba(93, 145, 199, 0.18);
  color: #b9d4ee;
  font-size: 10.5px;
  display: inline-flex;
  align-items: center;
}

.member-detail-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 18px;
  border-top: 1px solid rgba(154, 181, 210, 0.07);
}

.member-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.member-left b {
  font-size: 13px;
  color: #edf3fa;
  display: block;
}

.member-left small {
  font-size: 10.5px;
  color: var(--fg2-muted);
  display: block;
  margin-top: 2px;
}

.role-tags {
  display: flex;
  align-items: center;
  gap: 8px;
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
}

.role.cyan { background: #102b2d; border-color: rgba(73, 223, 208, 0.2); color: #95eee5; }
.role.purple { background: #241a3d; border-color: rgba(163, 123, 255, 0.22); color: #d0bdfd; }

.mapping-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 14px 18px 20px;
}

.mapping-row span {
  height: 28px;
  padding: 0 10px;
  border: 1px solid var(--fg2-line);
  border-radius: 7px;
  background: #102238;
  color: #a9bdd1;
  font-size: 11.5px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.mapping-row b {
  color: #e3eef9;
}

.avatar-round {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  font-weight: 800;
  font-size: 11px;
  color: #fff;
  flex: none;
}

.av-cs, .av-co, .av-ch { background: linear-gradient(145deg, #d98466, #8d3f31); }
.av-og, .av-o4, .av-lu { background: linear-gradient(145deg, #67a2ff, #3667d7); }
.av-gp { background: linear-gradient(145deg, #9b79ff, #6f49d7); }
.av-l3 { background: linear-gradient(145deg, #58adff, #2e77c6); }
.av-te { background: linear-gradient(145deg, #e6b089, #b97851); }

.text-muted {
  color: var(--fg2-muted);
  font-size: 12px;
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

@media (max-width: 1200px) {
  .groups-layout {
    grid-template-columns: 1fr;
  }
}
</style>