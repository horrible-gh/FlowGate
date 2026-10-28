<template>
  <ModalShell :open="open" size="md" close-on-backdrop @close="emit('close')">
    <template #header="{ titleId }">
      <h2 :id="titleId">{{ t('gui.flow_base.title') }}</h2>
    </template>
    <div data-test="flow-base" :data-state="state" class="flow-base">
      <p data-test="flow-base-current">{{ t('gui.flow_base.current') }}: {{ state === 'loading' ? t('gui.common.loading') : view?.effectiveWorkBaseRef ?? '—' }}</p>
      <p v-if="message" role="status" data-test="flow-base-message">{{ t('gui.flow_base.' + message) }}</p>
      <template v-if="state === 'editable'">
        <label for="flow-base-choice">{{ t('gui.flow_base.change') }}</label>
        <Select id="flow-base-choice" v-model="selected" :options="options" :disabled="submitting"
          :placeholder="t('gui.flow_base.select')" data-test="flow-base-choice" />
      </template>
      <Button v-if="state !== 'loading'" variant="secondary" :disabled="submitting" data-test="flow-base-retry" @click="load()">{{ t('gui.common.refresh') }}</Button>
    </div>
    <template #footer>
      <Button variant="secondary" data-test="flow-base-cancel" @click="emit('close')">{{ t('gui.common.cancel') }}</Button>
      <Button v-if="state === 'editable'" variant="primary" :disabled="!canSave" :loading="submitting" data-test="flow-base-save" @click="save">{{ t('gui.common.save') }}</Button>
    </template>
  </ModalShell>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useCurrentUser } from '@/core/auth/currentUser'
import { useProjectStore, type FlowWorkBase } from '@/features/project'
import ModalShell from '../../../components/ui/ModalShell.vue'
import Button from '../../../components/ui/Button.vue'
import Select, { type SelectOption } from '../../../components/ui/Select.vue'

const props = defineProps<{ open: boolean; projectId: string | null; groupId: string | null }>()
const emit = defineEmits<{ close: []; saved: [] }>()
const { t } = useI18n()
const store = useProjectStore()
const { user, status: userStatus } = useCurrentUser()
type State = 'loading' | 'editable' | 'readonly' | 'error'
const state = ref<State>('loading')
const view = ref<FlowWorkBase | null>(null)
const selected = ref('')
const options = ref<SelectOption[]>([])
const message = ref<string | null>(null)
const submitting = ref(false)
let generation = 0
const canSave = computed(() => state.value === 'editable' && !submitting.value && !!view.value
  && selected.value !== '' && selected.value !== view.value.effectiveWorkBaseRef
  && options.value.some((option) => option.value === selected.value))
const reasons: Record<string, string> = {
  disposed: 'disposed', git_disabled: 'git_disabled', checkout_unavailable: 'checkout_unavailable',
  history_locked: 'locked', provision_busy: 'busy',
}
const failures: Record<string, string> = {
  group_disposed: 'disposed', group_work_base_git_disabled: 'git_disabled',
  group_work_base_checkout_unavailable: 'checkout_unavailable', group_work_base_locked: 'locked',
  group_work_base_remote_only: 'remote_only', group_work_base_internal: 'internal',
  group_work_base_changed: 'changed', group_work_base_busy: 'busy',
  group_work_base_not_found: 'not_found', group_not_found: 'group_not_found',
  group_work_base_invalid: 'invalid', group_work_base_expected_required: 'changed',
  group_work_base_not_commit: 'not_commit', group_work_base_update_failed: 'save_failed',
}
const failure = (error: unknown) => error as { status?: number; code?: string }

async function load() {
  const seq = ++generation
  const pid = props.projectId, gid = props.groupId
  view.value = null
  selected.value = ''
  options.value = []
  message.value = null
  submitting.value = false
  state.value = 'loading'
  if (!props.open || !pid || !gid) return
  try {
    const result = await store.loadFlowWorkBase(gid)
    if (seq !== generation) return
    if (result.projectId !== pid || result.groupId !== gid) throw new Error('Wrong work-base scope')
    view.value = result
    if (!result.workBaseEditable || !result.canEdit) {
      state.value = 'readonly'
      message.value = reasons[result.reason ?? ''] ?? 'denied'
      return
    }
    // Server authority is read before any candidate request. Keep save absent until candidates arrive.
    try {
      const candidates = await store.loadWorkBaseOptions(pid, { force: true })
      if (seq !== generation) return
      if (!candidates.gitEnabled) {
        state.value = 'readonly'; message.value = 'git_disabled'; return
      }
      options.value = candidates.branches.map((branch) => ({ value: branch.name, label: branch.name }))
      state.value = 'editable'
      // Deliberately no default: a new read (especially after 409) requires a new selection.
    } catch (error) {
      if (seq !== generation) return
      state.value = 'readonly'
      message.value = failure(error).status === 403 ? 'options_denied' : 'options_failed'
    }
  } catch (error) {
    if (seq !== generation) return
    state.value = 'error'
    message.value = failure(error).status === 403 ? 'read_denied' : 'read_failed'
  }
}

async function save() {
  if (!canSave.value || !view.value || !props.projectId || !props.groupId) return
  const seq = generation
  const pid = props.projectId, gid = props.groupId
  const input = { workBaseRef: selected.value, expectedWorkBaseRef: view.value.workBaseRef }
  submitting.value = true
  message.value = null
  try {
    await store.saveFlowWorkBase(pid, gid, input)
    if (seq !== generation) return
    emit('saved')
    emit('close')
  } catch (error) {
    if (seq !== generation) return
    const { status, code } = failure(error)
    message.value = status === 403 ? 'denied' : failures[code ?? ''] ?? 'save_failed'
    if (status === 409 || status === 403) {
      selected.value = ''
      options.value = []
      state.value = 'readonly'
      // Explicit refresh only: no polling or replay of the rejected choice.
    }
  } finally {
    if (seq === generation) submitting.value = false
  }
}

watch([() => props.open, () => props.projectId, () => props.groupId, user, userStatus], () => {
  void load()
}, { immediate: true, deep: true, flush: 'sync' })
onBeforeUnmount(() => { generation += 1 })
</script>

<style scoped>
.flow-base { display: grid; gap: 12px; }
h2 { font-size: 1rem; margin: 0; }
p { margin: 0; overflow-wrap: anywhere; }
</style>
