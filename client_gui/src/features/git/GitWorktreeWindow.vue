<template>
  <ModelessShell
    ref="shellRef"
    :open="open"
    :minimized="minimized"
    :initial-x="initial.x"
    :initial-y="initial.y"
    :initial-width="initial.width"
    :initial-height="initial.height"
    :min-width="WINDOW_MIN_WIDTH"
    :min-height="WINDOW_MIN_HEIGHT"
    @update:minimized="(value: boolean) => emit('update:minimized', value)"
    @close="emit('close')"
  >
    <template #header>
      <span class="gw-win-title" data-test="git-window-title">
        <span>{{ t('gui.git.worktree.nav.window') }}</span>
        <span v-if="branch" class="gw-win-sub" :title="branch">{{ branch }}</span>
      </span>
    </template>
    <div class="gw-win" data-test="git-window" :data-tab="tab">
      <div class="gw-win-tabs" role="tablist" :aria-label="t('gui.git.worktree.nav.aria')" @keydown="onTabKey">
        <button
          v-for="name in TABS"
          :id="`${uid}-tab-${name}`"
          :key="name"
          type="button"
          role="tab"
          class="gw-win-tab"
          :class="{ 'is-active': tab === name }"
          data-test="git-window-tab"
          :data-tab="name"
          :aria-selected="tab === name ? 'true' : 'false'"
          :aria-controls="`${uid}-panel-${name}`"
          :tabindex="tab === name ? 0 : -1"
          @click="emit('update:tab', name)"
        >{{ t(`gui.git.worktree.nav.${name}`) }}</button>
      </div>
      <!-- Every tab stays mounted while the window is open, so a switch keeps its scroll, input
           and selection (0067-R §3-4); only the active one is shown. -->
      <div
        v-for="name in TABS"
        v-show="tab === name"
        :id="`${uid}-panel-${name}`"
        :key="name"
        class="gw-win-body"
        role="tabpanel"
        data-test="git-window-panel"
        :data-tab="name"
        :aria-labelledby="`${uid}-tab-${name}`"
      >
        <template v-if="name === 'files'">
          <template v-if="workspace">
            <GitChangesAccordion />
            <GitCommitBar />
          </template>
          <p v-else class="gw-line is-muted" data-test="git-window-unavailable">{{ t('gui.git.worktree.unavailable') }}</p>
        </template>
        <GitApplyAccordion v-else-if="name === 'merge'" />
        <template v-else-if="name === 'diff'">
          <GitApplyInfo v-if="workspace" />
          <p v-else class="gw-line is-muted" data-test="git-window-unavailable">{{ t('gui.git.worktree.unavailable') }}</p>
        </template>
        <GitWorktreeStatus v-else />
      </div>
    </div>
  </ModelessShell>
</template>

<script setup lang="ts">
// flowgate.v02.0067 T#1 — the one Git modeless of the Git tab (0067-R §3, v27 `gw-detail-window`):
// the four entries open it on their tab and its top tabs switch between `파일 관리` / `Base 반영` /
// `Diff` / `상태`. One width, height, header and padding for all four; only a long body scrolls.
// It is the Designer ModelessShell (non-blocking, its open / close / minimize and core/overlay
// z-index), owned by the panel as GitConnectWindow is — it never opens a Center canvas tab.
//
// The tab bodies are the 0066 parts: `파일 관리` is the changed-files list and the commit line at
// its bottom (T#2); the Base apply accordion and the target comparison list stay as they are until
// T#3–T#4 finish them. Every value and action is the worktree session's; the window holds no Git
// state of its own.
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import ModelessShell from '../../components/ui/ModelessShell.vue'
import GitApplyAccordion from './GitApplyAccordion.vue'
import GitApplyInfo from './GitApplyInfo.vue'
import GitChangesAccordion from './GitChangesAccordion.vue'
import GitCommitBar from './GitCommitBar.vue'
import GitWorktreeStatus from './GitWorktreeStatus.vue'
import { useGitWorktree } from './gitWorktreeSession'
import type { GitWorktreeTab } from './types'

const props = defineProps<{
  open: boolean
  tab: GitWorktreeTab
  minimized: boolean
}>()
const emit = defineEmits<{
  'update:tab': [tab: GitWorktreeTab]
  'update:minimized': [value: boolean]
  close: []
}>()

const { t } = useI18n()
const { worktree: wt } = useGitWorktree()

const TABS: readonly GitWorktreeTab[] = ['files', 'merge', 'diff', 'status']

// v27: 700 × 560, at the right of the canvas below the top bar. Like v27 (`gw-detail-window` never
// larger than the space it has) and GitConnectWindow, a smaller viewport gets a smaller window, so
// the titlebar controls, the tabs and the body stay on screen; only the body scrolls inside.
const WINDOW_WIDTH = 700
const WINDOW_HEIGHT = 560
const WINDOW_MIN_WIDTH = 420
const WINDOW_MIN_HEIGHT = 300
const WINDOW_TOP = 88
const WINDOW_RIGHT_GAP = 380
const WINDOW_EDGE = 8

interface Geometry {
  x: number
  y: number
  width: number
  height: number
}

const clamp = (value: number, min: number, max: number) => Math.min(Math.max(value, min), Math.max(min, max))

// Where the window opens: the v27 size and place on a normal screen, as much of it as fits on a
// small one (moved up before it gets shorter).
function defaultGeometry(): Geometry {
  if (typeof window === 'undefined') return { x: 120, y: WINDOW_TOP, width: WINDOW_WIDTH, height: WINDOW_HEIGHT }
  const vw = window.innerWidth
  const vh = window.innerHeight
  const width = Math.min(WINDOW_WIDTH, Math.max(0, vw - 2 * WINDOW_EDGE))
  const y = Math.min(WINDOW_TOP, Math.max(WINDOW_EDGE, vh - WINDOW_HEIGHT - WINDOW_EDGE))
  const height = Math.min(WINDOW_HEIGHT, Math.max(0, vh - y - WINDOW_EDGE))
  return { x: Math.max(WINDOW_EDGE, vw - width - WINDOW_RIGHT_GAP), y, width, height }
}

// The same place and size, made smaller and moved only as far as the viewport needs.
function fitted(g: Geometry): Geometry {
  const vw = window.innerWidth
  const vh = window.innerHeight
  const width = Math.min(g.width, Math.max(0, vw - 2 * WINDOW_EDGE))
  const height = Math.min(g.height, Math.max(0, vh - 2 * WINDOW_EDGE))
  return {
    x: clamp(g.x, WINDOW_EDGE, vw - width - WINDOW_EDGE),
    y: clamp(g.y, WINDOW_EDGE, vh - height - WINDOW_EDGE),
    width,
    height,
  }
}

const initial = defaultGeometry()
const shellRef = ref<InstanceType<typeof ModelessShell> | null>(null)

function fitViewport(): void {
  const shell = shellRef.value
  if (!shell) return
  const now = shell.geometry()
  const next = fitted(now)
  if (next.x !== now.x || next.y !== now.y || next.width !== now.width || next.height !== now.height) shell.setGeometry(next)
}

// Asked for again (an entry while it is open, or restored), it comes in front of the other
// modeless windows; the shell does this by itself only when it opens or is pressed.
function bringToFront(): void {
  shellRef.value?.bringToFront()
}

const uid = `gw-win-${Math.random().toString(36).slice(2, 8)}`
const workspace = computed(() => wt.state.status?.worktreeAvailable === true)
const branch = computed(() => wt.state.status?.branch ?? null)

// The `Diff` tab is the session's `Diff` pane: showing it reads the chosen target's list (and keeps
// it read on refresh / target change); closing the window lets it go.
watch(
  () => [props.open, props.tab] as const,
  ([open, tab]) => {
    if (open && tab === 'diff' && wt.state.infoPane !== 'diff') wt.toggleInfo('diff')
  },
  { immediate: true },
)

// Opening again reads the worktree again (E1 / E8 / E10, and the list when it opens on `Diff`):
// what changed while it was closed — outside the 0033 pending row — shows up, and so do the
// merge judgment and the follow-up Push. It is the session's refresh, so a read asked during a
// write still waits for its answer; switching tabs reads nothing. It opens at its v27 place,
// fitted to the viewport, and keeps fitting while it is open.
watch(
  () => props.open,
  (open, wasOpen) => {
    if (open && !wasOpen) {
      shellRef.value?.setGeometry(defaultGeometry())
      wt.refresh()
      window.addEventListener('resize', fitViewport)
    } else if (!open && wasOpen) {
      window.removeEventListener('resize', fitViewport)
      if (wt.state.infoPane) wt.toggleInfo(wt.state.infoPane)
    }
  },
)
onBeforeUnmount(() => window.removeEventListener('resize', fitViewport))

// Restored from its titlebar: fitted to the viewport as it is now, and in front.
watch(
  () => props.minimized,
  (minimized, wasMinimized) => {
    if (!props.open || minimized || !wasMinimized) return
    fitViewport()
    bringToFront()
  },
)

defineExpose({ bringToFront })

// ← / → move between the tabs (WAI-ARIA tabs).
function onTabKey(event: KeyboardEvent): void {
  if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
  const step = event.key === 'ArrowRight' ? 1 : -1
  const next = TABS[(TABS.indexOf(props.tab) + step + TABS.length) % TABS.length]
  emit('update:tab', next)
  const target = (event.currentTarget as HTMLElement).querySelector<HTMLElement>(`[data-tab="${next}"]`)
  target?.focus()
}
</script>
