<template>
  <div class="modal-bg" role="dialog" aria-modal="true">
    <div class="modal-box">
      <div class="modal-hd">
        <span class="modal-title">
          <span class="doc-tag" :class="`c-${rootType}`" style="font-size:.7rem; padding:2px 6px; margin-right:4px;">{{ rootType }}</span>
          {{ $t(`main.new_requirement_modal.title_${rootType}`) }}
        </span>
        <button class="modal-close" type="button" @click="$emit('close')">
          <AppIcon name="x" />
        </button>
      </div>

      <div class="modal-bd">
        <div class="root-tabs" role="tablist" :aria-label="$t('main.new_requirement_modal.root_type_label')">
          <button
            v-for="type in rootTypes"
            :key="type"
            class="root-tab"
            :class="{ active: rootType === type, bug: type === 'B' }"
            type="button"
            role="tab"
            :aria-selected="rootType === type"
            @click="rootType = type"
          >
            <span class="root-tab-icon">
              <AppIcon :name="type === 'B' ? 'bug' : 'list-checks'" />
            </span>
            <span>
              <strong>
                {{ $t(`main.new_requirement_modal.tab_${type}_title`) }}
                <span class="doc-tag" :class="`c-${type}`">{{ type }}</span>
              </strong>
              <small>{{ $t(`main.new_requirement_modal.tab_${type}_desc`) }}</small>
            </span>
          </button>
        </div>

        <div class="req-start-info" :class="{ bug: rootType === 'B' }">
          <AppIcon name="info" style="margin-top:1px; flex-shrink:0;" />
          <i18n-t :keypath="`main.new_requirement_modal.info_text_${rootType}`" tag="span">
            <template #emphasis>
              <strong>{{ $t('main.new_requirement_modal.starting_point') }}</strong>
            </template>
          </i18n-t>
        </div>

        <form @submit.prevent="submit">
          <div class="form-group">
            <label class="form-label req">{{ $t('main.new_requirement_modal.project_label') }}</label>
            <select v-model="form.project" class="form-ctrl" @change="onProjectChange">
              <option value="" disabled>{{ $t('main.new_requirement_modal.select_project') }}</option>
              <option v-for="p in projects" :key="p.project" :value="p.project">
                {{ p.project }}
              </option>
            </select>
          </div>

          <div v-if="currentModules.length > 0" class="form-group">
            <label class="form-label">{{ $t('main.new_requirement_modal.module_label') }}</label>
            <select v-model="form.module" class="form-ctrl">
              <option v-for="m in currentModules" :key="m.id" :value="m.id">{{ m.label }}</option>
            </select>
          </div>

          <div class="form-group">
            <label class="form-label req">{{ $t('main.new_requirement_modal.group_label') }}</label>
            <div class="group-toggle">
              <button
                class="group-toggle-btn"
                :class="{ active: groupMode === 'existing' }"
                type="button"
                @click="groupMode = 'existing'"
              >
                {{ $t('main.new_requirement_modal.existing_group') }}
              </button>
              <button
                class="group-toggle-btn"
                :class="{ active: groupMode === 'new' }"
                type="button"
                @click="groupMode = 'new'"
              >
                {{ $t('main.new_requirement_modal.new_group') }}
              </button>
            </div>

            <div v-if="groupMode === 'existing'">
              <select v-model="form.groupId" class="form-ctrl">
                <option
                  v-for="group in groupOptions"
                  :key="group.id"
                  :value="group.id"
                  :disabled="group.busy"
                >
                  {{ group.busy ? `${group.label} — ${busyHint}` : group.label }}
                </option>
              </select>
            </div>

            <div v-else>
              <input
                v-model="form.newGroupName"
                class="form-ctrl"
                type="text"
                :placeholder="$t('main.new_requirement_modal.new_group_placeholder')"
              />
              <p class="form-hint">{{ $t('main.new_requirement_modal.group_hint') }}</p>
            </div>
          </div>

          <div class="form-group">
            <label class="form-label req">{{ $t('main.new_requirement_modal.title_label') }}</label>
            <div class="title-input-row">
              <input
                v-model="form.title"
                class="form-ctrl"
                id="newReqTitle"
                type="text"
                maxlength="100"
                :placeholder="$t(`main.new_requirement_modal.title_placeholder_${rootType}`)"
                required
              />
              <button
                v-if="groupNameForTitle"
                class="title-fill-btn"
                type="button"
                :aria-label="$t('main.new_requirement_modal.use_group_name')"
                :title="$t('main.new_requirement_modal.use_group_name')"
                @click="applyGroupNameToTitle"
              >
                <AppIcon name="magic-wand" />
              </button>
              <button
                class="title-fill-btn"
                type="button"
                :aria-label="$t('main.new_requirement_modal.fill_document_type_title_tooltip')"
                :title="$t('main.new_requirement_modal.fill_document_type_title_tooltip')"
                @click="applyDocumentTypeTitle"
              >
                <AppIcon name="magic-wand" />
              </button>
            </div>
          </div>

          <div class="form-group">
            <label class="form-label">{{ $t('main.new_requirement_modal.description_label') }} <span style="color:var(--text-m); font-weight:400;">{{ $t('main.new_requirement_modal.optional') }}</span></label>
            <textarea
              v-model="form.description"
              class="form-ctrl"
              rows="3"
              :placeholder="$t(`main.new_requirement_modal.description_placeholder_${rootType}`)"
              style="resize:vertical;"
            ></textarea>
          </div>

          <div class="form-row">
            <div class="form-group" style="margin-bottom:0;">
              <label class="form-label">{{ $t('main.new_requirement_modal.owner_label') }}</label>
              <select v-model="form.owner" class="form-ctrl">
                <option v-for="owner in owners" :key="owner" :value="owner">{{ owner }}</option>
              </select>
            </div>

            <div class="form-group" style="margin-bottom:0;">
              <label class="form-label">{{ $t('main.new_requirement_modal.template_label') }}</label>
              <select v-model="form.template" class="form-ctrl">
                <option value="default">{{ $t(`main.new_requirement_modal.default_template_${rootType}`) }}</option>
                <option value="none">{{ $t('main.new_requirement_modal.empty_template') }}</option>
              </select>
            </div>
          </div>

          <div style="margin-top:16px; padding:10px 14px; background:var(--bg); border-radius:var(--r); display:flex; align-items:center; gap:10px;">
            <label class="toggle" style="flex-shrink:0;">
              <input v-model="form.openAfter" type="checkbox" />
              <span class="toggle-track"></span>
            </label>
            <span style="font-size:.8rem; color:var(--text-s);">{{ $t('main.new_requirement_modal.open_after') }}</span>
          </div>
        </form>
      </div>

      <div class="modal-ft">
        <div v-if="flashMessage" :class="['alert', flashOk ? 'alert-success' : 'alert-danger']" style="width: 100%; margin-bottom: 12px;">
          <AppIcon :name="flashOk ? 'check' : 'warning'" />
          <span>{{ flashMessage }}</span>
        </div>
        <button class="btn btn-secondary" type="button" @click="$emit('close')">
          {{ $t('common.cancel') }}
        </button>
        <button
          class="btn btn-primary"
          type="button"
          :disabled="submitting || targetGroupBusy"
          :title="targetGroupBusy ? busyHint : undefined"
          @click="submit"
        >
          <span v-if="submitting">
            <AppIcon name="spinner" spin />
            {{ $t('main.new_requirement_modal.registering') || 'Registering...' }}
          </span>
          <span v-else>
            <AppIcon name="file-plus" />
            {{ $t('main.new_requirement_modal.create_button') }}
          </span>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import AppIcon from '@shared/AppIcon.vue'
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { extractApiErrorMessage, getRequest, postUrlEncoded } from '@shared/api'
import { useProjectStore } from '../stores/project'
import { useExplorerStore } from '../stores/explorer'
import { useAiInvokeRunsStore } from '../stores/aiInvokeRuns'
import { useToast } from './common/useToast'

interface ModuleItem {
  id: string
  label: string
}

interface ProjectItem {
  project: string
  modules: ModuleItem[]
}

const props = defineProps<{
  initialGroupId?: string | null
}>()

const emit = defineEmits<{ close: []; created: [payload: { docId: string; openAfter: boolean }] }>()
const { t } = useI18n()
const projectStore = useProjectStore()
const explorerStore = useExplorerStore()
const aiInvokeRunsStore = useAiInvokeRunsStore()
const { showToast } = useToast()

// 0424 TR0005 rework — an existing group can have an active AI run; picking it (or
// submitting after one starts mid-edit) must be blocked in the UI itself, not just
// reported back as a toast once the server 423s.
const busyHint = computed(() => t('main.review_action_bar.ai_running_hint'))
function isGroupBusy(groupId: string): boolean {
  return aiInvokeRunsStore.isGroupRunning(groupId) || aiInvokeRunsStore.isGroupInlineVisible(groupId)
}

const projects = ref<ProjectItem[]>([])
const currentModules = ref<Array<{ id: string; label: string }>>([])
const submitting = ref(false)
const flashMessage = ref('')
const flashOk = ref(false)
const groupMode = ref<'existing' | 'new'>('new')
const rootTypes = ['R', 'B'] as const
const rootType = ref<(typeof rootTypes)[number]>('R')

const form = ref({
  project: '',
  module: '',
  groupId: '',
  newGroupName: '',
  title: '',
  description: '',
  owner: 'admin',
  template: 'default',
  openAfter: true,
})

const owners = ['admin', 'copilot', 'reviewer']

const groupOptions = computed(() => {
  const pid = projectStore.currentProjectId
  const nodes = pid ? explorerStore.getCachedGroupTree(pid) ?? [] : []
  const groups = nodes.filter((node) => (
    node.node_type === 'group'
    && !nodes.some((candidate) => (
      candidate.node_type === 'document'
      && candidate.parent_id === node.id
      && ['R', 'B'].includes(candidate.type_code ?? '')
    ))
  ))
  return groups.map((group) => ({
    id: group.id,
    label: group.number ? `${group.number}: ${group.label}` : group.label,
    // Pure group title (no number prefix) — the value dropped into the document
    // title field by the "use group name" button. NR0003 §3/§10.
    name: group.label,
    module: getGroupModule(group, nodes),
    busy: isGroupBusy(group.id),
  }))
})

const targetGroupBusy = computed(() =>
  groupMode.value === 'existing' && !!form.value.groupId && isGroupBusy(form.value.groupId),
)

// Group title to drop into the title field. Existing-group mode → the selected
// group's pure title; new-group mode → the name the user is typing. '' hides the
// button (no group list / nothing selected / empty new-group name).
const groupNameForTitle = computed(() => {
  if (groupMode.value === 'existing') {
    return groupOptions.value.find((group) => group.id === form.value.groupId)?.name ?? ''
  }
  return form.value.newGroupName.trim()
})

// R0001 group 0111: one click fills the title input with the group name, instead of
// hand-typing a placeholder and renaming the document afterwards.
function applyGroupNameToTitle() {
  const name = groupNameForTitle.value
  if (!name) return
  form.value.title = name
}

// group 0369 rejection rework: independent of the group-name button above, this
// fills the title with the localized name of the currently selected R/B root type.
function applyDocumentTypeTitle() {
  form.value.title = t(`main.new_requirement_modal.tab_${rootType.value}_title`)
}

function getGroupModule(group: { id: string; parent_id: string | null }, nodes: Array<{ id: string; label: string }>): string {
  const parent = group.parent_id ? nodes.find((node) => node.id === group.parent_id) : null
  if (parent?.label) return parent.label
  const parts = group.id.split('.')
  return parts.length >= 3 ? parts[1] : ''
}

function applyInitialGroup() {
  if (!props.initialGroupId) return false
  const matched = groupOptions.value.find((group) => group.id === props.initialGroupId)
  if (!matched) return false
  groupMode.value = 'existing'
  form.value.groupId = matched.id
  form.value.module = matched.module || ''
  return true
}

onMounted(async () => {
  if (!projectStore.currentProjectId) {
    try {
      await projectStore.fetchProjects()
    } catch {
      /* Fall back to the FlowGate project list query on project store loading failure. */
    }
  }

  try {
    const res = await getRequest<unknown>('/api/v1/projects')
    const raw = res.data
    const list: unknown[] =
      Array.isArray(raw) ? raw :
      Array.isArray((raw as { data?: unknown[] })?.data) ? (raw as { data: unknown[] }).data :
      Array.isArray((raw as { projects?: unknown[] })?.projects) ? (raw as { projects: unknown[] }).projects :
      []
    projects.value = list.map((item) => {
      const it = item as Record<string, unknown>
      return {
        project: ((it.project ?? it.project_id) ?? '') as string,
        modules: Array.isArray(it.modules)
          ? (it.modules as Array<string | { name?: string; title?: string }>).map((m) =>
              typeof m === 'string' ? { id: m, label: m } : { id: (m.name ?? '') as string, label: ((m.title || m.name) ?? '') as string }
            )
          : [],
      }
    }).filter((p) => p.project !== '__SYSTEM__')

    const currentId = projectStore.currentProjectId
    if (currentId) {
      const found = projects.value.find((p) => p.project === currentId)
      form.value.project = found?.project ?? projects.value[0]?.project ?? ''
      form.value.module = found?.modules[0]?.id ?? projects.value[0]?.modules[0]?.id ?? ''
    } else if (projects.value.length > 0) {
      form.value.project = projects.value[0].project
      form.value.module = projects.value[0].modules[0]?.id ?? ''
    }
    const selectedProject = projects.value.find((p) => p.project === form.value.project)
    currentModules.value = selectedProject?.modules ?? []
  } catch {
    projects.value = []
  }

  const pid = projectStore.currentProjectId
  if (pid) {
    try {
      await explorerStore.fetchGroupTree(pid)
    } catch {
      /* Group list is supplementary UI info; do not block the creation flow. */
    }
  }
})

watch(groupOptions, (groups) => {
  if (groups.length === 0) {
    form.value.groupId = ''
    if (groupMode.value === 'existing') {
      groupMode.value = 'new'
    }
    return
  }

  if (applyInitialGroup()) return

  if (!form.value.groupId || !groups.some((group) => group.id === form.value.groupId)) {
    form.value.groupId = groups[0].id
  }
}, { immediate: true })

function onProjectChange() {
  const found = projects.value.find((p) => p.project === form.value.project)
  currentModules.value = found?.modules ?? []
  form.value.module = currentModules.value[0]?.id ?? ''
}

async function submit() {
  if (targetGroupBusy.value) {
    showToast(busyHint.value, 'danger')
    return
  }
  if (!form.value.project) {
    flashOk.value = false
    flashMessage.value = t('main.requirement.create.select_project')
    return
  }

  if (!form.value.title.trim()) {
    flashOk.value = false
    flashMessage.value = t('main.new_requirement_modal.error_enter_title') || 'Please enter a title'
    return
  }

  if (groupMode.value === 'existing' && !form.value.groupId) {
    flashOk.value = false
    flashMessage.value = t('main.new_requirement_modal.error_select_existing_group') || 'Please select an existing group'
    return
  }

  const groupPayload: Record<string, string> = groupMode.value === 'existing'
    ? { group_id: form.value.groupId }
    : { new_group_name: form.value.newGroupName.trim() || form.value.title.trim() }

  submitting.value = true
  flashMessage.value = ''
  try {
    const response = await postUrlEncoded<{ ok: boolean; errors?: string[]; result?: unknown }>(
      '/api/v1/outbox/create',
      {
        project: form.value.project,
        module: form.value.module,
        title: form.value.title,
        slug: '',
        priority: 'medium',
        body: form.value.description,
        owner: form.value.owner,
        doc_type: rootType.value,
        template: form.value.template,
        ...groupPayload,
      }
    )
    if (!response.data.ok) {
      flashOk.value = false
      flashMessage.value = response.data.errors?.[0] || t('main.requirement.create.error')
      return
    }
    const result = response.data.result as { doc_id?: string } | undefined
    const docId = result?.doc_id ?? ''
    flashOk.value = true
    flashMessage.value = t(`main.new_requirement_modal.success_${rootType.value}`)
    emit('created', { docId, openAfter: form.value.openAfter })
  } catch (error: unknown) {
    flashOk.value = false
      const serverErr = (error as { response?: { data?: { errors?: any[] } } })?.response?.data?.errors?.[0]
      let message = t('main.requirement.create.error')
      if (typeof serverErr === 'string') {
        message = serverErr
      } else if (serverErr && typeof serverErr === 'object') {
        message = serverErr.message || serverErr.code || message
      }
      message = extractApiErrorMessage(error, message)
      // surface server error via toast only — no inline .alert-danger in .modal-ft
      try {
        // show toast above modal (teleport ensures visibility)
        showToast(message, 'danger')
      } catch (e) {
        // best-effort; do not throw from UI error handling
      }
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.root-tabs {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  margin-bottom: 16px;
  padding: 4px;
  border: 1px solid var(--border);
  border-radius: var(--r-lg);
  background: var(--bg);
}

.root-tab {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 10px 12px;
  border: 1px solid transparent;
  border-radius: var(--r);
  color: var(--text-s);
  text-align: left;
}

.root-tab strong {
  display: block;
  color: var(--text);
  font-size: .8rem;
}

.root-tab small {
  display: block;
  margin-top: 2px;
  font-size: .68rem;
}

.root-tab.active {
  border-color: #bfdbfe;
  background: var(--surface);
  box-shadow: var(--sh-sm);
}

.root-tab.bug.active {
  border-color: #fecaca;
}

.root-tab.active strong {
  color: var(--primary);
}

.root-tab.bug.active strong {
  color: #dc2626;
}

.root-tab-icon {
  display: inline-flex;
  width: 30px;
  height: 30px;
  flex: 0 0 30px;
  align-items: center;
  justify-content: center;
  border-radius: 8px;
  background: var(--primary-l);
  color: var(--primary);
}

.root-tab.bug .root-tab-icon {
  background: #fee2e2;
  color: #dc2626;
}

.req-start-info.bug {
  border-color: #fecaca;
  background: #fff1f2;
  color: #b91c1c;
}

/* Title input + "use group name" button (group 0111 / R0001): one click drops the
   group name into the title so it no longer has to be hand-typed and renamed later. */
.title-input-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.title-input-row .form-ctrl {
  flex: 1 1 auto;
  min-width: 0;
}

.title-fill-btn {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border: 1px solid var(--border);
  border-radius: var(--r);
  background: var(--surface);
  color: var(--text-m);
  font-size: 0.9rem;
  cursor: pointer;
  transition: color 0.15s, border-color 0.15s, background 0.15s;
}

.title-fill-btn:hover {
  color: var(--primary);
  border-color: #bfdbfe;
  background: var(--surface-h);
}
</style>
