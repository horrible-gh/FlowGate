<template>
  <!-- Same frame as File › 새 Flow (DesignerFlowCreateDialog): header + ✕, labelled branch fields, right-aligned actions. -->
  <ModalShell :open="open" size="md" surface-class="flow-base-dialog" @close="emit('close')">
    <template #header="{ titleId }">
      <div class="fbd-head">
        <h2 :id="titleId" class="fbd-title">{{ t('gui.flow_base.title') }}</h2>
        <button type="button" class="fbd-close" :aria-label="t('gui.designer.file_menu.close')"
          :title="t('gui.designer.file_menu.close')" data-test="flow-base-close" @click="emit('close')">
          <svg width="19" height="19" viewBox="0 0 20 20" fill="none" aria-hidden="true">
            <path d="M5 5l10 10M15 5L5 15" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" />
          </svg>
        </button>
      </div>
    </template>

    <div data-test="flow-base" :data-state="state" class="fbd-form">
      <div class="fbd-field">
        <span class="fbd-label">{{ t('gui.flow_base.current') }}</span>
        <div class="fbd-control fbd-branch-control is-readonly" :aria-busy="state === 'loading' || undefined">
          <svg class="fbd-branch-icon" viewBox="0 0 20 20" fill="none" aria-hidden="true">
            <circle cx="5.5" cy="4.5" r="2" stroke="currentColor" stroke-width="1.5" />
            <circle cx="5.5" cy="15.5" r="2" stroke="currentColor" stroke-width="1.5" />
            <circle cx="15" cy="8" r="2" stroke="currentColor" stroke-width="1.5" />
            <path d="M5.5 6.5v7M13 8h-3.2C7.5 8 5.5 10 5.5 12.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
          </svg>
          <span class="fbd-value" :title="currentText" data-test="flow-base-current">{{ currentText }}</span>
          <span v-if="state === 'loading'" class="fbd-spinner" role="status" :aria-label="t('gui.common.loading')" />
          <button v-else type="button" class="fbd-retry" :disabled="submitting"
            :aria-label="t('gui.common.refresh')" :title="t('gui.common.refresh')"
            data-test="flow-base-retry" @click="load()">
            <svg viewBox="0 0 20 20" fill="none" aria-hidden="true">
              <path d="M15.8 6.8A6.2 6.2 0 1 0 16 12M16 3v4.5h-4.5" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
          </button>
        </div>
      </div>

      <div v-if="state === 'editable'" class="fbd-field">
        <label class="fbd-label" for="flow-base-choice">{{ t('gui.flow_base.change') }}</label>
        <div class="fbd-control fbd-branch-control" :class="{ 'is-disabled': submitting }">
          <svg class="fbd-branch-icon" viewBox="0 0 20 20" fill="none" aria-hidden="true">
            <circle cx="5.5" cy="4.5" r="2" stroke="currentColor" stroke-width="1.5" />
            <circle cx="5.5" cy="15.5" r="2" stroke="currentColor" stroke-width="1.5" />
            <circle cx="15" cy="8" r="2" stroke="currentColor" stroke-width="1.5" />
            <path d="M5.5 6.5v7M13 8h-3.2C7.5 8 5.5 10 5.5 12.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
          </svg>
          <Select id="flow-base-choice" v-model="selected" :options="options" :disabled="submitting"
            :placeholder="t('gui.flow_base.select')" data-test="flow-base-choice" />
          <svg class="fbd-chevron" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <path d="m4 6 4 4 4-4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
        </div>
      </div>

      <p v-if="message" role="status" class="fbd-message" :class="{ 'is-error': messageIsError }" data-test="flow-base-message">{{ t('gui.flow_base.' + message) }}</p>
    </div>

    <template #footer>
      <div class="fbd-actions">
        <Button variant="secondary" data-test="flow-base-cancel" @click="emit('close')">{{ t('gui.common.cancel') }}</Button>
        <Button v-if="state === 'editable'" variant="primary" :disabled="!canSave" :loading="submitting" data-test="flow-base-save" @click="save">{{ t('gui.common.save') }}</Button>
      </div>
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
const currentText = computed(() => state.value === 'loading'
  ? t('gui.common.loading') : view.value?.effectiveWorkBaseRef ?? '—')
// Read-only reasons are information; everything else that lands in `message` is a failure.
const notices = new Set(['denied', 'disposed', 'git_disabled', 'checkout_unavailable', 'locked', 'busy'])
const messageIsError = computed(() => !!message.value && !notices.has(message.value))

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

<style>
/* Same surface as DesignerFlowCreateDialog (.dfm-create-dialog); explicit class keeps the shared shell unchanged elsewhere. */
.flow-base-dialog.ui-modal-surface {
  max-width: 480px;
  background: linear-gradient(150deg, #132238 0%, #101d30 53%, #101b2c 100%);
  border: 1px solid rgba(143, 169, 203, .21);
  border-radius: 15px;
  box-shadow: 0 28px 80px rgba(0, 0, 0, .32), 0 4px 16px rgba(0, 0, 0, .2), inset 0 1px rgba(255, 255, 255, .035);
  color: var(--fg2-text, #ecf3fb);
}
.flow-base-dialog > .ui-modal-body {
  padding: 0 29px;
  overflow: visible;
}
@media (max-width: 500px) {
  .flow-base-dialog > .ui-modal-body { padding: 0 20px; }
}
</style>

<style scoped>
.fbd-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 27px 29px 25px;
}
.fbd-title {
  margin: 0;
  font-size: 17px;
  line-height: 24px;
  font-weight: 670;
  letter-spacing: -.5px;
}
.fbd-close, .fbd-retry {
  display: inline-grid;
  place-items: center;
  flex: none;
  padding: 0;
  border: 0;
  background: transparent;
  color: #8fa4bb;
  cursor: pointer;
}
.fbd-close {
  width: 30px;
  height: 30px;
  margin: -3px -7px -3px 0;
  border-radius: 8px;
}
.fbd-close:hover, .fbd-retry:hover:not(:disabled) { background: rgba(255, 255, 255, .07); color: #e8f2ff; }
.fbd-close:focus-visible, .fbd-retry:focus-visible {
  outline: 2px solid var(--fg2-focus, #77aaff);
  outline-offset: 2px;
}
.fbd-form { display: grid; gap: 20px; }
.fbd-field { min-width: 0; }
.fbd-label {
  display: block;
  margin: 0 0 8px;
  font-size: 12px;
  line-height: 16px;
  font-weight: 570;
  color: #a6b9cd;
}
.fbd-control {
  position: relative;
  display: flex;
  align-items: center;
  height: 43px;
  box-sizing: border-box;
  border: 1px solid #2b4058;
  border-radius: 9px;
  background: rgba(7, 17, 31, .54);
  transition: border-color .16s, background .16s, box-shadow .16s;
}
.fbd-control:hover:not(.is-disabled):not(.is-readonly) { background: rgba(12, 28, 47, .85); border-color: #38526d; }
.fbd-control:focus-within { border-color: #639af4; box-shadow: 0 0 0 3px rgba(72, 139, 242, .10); }
.fbd-control.is-readonly { background: rgba(7, 17, 31, .32); }
.fbd-control :deep(.ui-select) {
  display: block;
  width: 100%;
  height: 100%;
  min-width: 0;
  box-sizing: border-box;
  padding: 0 42px 0 36px;
  background: transparent;
  border: 0;
  border-radius: inherit;
  color: var(--fg2-text, #ecf3fb);
  font-family: inherit;
  font-size: 13px;
  font-weight: 500;
  outline: none;
  cursor: pointer;
}
.fbd-control :deep(.ui-select:disabled) { color: #7f96ae; cursor: default; opacity: 1; }
.fbd-control :deep(.ui-select option) { background: #15253a; color: #ecf3fb; }
.fbd-value {
  flex: 1;
  min-width: 0;
  padding: 0 44px 0 36px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  color: var(--fg2-text, #ecf3fb);
  font-size: 13px;
  font-weight: 500;
}
.fbd-chevron {
  position: absolute;
  right: 13px;
  top: 50%;
  transform: translateY(-50%);
  width: 14px;
  height: 14px;
  color: #92aac4;
  pointer-events: none;
}
.fbd-branch-icon { position: absolute; left: 13px; width: 15px; height: 15px; color: #7b9ac1; pointer-events: none; }
.fbd-spinner {
  position: absolute;
  right: 14px;
  width: 14px;
  height: 14px;
  box-sizing: border-box;
  border: 1.8px solid rgba(160, 185, 220, .2);
  border-top-color: #8bb5ef;
  border-radius: 50%;
  animation: fbd-spin .8s linear infinite;
}
@keyframes fbd-spin { to { transform: rotate(360deg); } }
.fbd-retry { position: absolute; right: 6px; width: 31px; height: 31px; border-radius: 7px; color: #a7bcd3; }
.fbd-retry svg { width: 16px; height: 16px; }
.fbd-retry:disabled { cursor: default; opacity: .43; }
.fbd-message { margin: -8px 0 0; color: #8fa4bb; font-size: 11.5px; line-height: 16px; overflow-wrap: anywhere; }
.fbd-message.is-error { color: #e0a6a1; }
.fbd-actions { display: flex; justify-content: flex-end; gap: 9px; padding: 28px 29px 24px; }
.fbd-actions :deep(.ui-btn) { height: 37px; padding: 0 17px; border-radius: 8px; font-size: 12.5px; font-weight: 620; }
.fbd-actions :deep(.ui-btn--secondary) { background: transparent; border-color: #36506b; color: #c2d0df; }
.fbd-actions :deep(.ui-btn--secondary:hover) { background: rgba(255, 255, 255, .05); border-color: #506985; }
.fbd-actions :deep(.ui-btn--primary) { min-width: 74px; background: linear-gradient(#508ff0, #3373d7); border-color: #548fe9; color: #fff; box-shadow: inset 0 1px rgba(255, 255, 255, .15); }
.fbd-actions :deep(.ui-btn--primary:hover:not(:disabled)) { background: linear-gradient(#69a0f7, #4783e6); filter: none; }
.fbd-actions :deep(.ui-btn--primary:disabled) { opacity: .43; box-shadow: none; }
@media (max-width: 500px) {
  .fbd-head { padding: 23px 20px 21px; }
  .fbd-form { gap: 17px; }
  .fbd-actions { padding: 28px 20px 21px; }
}
@media (prefers-reduced-motion: reduce) {
  .fbd-spinner { animation: none; }
  .fbd-control, .fbd-close, .fbd-retry, .fbd-actions :deep(.ui-btn) { transition: none; }
}
</style>
