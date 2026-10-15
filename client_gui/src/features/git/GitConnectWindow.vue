<template>
  <ModelessShell
    :key="placement.seq"
    :open="open"
    :title="t('gui.git.connect.title')"
    :minimized="isMinimized"
    :initial-x="placement.x"
    :initial-y="placement.y"
    :initial-width="placement.width"
    :initial-height="placement.height"
    @update:minimized="setMinimized"
    @close="onClose"
  >
    <template #header>
      <button
        v-if="isMinimized"
        type="button"
        class="gxc-restore"
        data-test="git-connect-restore"
        @mousedown.stop
        @click="setMinimized(false)"
      >
        <span aria-hidden="true">⑂</span> {{ t('gui.git.connect.restore') }}
      </button>
      <span v-else class="gxc-title" data-test="git-connect-title">
        <span aria-hidden="true">⑂</span> {{ t('gui.git.connect.title') }}
      </span>
    </template>
    <div ref="bodyRef" class="gxc-body" data-test="git-connect-window">
      <div class="gxc-details">
        <div class="gxc-row">
          <label :for="`${uid}-remote`">{{ t('gui.git.connect.remote') }}</label>
          <input
            :id="`${uid}-remote`"
            v-model="form.repoUrl"
            type="text"
            :placeholder="t('gui.git.connect.remote_placeholder')"
            spellcheck="false"
            autocomplete="off"
            :disabled="locked"
            data-test="git-connect-remote"
            @input="onInput"
          />
        </div>
        <div class="gxc-row">
          <label :for="`${uid}-provider`">{{ t('gui.git.connect.provider') }}</label>
          <select
            :id="`${uid}-provider`"
            v-model="form.provider"
            :disabled="locked"
            data-test="git-connect-provider"
            @change="onInput"
          >
            <option v-for="p in PROVIDERS" :key="p.value" :value="p.value">{{ p.label }}</option>
          </select>
        </div>
        <div class="gxc-row">
          <label :for="`${uid}-username`">{{ t('gui.git.connect.username') }}</label>
          <input
            :id="`${uid}-username`"
            v-model="form.username"
            type="text"
            autocomplete="off"
            :disabled="locked"
            data-test="git-connect-username"
            @input="onInput"
          />
        </div>
        <div class="gxc-row">
          <label :for="`${uid}-secret`">{{ t('gui.git.connect.secret') }}</label>
          <input
            :id="`${uid}-secret`"
            v-model="form.secret"
            type="password"
            autocomplete="new-password"
            :placeholder="hasStoredSecret ? t('gui.git.connect.secret_stored') : ''"
            :disabled="locked"
            data-test="git-connect-secret"
            @input="onInput"
          />
        </div>
        <div class="gxc-row">
          <label :for="`${uid}-branch`">{{ t('gui.git.connect.branch') }}</label>
          <input
            :id="`${uid}-branch`"
            v-model="form.baseBranch"
            type="text"
            spellcheck="false"
            :disabled="locked"
            data-test="git-connect-branch"
            @input="onInput"
          />
        </div>
      </div>
      <div
        class="gxc-message"
        :class="[`is-${messageTone}`, { 'is-empty': !messageText }]"
        aria-live="polite"
        data-test="git-connect-message"
      >{{ messageText }}</div>
      <div class="gxc-footer">
        <div class="gxc-footer-test">
          <button type="button" class="gxc-foot-btn" :disabled="locked" data-test="git-connect-test" @click="onTest">
            {{ busy === 'testing' ? t('gui.git.connect.testing') : t('gui.git.connect.test') }}
          </button>
          <span
            class="gxc-test-status"
            :class="testText ? `is-${testTone}` : null"
            aria-live="polite"
            data-test="git-connect-test-status"
          >{{ testText }}</span>
        </div>
        <button type="button" class="gxc-foot-btn" :disabled="busy !== 'idle'" data-test="git-connect-cancel" @click="onCancel">
          {{ t('gui.git.connect.cancel') }}
        </button>
        <button type="button" class="gxc-foot-btn primary" :disabled="locked" data-test="git-connect-submit" @click="onConnect">
          {{ busy === 'connecting' ? t('gui.git.connect.submitting') : t('gui.git.connect.submit') }}
        </button>
      </div>
    </div>
  </ModelessShell>
</template>

<script lang="ts">
// Per-instance id prefix for the label/input pairs.
let connectWindowSeq = 0
</script>

<script setup lang="ts">
// flowgate.v02.0065 — Git 연결 modeless (0065-R §3.2, v12 `#gitConnectWindow`) on the shared
// ModelessShell, like the 0033 windows. The fields, their order and the footer follow the
// mockup; nothing else (no extra settings, toggle or description).
//
// UI only: the window checks what it can on its own (empty URL / Base branch, URL form) and
// hands a valid form up as `test` / `connect`. Whoever mounts it runs the requests and feeds
// the result back through `busy` / `testState` / `message`. The token typed here leaves only in
// those two payloads; it is cleared on cancel / close and whenever the window opens or closes,
// and it is never put in a title, a data attribute or a log.
import { computed, nextTick, onBeforeUnmount, reactive, ref, shallowRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import ModelessShell from '../../components/ui/ModelessShell.vue'
import type {
  GitConnectBusy,
  GitConnectForm,
  GitConnectInitial,
  GitConnectMessage,
  GitConnectProvider,
  GitConnectTestState,
} from './types'

const props = withDefaults(
  defineProps<{
    open: boolean
    // Optional `v-model:minimized`; left unset, the window keeps it to itself.
    minimized?: boolean
    // Values the window opens with — never the token.
    initial?: GitConnectInitial | null
    busy?: GitConnectBusy
    testState?: GitConnectTestState | null
    message?: GitConnectMessage | null
    // A token is already stored: the token field says so in its placeholder only.
    hasStoredSecret?: boolean
    readOnly?: boolean
  }>(),
  {
    minimized: undefined,
    initial: null,
    busy: 'idle',
    testState: null,
    message: null,
    hasStoredSecret: false,
    readOnly: false,
  },
)

const emit = defineEmits<{
  test: [form: GitConnectForm]
  connect: [form: GitConnectForm]
  cancel: []
  close: []
  'update:minimized': [value: boolean]
  'input-change': []
}>()

const { t } = useI18n()

const WIDTH = 500
const HEIGHT = 360
const MIN_HEIGHT = 160
// v12 `#gitConnectWindow`: 30px off the right edge and centred a little above the middle; at
// 800px and below 15px from the top and 10px off the right. Never wider than the viewport less
// those margins, so the titlebar controls and the footer stay on screen on a narrow window.
const NARROW = 800
const DEFAULT_BRANCH = 'main'
// The mockup's URL rule: http(s)://, ssh:// or the scp-like user@host:path form.
const REMOTE_URL = /^(https?:\/\/|ssh:\/\/|[^\s@]+@[^\s:]+:)/
const PROVIDERS: ReadonlyArray<{ value: GitConnectProvider; label: string }> = [
  { value: 'generic', label: 'Generic Git' },
  { value: 'github', label: 'GitHub' },
  { value: 'gitlab', label: 'GitLab' },
  { value: 'gitea', label: 'Gitea' },
  { value: 'gitbucket', label: 'GitBucket' },
]

const uid = `gxc-${++connectWindowSeq}`

interface Placement {
  seq: number
  x: number
  y: number
  width: number
  height: number
}

let placementSeq = 0
// ModelessShell reads its geometry once, when it is created; a new `seq` keys a new shell.
function fitViewport(): Placement {
  const vw = window.innerWidth
  const vh = window.innerHeight
  const narrow = vw <= NARROW
  const width = Math.max(0, Math.min(WIDTH, vw - (narrow ? 20 : 30)))
  const y = narrow ? 15 : Math.max(15, Math.round(vh / 2 - 220))
  return {
    seq: ++placementSeq,
    x: Math.max(0, vw - width - (narrow ? 10 : 30)),
    y,
    width,
    height: Math.max(MIN_HEIGHT, Math.min(HEIGHT, vh - y - 15)),
  }
}

const placement = shallowRef<Placement>(fitViewport())
const bodyRef = ref<HTMLElement | null>(null)

function openingForm(): GitConnectForm {
  const init = props.initial
  return {
    repoUrl: init?.repoUrl ?? '',
    provider: init?.provider ?? 'generic',
    username: init?.username ?? '',
    secret: '',
    baseBranch: init?.baseBranch || DEFAULT_BRANCH,
  }
}

const form = reactive<GitConnectForm>(openingForm())
// i18n key of the window's own input check; shown before any `message` from outside.
const localError = ref<string | null>(null)
const edited = ref(false)
const localMinimized = ref(false)

const isMinimized = computed(() => props.minimized ?? localMinimized.value)
const locked = computed(() => props.busy !== 'idle' || props.readOnly)

function resetForm(): void {
  Object.assign(form, openingForm())
  localError.value = null
  edited.value = false
}

// Opening starts from `initial` and fits the current viewport; closing drops the token and
// everything typed.
watch(
  () => props.open,
  (open) => {
    localMinimized.value = false
    resetForm()
    if (open) placement.value = fitViewport()
  },
)

// The viewport got smaller than the open window (ModelessShell only keeps part of the titlebar
// on screen): fit it again, keeping what was typed, the minimized state and the focused field.
// A minimized window is only its titlebar, so it is checked again at its full size once it is
// restored.
function fitIfOverflowing(): void {
  const panel = bodyRef.value?.closest('.ui-modeless-panel')
  if (!panel) return
  const rect = panel.getBoundingClientRect()
  if (rect.right <= window.innerWidth && rect.bottom <= window.innerHeight) return
  const active = document.activeElement
  const focused = active instanceof HTMLElement && panel.contains(active) ? active.dataset.test ?? null : null
  placement.value = fitViewport()
  if (focused) {
    void nextTick(() =>
      bodyRef.value?.closest('.ui-modeless-panel')?.querySelector<HTMLElement>(`[data-test="${focused}"]`)?.focus(),
    )
  }
}

watch(
  () => props.open,
  (open) => {
    if (open) window.addEventListener('resize', fitIfOverflowing)
    else window.removeEventListener('resize', fitIfOverflowing)
  },
  { immediate: true },
)
onBeforeUnmount(() => window.removeEventListener('resize', fitIfOverflowing))

// Restored: measured after the shell has laid out its full height again.
watch(
  isMinimized,
  (minimized) => {
    if (props.open && !minimized) fitIfOverflowing()
  },
  { flush: 'post' },
)

// `initial` may arrive after the window opened; it never overwrites what the user typed.
watch(
  () => props.initial,
  () => {
    if (props.open && !edited.value) resetForm()
  },
)

const messageText = computed(() => {
  if (localError.value) return t(localError.value)
  const message = props.message
  return message ? t(message.messageKey, { ...(message.params ?? {}) }) : ''
})
const messageTone = computed(() => (localError.value ? 'error' : props.message?.tone ?? 'error'))

const testText = computed(() => {
  const state = props.testState
  if (localError.value || !state) return ''
  return t(state.messageKey, { ...(state.params ?? {}) })
})
const testTone = computed(() => (props.testState?.ok ? 'success' : 'error'))

function onInput(): void {
  edited.value = true
  localError.value = null
  emit('input-change')
}

function check(): string | null {
  const url = form.repoUrl.trim()
  const branch = form.baseBranch.trim()
  if (!url || !branch) return 'gui.git.connect.required'
  if (!REMOTE_URL.test(url)) return 'gui.git.connect.invalid_url'
  return null
}

function payload(): GitConnectForm | null {
  if (locked.value) return null
  localError.value = check()
  if (localError.value) return null
  return {
    repoUrl: form.repoUrl.trim(),
    provider: form.provider,
    username: form.username.trim(),
    secret: form.secret,
    baseBranch: form.baseBranch.trim(),
  }
}

function onTest(): void {
  const value = payload()
  if (value) emit('test', value)
}

function onConnect(): void {
  const value = payload()
  if (value) emit('connect', value)
}

function onCancel(): void {
  resetForm()
  emit('cancel')
}

function onClose(): void {
  resetForm()
  emit('close')
}

function setMinimized(value: boolean): void {
  localMinimized.value = value
  emit('update:minimized', value)
}
</script>
