<template>
  <section class="gx-panel" data-test="designer-git-panel">
    <div v-show="!unlinked" class="gx-head" data-test="git-head">
      <GitHeader
        :work-branch="session.workBranch.value"
        :base-branch="session.state.status?.baseBranch ?? null"
        :status-known="session.state.statusLoad === 'ready'"
        :enabled="session.state.status?.enabled === true"
        :status-error="session.state.statusError"
        :loading="session.state.statusLoad === 'loading'"
        :refreshing="session.state.statusLoad === 'loading'"
        :notice="headerNotice"
        @refresh="refreshAll()"
      />
    </div>
    <GitStatusErrorCard v-if="statusReadFailed" :reason="statusReadReason" @retry="session.refresh()" />
    <GitUnlinkedCard v-else-if="unlinked" :disabled="readOnly" @connect="openConnect" />
    <GitExceptionList v-else />
    <GitWorktreePanel v-if="worktree.gateOpen.value" />
    <template v-for="win in session.state.windows" :key="win.id">
      <GitConflictWindow v-if="win.kind === 'conflict'" :win="win" />
      <GitPushErrorWindow v-else :win="win" />
    </template>
    <GitConnectWindow
      v-model:minimized="connectMinimized"
      :open="connectOpen"
      :initial="connectInitial"
      :busy="connectBusy"
      :test-state="connectTestState"
      :message="connectMessage"
      :has-stored-secret="connectHasStoredSecret"
      :retry-provision="connectRetryProvision"
      :read-only="readOnly"
      @test="onConnectTest"
      @connect="onConnectSubmit"
      @retry-provision="onRetryProvision"
      @cancel="closeConnect"
      @close="closeConnect"
      @input-change="clearConnectFeedback"
    />
    <ConfirmDialog
      :open="!!pendingConfirm"
      :title="pendingConfirm?.title ?? ''"
      :message="pendingConfirm?.message ?? ''"
      :confirm-label="pendingConfirm?.confirmLabel ?? t('gui.git.confirm.ok')"
      :cancel-label="t('gui.git.confirm.cancel')"
      :danger="pendingConfirm?.danger ?? false"
      @confirm="onConfirm"
      @cancel="closeConfirm"
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
//
// flowgate.v02.0066 — beside it the panel owns the Group worktree session (0066 D#2 §2-2): the
// worktree area below the exception cards once git/status confirmed Git on (§3-1), refresh and
// tab activation reading both sessions, the worktree count added to a count badge (§3-8), the
// worktree `open-diff` intent (origin worktree, diffSource group) and `git-center-diff-refresh`.
// A worktree confirm dialog is bound to the project / Flow / generation it was opened in: it
// closes when that context goes and its action runs only while the context is still current.
// T#7: the `Diff` pane rows raise the same intent with the target as compareBase, and a merge
// dialog also closes when the merge target changes (0066 D#2 §3-2-2 ⑤ ⑦).
import { computed, onBeforeUnmount, provide, reactive, ref, shallowRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import ConfirmDialog from '../../components/ui/ConfirmDialog.vue'
import './gitPanel.css'
import type { FlowRef } from '@/features/project'
import GitConflictWindow from './GitConflictWindow.vue'
import GitConnectWindow from './GitConnectWindow.vue'
import GitExceptionList from './GitExceptionList.vue'
import GitHeader from './GitHeader.vue'
import GitPushErrorWindow from './GitPushErrorWindow.vue'
import GitStatusErrorCard from './GitStatusErrorCard.vue'
import GitUnlinkedCard from './GitUnlinkedCard.vue'
import GitWorktreePanel from './GitWorktreePanel.vue'
import { gitErrorOf } from './api'
import { createHttpGitSource, type GitSource } from './gitSource'
import { createGitSession, GIT_PANEL_CONTEXT, type GitConfirmRequest } from './gitSession'
import { createGitWorktreeSession, GIT_WORKTREE_CONTEXT, type WorktreeTicket } from './gitWorktreeSession'
import type {
  GitBadge,
  GitCenterDiffRefresh,
  GitConfigSave,
  GitConfirmSettingsIntent,
  GitConnectBusy,
  GitConnectForm,
  GitConnectInitial,
  GitConnectMessage,
  GitConnectProvider,
  GitConnectTestState,
  GitConnectionTest,
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
  'git-center-diff-refresh': [request: GitCenterDiffRefresh]
}>()

const { t } = useI18n()
const session = createGitSession({ source: props.source })
const worktree = createGitWorktreeSession({
  git: session,
  source: props.source,
  onCenterDiffRefresh: (request) => emit('git-center-diff-refresh', { ...request }),
})
const pendingConfirm = shallowRef<GitConfirmRequest | null>(null)
// The worktree context of the open dialog; null for a 0033 dialog.
const confirmTicket = shallowRef<WorktreeTicket | null>(null)
// What else must hold for the open worktree dialog (e.g. the merge target it was opened for).
const confirmValid = shallowRef<(() => boolean) | null>(null)

function askConfirm(request: GitConfirmRequest, ticket: WorktreeTicket | null, stillValid?: () => boolean): void {
  pendingConfirm.value = request
  confirmTicket.value = ticket
  confirmValid.value = ticket && stillValid ? stillValid : null
}

function closeConfirm(): void {
  pendingConfirm.value = null
  confirmTicket.value = null
  confirmValid.value = null
}

// A worktree dialog lives only while its context (and its own condition) still holds.
const confirmStale = (): boolean => {
  const ticket = confirmTicket.value
  if (!ticket) return false
  return !worktree.isCurrent(ticket) || (!!confirmValid.value && !confirmValid.value())
}

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

const WORKTREE_LETTERS: readonly GitFileLetter[] = ['A', 'D', 'M', '?']

// A worktree row: the group work base floor against the working file (0066 D#2 §5-2). The
// letters T / U have no diff letter and go up as null.
function openFileDiff(path: string, letter: string): void {
  const projectId = session.state.projectId
  const groupId = session.state.flow?.groupId ?? null
  if (!projectId || !groupId) return
  emit('open-diff', {
    projectId,
    groupId,
    mergeId: null,
    path,
    status: WORKTREE_LETTERS.find((l) => l === letter) ?? null,
    origin: 'worktree',
    diffSource: 'group',
    compareBase: null,
  })
}

// A row of the `Diff` pane: the merge-base with the target its list was read for (§5-2, ⑦).
// `/changes` letters other than M / A / D / ? go up as null.
function openTargetDiff(path: string, letter: string, target: string): void {
  const projectId = session.state.projectId
  const groupId = session.state.flow?.groupId ?? null
  if (!projectId || !groupId || !target) return
  emit('open-diff', {
    projectId,
    groupId,
    mergeId: null,
    path,
    status: WORKTREE_LETTERS.find((l) => l === letter) ?? null,
    origin: 'worktree',
    diffSource: 'group',
    compareBase: target,
  })
}

provide(GIT_WORKTREE_CONTEXT, { worktree, openFileDiff, openTargetDiff, confirm: askConfirm })

// Header refresh, tab activation and the exposed refresh read both sessions (§3-2-1); the
// worktree reads nothing while its gate is closed.
function refreshAll(): Promise<void> {
  worktree.refresh()
  return session.refresh()
}

provide(GIT_PANEL_CONTEXT, {
  session,
  openDiff,
  openSettings,
  confirm: (request) => askConfirm(request, null),
})

function onConfirm(): void {
  const request = pendingConfirm.value
  const stale = confirmStale()
  closeConfirm()
  // A worktree dialog never acts on a context other than the one it was opened in.
  if (stale) return
  request?.run()
}

// Flow / project switch, gate closed, dispose or its own condition gone (a merge dialog after a
// target change): the worktree dialog closes and sends nothing.
watch(confirmStale, (stale) => {
  if (stale) closeConfirm()
})

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
    if (value && previous === false) void refreshAll()
  },
)

// flowgate.v02.0065 — Git 미연동 / read failure / Git 연결 (0065-R §3.1, §3.2). One branch shows
// either the read-failure card, the "미연동" card (a status the server answered with Git off) or
// the 0033 exception area — a failed re-read never leaves an earlier exception card (and its
// actions) up beside it. "미연동" also hides the header, as v12 does; it stays in the DOM.
// The connect window's state is the panel's own, not the session's window list:
// `windows-change` keeps reporting the 0033 windows only.
//
// A re-read the network failed keeps the last answer and only notes it on the header (0033
// §4.5). Over a connected project that is right; over "Git off" it would leave 미연동 up with
// the header (and its note) hidden — even after a connect turned Git on. There it is a read
// failure: the error card with retry, until a read answers.
const staleUnlinked = computed(
  () =>
    session.state.statusLoad === 'ready' &&
    session.state.status?.enabled === false &&
    session.state.notices.header?.code === 'network',
)
const statusReadFailed = computed(() => session.state.statusLoad === 'error' || staleUnlinked.value)
const statusReadReason = computed(() => (staleUnlinked.value ? 'network' : session.state.statusError))
const unlinked = computed(
  () => !staleUnlinked.value && session.state.statusLoad === 'ready' && session.state.status?.enabled === false,
)
const connectOpen = ref(false)
const connectMinimized = ref(false)
const connectInitial = shallowRef<GitConnectInitial | null>(null)
const connectHasStoredSecret = ref(false)
const connectTestState = shallowRef<GitConnectTestState | null>(null)
const connectMessage = shallowRef<GitConnectMessage | null>(null)
// Settings saved but the Base checkout not prepared: the window offers `저장소 준비 다시 시도`.
const connectRetryProvision = ref(false)

// `Git 연결` runs the existing project Git settings contracts (v0.1 GitSettingsView) through the
// same GitSource as the session — a test hands in its own. Order: test-connection → settings save
// with Git on → provision → session refresh (0065-R §3.3). The token typed in the window goes
// only into those request bodies: it is never kept in this state, an emit or a message.
let connectSource: GitSource | null = props.source ?? null
const connectSrc = (): GitSource => (connectSource ??= createHttpGitSource())

// One request at a time. Opening / closing the window, another project or unmount starts a new
// generation: a late answer of an older one changes nothing in the window.
let connectGen = 0
let connectDisposed = false
const connectCurrent = (gen: number) => gen === connectGen && !connectDisposed

// The generation cannot take back a save or provision already sent, so the request itself is
// held per project until it has run its course — past closing and reopening the window. Until
// then that project's window shows it running and starts nothing beside it.
const connectRunning = reactive(new Map<string, GitConnectBusy>())
const connectSettled = new Map<string, Promise<void>>()
const connectBusy = computed<GitConnectBusy>(() => (props.projectId && connectRunning.get(props.projectId)) || 'idle')

function holdConnect(projectId: string, busy: GitConnectBusy, task: () => Promise<void>): void {
  connectRunning.set(projectId, busy)
  const settled = task()
    .catch(() => undefined)
    .finally(() => {
      connectRunning.delete(projectId)
      connectSettled.delete(projectId)
    })
  connectSettled.set(projectId, settled)
}

const CONNECT_PROVIDERS: readonly GitConnectProvider[] = ['generic', 'github', 'gitlab', 'gitea', 'gitbucket']
const asProvider = (value: string): GitConnectProvider => CONNECT_PROVIDERS.find((p) => p === value) ?? 'generic'

const toSave = (form: GitConnectForm): GitConfigSave => ({
  repoUrl: form.repoUrl,
  provider: form.provider,
  username: form.username || null,
  // Blank = keep the stored token (never "", which would clear it).
  secret: form.secret || null,
  baseBranch: form.baseBranch,
})

// test-connection answered: both reachable and authenticated, or which of them failed.
function testFailureKey(result: GitConnectionTest): string | null {
  if (result.reachable === true && result.authenticated === true) return null
  if (result.failureCode === 'auth_failed' || result.authenticated === false) return 'gui.git.connect.result.auth_failed'
  if (result.failureCode === 'unreachable' || result.reachable === false) return 'gui.git.connect.result.unreachable'
  return 'gui.git.connect.result.test_failed'
}

// A request that failed outright. Permission, input check and network have their own words;
// the server's text is never shown (it can carry the URL with credentials).
function requestFailureKey(error: unknown, fallback: string): string {
  const err = gitErrorOf(error)
  if (err.status === 403 || err.code === 'forbidden') return 'gui.git.connect.result.forbidden'
  if (err.status === 422 || err.code === 'invalid_request') return 'gui.git.connect.result.invalid_request'
  if (err.network) return 'gui.git.connect.result.network'
  return fallback
}

function clearConnectFeedback(): void {
  connectTestState.value = null
  connectMessage.value = null
  connectRetryProvision.value = false
}

function openConnect(): void {
  const projectId = props.projectId
  if (props.readOnly || !projectId) return
  // Already open: brought back as it is — what was typed, a request running, its outcome.
  if (connectOpen.value) {
    connectMinimized.value = false
    return
  }
  const gen = ++connectGen
  clearConnectFeedback()
  connectInitial.value = null
  connectHasStoredSecret.value = false
  connectMinimized.value = false
  connectOpen.value = true
  void loadConnectConfig(projectId, gen)
}

// Settings that exist with Git off open the window filled in (never the token: only whether
// one is stored). A failed read leaves the form empty with a note; 미연동 stays as it is.
// A request of a closed window still running is waited for, so the form shows what it left.
async function loadConnectConfig(projectId: string, gen: number): Promise<void> {
  await connectSettled.get(projectId)
  if (!connectCurrent(gen)) return
  try {
    const config = await connectSrc().fetchGitConfig(projectId)
    if (!connectCurrent(gen) || !config.configured) return
    connectInitial.value = {
      repoUrl: config.repoUrl,
      provider: asProvider(config.provider),
      username: config.username ?? '',
      baseBranch: config.baseBranch,
    }
    connectHasStoredSecret.value = config.hasSecret
  } catch {
    if (!connectCurrent(gen)) return
    connectMessage.value = { tone: 'error', messageKey: 'gui.git.connect.result.config_failed' }
  }
}

function closeConnect(): void {
  connectGen += 1
  connectOpen.value = false
  connectMinimized.value = false
  clearConnectFeedback()
}

// The window let go of a request that may already have changed the settings on the server:
// the tab still reads them again (same project only).
function refreshAfterDrop(projectId: string): void {
  if (!connectDisposed && props.projectId === projectId) void session.refresh()
}

function stopConnect(message: GitConnectMessage): void {
  connectMessage.value = message
}

function onConnectTest(form: GitConnectForm): void {
  const projectId = props.projectId
  if (!projectId || props.readOnly || connectRunning.has(projectId)) return
  const gen = ++connectGen
  clearConnectFeedback()
  holdConnect(projectId, 'testing', () => runTest(projectId, gen, form))
}

async function runTest(projectId: string, gen: number, form: GitConnectForm): Promise<void> {
  let state: GitConnectTestState
  try {
    const key = testFailureKey(await connectSrc().testGitConnection(projectId, toSave(form)))
    state = key ? { ok: false, messageKey: key } : { ok: true, messageKey: 'gui.git.connect.result.test_ok' }
  } catch (error) {
    state = { ok: false, messageKey: requestFailureKey(error, 'gui.git.connect.result.test_failed') }
  }
  if (!connectCurrent(gen)) return
  connectTestState.value = state
}

function onConnectSubmit(form: GitConnectForm): void {
  const projectId = props.projectId
  if (!projectId || props.readOnly || connectRunning.has(projectId)) return
  const gen = ++connectGen
  clearConnectFeedback()
  holdConnect(projectId, 'connecting', () => runConnect(projectId, gen, toSave(form)))
}

async function runConnect(projectId: string, gen: number, input: GitConfigSave): Promise<void> {

  // 1. The same test as `연결 테스트`: nothing is saved unless the remote answers and accepts.
  try {
    const key = testFailureKey(await connectSrc().testGitConnection(projectId, input))
    if (!connectCurrent(gen)) return
    if (key) return stopConnect({ tone: 'error', messageKey: key })
  } catch (error) {
    if (!connectCurrent(gen)) return
    return stopConnect({ tone: 'error', messageKey: requestFailureKey(error, 'gui.git.connect.result.test_failed') })
  }

  // 2. Save with Git on — `연결` is the Git 사용 switch (0065-R §3.3).
  try {
    await connectSrc().saveGitConfig(projectId, input)
  } catch (error) {
    if (!connectCurrent(gen)) return refreshAfterDrop(projectId)
    return stopConnect({ tone: 'error', messageKey: requestFailureKey(error, 'gui.git.connect.result.save_failed') })
  }
  if (!connectCurrent(gen)) return refreshAfterDrop(projectId)

  // 3. Prepare the Base checkout.
  await prepareBase(projectId, gen)
}

function onRetryProvision(): void {
  const projectId = props.projectId
  if (!projectId || props.readOnly || connectRunning.has(projectId) || !connectRetryProvision.value) return
  const gen = ++connectGen
  clearConnectFeedback()
  holdConnect(projectId, 'provisioning', () => prepareBase(projectId, gen))
}

// POST git/provision is decided on `result.status`: only `ok` (mode none = already there
// included) is done. `failed` (an HTTP 200), git_busy and a failed request leave the settings
// saved but the connection not complete: the window says so and offers the preparation again,
// and the provision state and the tab are read again so the screen follows the server.
async function prepareBase(projectId: string, gen: number): Promise<void> {
  let outcome: 'ok' | 'busy' | 'failed'
  let failureKey = 'gui.git.connect.result.provision_failed'
  try {
    const result = await connectSrc().provisionGit(projectId)
    outcome = result.status === 'ok' ? 'ok' : result.busy ? 'busy' : 'failed'
  } catch (error) {
    const err = gitErrorOf(error)
    outcome = err.code === 'git_busy' ? 'busy' : 'failed'
    if (err.status === 403 || err.code === 'forbidden') failureKey = 'gui.git.connect.result.provision_forbidden'
  }
  if (!connectCurrent(gen)) return refreshAfterDrop(projectId)

  if (outcome === 'ok') {
    // Done: the window closes (dropping the token) and the tab shows what the server now says.
    closeConnect()
    await session.refresh()
    return
  }

  const [provision] = await Promise.all([
    connectSrc()
      .fetchGitProvision(projectId)
      .catch(() => null),
    session.refresh(),
  ])
  if (!connectCurrent(gen)) return
  stopConnect({ tone: 'error', messageKey: outcome === 'busy' ? 'gui.git.connect.result.provision_busy' : failureKey })
  // Preparing again needs the saved settings with Git on; an unknown state still offers it.
  connectRetryProvision.value = provision?.enabled !== false
}

watch(
  () => props.projectId,
  () => {
    closeConnect()
    connectInitial.value = null
    connectHasStoredSecret.value = false
  },
)

// 0033 rule (danger > progress > count, one only); the worktree's changed paths join a count
// badge only, and only while the worktree area is shown (0066 D#2 §3-8).
const badge = computed<GitBadge | null>(() => {
  const base = session.badge.value
  const changed = worktree.changedCount.value ?? 0
  if (!changed || (base && base.tone !== 'count')) return base
  if (!base) return { tone: 'count', text: String(changed) }
  const lowerBound = base.text.endsWith('+')
  return { tone: 'count', text: `${(Number.parseInt(base.text, 10) || 0) + changed}${lowerBound ? '+' : ''}` }
})
const badgeKey = (value: GitBadge | null) => (value ? `${value.tone}:${value.text}` : '')
watch(
  () => badgeKey(badge.value),
  () => emit('badge-change', badge.value ? { ...badge.value } : null),
)
watch(
  () => session.state.windows,
  (windows) => emit('windows-change', windows.map((w) => ({ ...w }))),
)

onBeforeUnmount(() => {
  connectDisposed = true
  if (confirmTicket.value) closeConfirm()
  worktree.dispose()
  session.dispose()
})

defineExpose({
  refresh: () => refreshAll(),
  restoreWindow: (id: string) => session.restoreWindow(id),
  closeWindow: (id: string) => session.closeWindow(id),
})
</script>

