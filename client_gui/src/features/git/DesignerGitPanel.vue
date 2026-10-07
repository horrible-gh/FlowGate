<template>
  <section class="gx-panel" data-test="designer-git-panel">
    <GitHeader
      :work-branch="session.workBranch.value"
      :base-branch="session.state.status?.baseBranch ?? null"
      :status-known="session.state.statusLoad === 'ready'"
      :enabled="session.state.status?.enabled === true"
      :status-error="session.state.statusError"
      :loading="session.state.statusLoad === 'loading'"
      :refreshing="session.state.statusLoad === 'loading'"
      :notice="headerNotice"
      @refresh="session.refresh()"
    />
    <GitExceptionList />
    <template v-for="win in session.state.windows" :key="win.id">
      <GitConflictWindow v-if="win.kind === 'conflict'" :win="win" />
      <GitPushErrorWindow v-else :win="win" />
    </template>
    <ConfirmDialog
      :open="!!pendingConfirm"
      :title="pendingConfirm?.title ?? ''"
      :message="pendingConfirm?.message ?? ''"
      :confirm-label="pendingConfirm?.confirmLabel ?? t('gui.git.confirm.ok')"
      :cancel-label="t('gui.git.confirm.cancel')"
      :danger="pendingConfirm?.danger ?? false"
      @confirm="onConfirm"
      @cancel="pendingConfirm = null"
    />
  </section>
</template>

<script setup lang="ts">
// flowgate.v02.0033 — the Designer right Tool Panel `Git` tab content (0033-D §5.1). Public
// part of features/git: a Tool Panel Container mounts
// `<DesignerGitPanel :project-id :flow :active :read-only @badge-change @open-diff
//   @open-confirm-settings @windows-change />` and may call refresh / restoreWindow /
// closeWindow. Server requests and the part's own windows are handled inside; only intents a
// screen outside the part must act on go up (diff tab, confirm settings, taskbar, badge).
//
// The panel owns one Git session for its lifetime; the container keeps it mounted after the
// first show so the badge stays live while the tab is hidden (§3.8).
import { computed, onBeforeUnmount, provide, shallowRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import ConfirmDialog from '../../components/ui/ConfirmDialog.vue'
import './gitPanel.css'
import type { FlowRef } from '@/features/project'
import GitConflictWindow from './GitConflictWindow.vue'
import GitExceptionList from './GitExceptionList.vue'
import GitHeader from './GitHeader.vue'
import GitPushErrorWindow from './GitPushErrorWindow.vue'
import type { GitSource } from './gitSource'
import { createGitSession, GIT_PANEL_CONTEXT, type GitConfirmRequest } from './gitSession'
import type {
  GitBadge,
  GitConfirmSettingsIntent,
  GitDiffIntent,
  GitFileLetter,
  GitFileOrigin,
  GitWindowDescriptor,
} from './types'

const props = withDefaults(
  defineProps<{
    projectId: string | null
    flow: FlowRef | null
    active?: boolean
    readOnly?: boolean
    source?: GitSource
  }>(),
  { active: true, readOnly: false, source: undefined },
)

const emit = defineEmits<{
  'badge-change': [badge: GitBadge | null]
  'open-diff': [intent: GitDiffIntent]
  'open-confirm-settings': [intent: GitConfirmSettingsIntent]
  'windows-change': [windows: GitWindowDescriptor[]]
}>()

const { t } = useI18n()
const session = createGitSession({ source: props.source })
const pendingConfirm = shallowRef<GitConfirmRequest | null>(null)

function openDiff(origin: GitFileOrigin, path: string, status: GitFileLetter | null): void {
  const projectId = session.state.projectId
  if (!projectId) return
  const row = session.pendingRow.value
  const review = origin === 'review'
  emit('open-diff', {
    projectId,
    groupId: origin === 'dirty' ? null : session.state.flow?.groupId ?? null,
    mergeId: review ? row?.mergeId ?? null : null,
    path,
    status,
    origin,
    diffSource: review ? 'review' : 'base',
  })
}

function openSettings(): void {
  const projectId = session.state.projectId
  const hold = session.holdView.value
  if (!projectId || !hold || session.state.readOnly || session.state.forbidden) return
  emit('open-confirm-settings', { projectId, groupId: hold.groupId, acDocId: hold.acDocId })
}

provide(GIT_PANEL_CONTEXT, {
  session,
  openDiff,
  openSettings,
  confirm: (request) => {
    pendingConfirm.value = request
  },
})

function onConfirm(): void {
  const request = pendingConfirm.value
  pendingConfirm.value = null
  request?.run()
}

const headerNotice = computed(() => {
  const notice = session.state.notices.header
  return notice ? t(`gui.git.notice.${notice.code}`, { ...(notice.params ?? {}) }) : null
})

watch(
  () => [props.projectId, props.flow?.groupId ?? null, props.flow?.name ?? null, props.flow?.branch ?? null] as const,
  () => session.setContext(props.projectId, props.flow),
  { immediate: true },
)
watch(
  () => props.readOnly,
  (value) => session.setReadOnly(value),
  { immediate: true },
)
// Coming into view reads again (§4.3); hidden, the part stays mounted and keeps its badge.
watch(
  () => props.active,
  (value, previous) => {
    if (value && previous === false) void session.refresh()
  },
)

const badgeKey = (badge: GitBadge | null) => (badge ? `${badge.tone}:${badge.text}` : '')
watch(
  () => badgeKey(session.badge.value),
  () => emit('badge-change', session.badge.value ? { ...session.badge.value } : null),
)
watch(
  () => session.state.windows,
  (windows) => emit('windows-change', windows.map((w) => ({ ...w }))),
)

onBeforeUnmount(() => session.dispose())

defineExpose({
  refresh: () => session.refresh(),
  restoreWindow: (id: string) => session.restoreWindow(id),
  closeWindow: (id: string) => session.closeWindow(id),
})
</script>

