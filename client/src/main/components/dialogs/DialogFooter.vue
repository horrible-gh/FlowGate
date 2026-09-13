<template>
  <div class="fg-dialog-footer">
    <!-- A failed action keeps the dialog open and says so here (L0009 §2 "Async
         action"): the feature's own error surface, if it has one, is additional. -->
    <p v-if="lastError" class="fg-dialog-footer__error" role="alert">{{ lastError.message }}</p>
    <div class="fg-dialog-footer__actions">
      <button
        v-for="action in orderedActions"
        :key="action.id"
        type="button"
        class="fg-dialog-btn"
        :class="[
          `fg-dialog-btn--${action.role}`,
          `fg-dialog-btn--tone-${action.tone ?? 'default'}`,
          { 'is-loading': isActionLoading(action) },
        ]"
        :data-dialog-action-role="action.role"
        :data-dialog-action-id="action.id"
        :disabled="isActionDisabled(action)"
        @click="onFooterClick(action)"
      >
        <!-- The slot swaps what a button shows, never where it sits: position belongs
             to the ordering contract below (D0008 §5 "DialogFooter"). -->
        <slot :name="`action-${action.id}`" :action="action" :loading="isActionLoading(action)">
          <AppIcon v-if="isActionLoading(action)" name="spinner" spin />
          {{ action.label }}
        </slot>
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * Common dialog footer — semantic action ordering and the single execution path.
 *
 * flowgate.default.0560 T0012 §2.5 / design: D0008 §2 "DialogFooter" + §6 "Footer 규칙",
 * logic: L0009 §2 "DialogFooter ordering과 로직 계약".
 *
 * This is the component R0001 is actually about. Feature code hands over a list of
 * semantic actions and loses the ability to place them: cancel is always immediately
 * left of the primary button, a separate danger action is always left of cancel, and a
 * "stop the running work" action sits between them. A screen cannot opt out of the
 * order by rendering its own buttons in its own sequence any more.
 */
import { computed, ref, watchEffect } from 'vue'

import AppIcon from '@shared/AppIcon.vue'

import { footerRoleMaxCount, footerRolePriority, type DialogAction, type DialogActionRole } from './dialogTypes'
import { dialogDevWarn } from '../../composables/useDialogStack'

export interface DialogFooterProps {
  actions: DialogAction[]
  busy?: boolean
  disabled?: boolean
}

const props = withDefaults(defineProps<DialogFooterProps>(), {
  busy: false,
  disabled: false,
})

const emit = defineEmits<{ select: [action: DialogAction] }>()

/**
 * In-flight action ids. They live in this footer instance and nowhere else: the
 * `DialogAction` objects arrive as props and belong to the caller, so writing
 * `action.loading` here would be mutating someone else's state (L0009 §2 "Async
 * action"). Rendering ORs the caller's flags with these instead.
 */
const runningActionIds = ref<ReadonlySet<string>>(new Set())

const lastError = ref<{ actionId: string; message: string } | null>(null)

function isRunning(action: DialogAction): boolean {
  return runningActionIds.value.has(action.id)
}

function isActionDisabled(action: DialogAction): boolean {
  if (action.disabled === true) return true
  if (props.disabled) return true
  if (isRunning(action)) return true
  if (props.busy) return busyDisables(action.role)
  return false
}

/**
 * L0009 §2 "Busy / Blocking": busy means "an action is running", not "the dialog is
 * sealed". Stopping the run and dismissing a transient overlay have to stay clickable
 * while busy — a stop button that greys out the moment work starts is useless.
 * `cancel` follows the documented default (disabled while busy); a feature that wants
 * it live passes its own flags, and since composition is OR it can only tighten.
 */
function busyDisables(role: DialogActionRole): boolean {
  return role !== 'stop' && role !== 'dismiss'
}

function isActionLoading(action: DialogAction): boolean {
  return action.loading === true || isRunning(action)
}

/**
 * L0009 §2 "ordering 알고리즘".
 *
 * key = (role priority, order ?? caller_index, caller_index)
 *
 * `order` is read with an explicit `undefined` check because `0` is a legitimate value
 * — a falsy test would quietly demote every `order: 0` action to its caller position.
 * The third key makes the result deterministic regardless of sort stability.
 */
const orderedActions = computed<DialogAction[]>(() => {
  const indexed = props.actions.map((action, callerIndex) => ({ action, callerIndex }))
  indexed.sort((a, b) => {
    const byRole = footerRolePriority[a.action.role] - footerRolePriority[b.action.role]
    if (byRole !== 0) return byRole
    const aOrder = a.action.order !== undefined ? a.action.order : a.callerIndex
    const bOrder = b.action.order !== undefined ? b.action.order : b.callerIndex
    if (aOrder !== bOrder) return aOrder - bOrder
    return a.callerIndex - b.callerIndex
  })
  return indexed.map((item) => item.action)
})

/**
 * L0009 §4 "Contract violation 체크 분기". Development-time only: production keeps
 * rendering in sorted order rather than taking a screen down over a bad action list.
 * Runs on every change to `actions`, because a violation introduced by a later update
 * is exactly as wrong as one present on the first render.
 */
watchEffect(() => {
  if (!import.meta.env.DEV) return
  const seen = new Set<string>()
  const roleCounts = new Map<DialogActionRole, number>()
  for (const action of props.actions) {
    if (seen.has(action.id)) {
      dialogDevWarn(`duplicate action id "${action.id}" in a dialog footer`)
    }
    seen.add(action.id)
    roleCounts.set(action.role, (roleCounts.get(action.role) ?? 0) + 1)
  }
  for (const [role, max] of Object.entries(footerRoleMaxCount)) {
    const count = roleCounts.get(role as DialogActionRole) ?? 0
    if (max !== undefined && count > max) {
      dialogDevWarn(`${count} actions with role "${role}" in one dialog footer (max ${max})`)
    }
  }
})

/**
 * The single click path (L0009 §0 규칙 3, §2 "action 실행 주체").
 *
 * `select` is emitted first and is purely an observation signal — logging, analytics, a
 * test watching clicks. Feature code must NOT call `action.onSelect` from it; the one
 * place `onSelect` runs is `runAsyncAction` below. Emitting first keeps the signal even
 * if `onSelect` throws synchronously or closes the dialog outright.
 */
function onFooterClick(action: DialogAction): void {
  if (isActionDisabled(action)) return
  emit('select', action)
  void runAsyncAction(action)
}

async function runAsyncAction(action: DialogAction): Promise<void> {
  markRunning(action.id, true)
  lastError.value = null
  try {
    await action.onSelect(action)
    // The resolved value carries no meaning. Whether the dialog closes on success is
    // the feature's decision, made inside onSelect (L0009 §2 "Async action").
  } catch (error) {
    lastError.value = { actionId: action.id, message: errorMessage(error) }
    dialogDevWarn(`dialog action "${action.id}" failed: ${lastError.value.message}`)
  } finally {
    // Unconditional: releasing only "while still open" leaves a permanently disabled
    // button behind when the dialog is torn down mid-flight and then reopened.
    markRunning(action.id, false)
  }
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message
  return String(error)
}

function markRunning(id: string, value: boolean): void {
  const next = new Set(runningActionIds.value)
  if (value) next.add(id)
  else next.delete(id)
  runningActionIds.value = next
}

defineExpose({
  orderedActions,
  runningActionIds,
  lastError,
})
</script>
