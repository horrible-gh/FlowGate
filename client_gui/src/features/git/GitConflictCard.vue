<template>
  <section class="gx-card" :class="{ danger: !(unsupported && graphMerge) }" data-test="git-conflict-card">
    <div class="gx-title">
      {{ t('gui.git.conflict.title_target', { target }) }}
    </div>
    <!-- (mockup v7 item 3): a card result's merge conflict is no dead end here — it is handled in the
         card; [카드로 이동] opens that card's '병합 · 충돌' tab. No resolve action is offered in this panel. -->
    <div v-if="unsupported && graphMerge" class="gx-card-handled" data-test="git-conflict-card-handled">
      <b>{{ t('gui.git.conflict.card_handled') }}</b>
      <span>{{ t('gui.git.conflict.card_handled_desc') }}</span>
      <div class="gx-card-handled-btns">
        <button type="button" class="gx-card-handled-go" data-test="git-conflict-open-card" @click="openCard">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14 M13 6l6 6-6 6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" /></svg>{{ t('gui.git.conflict.open_card') }}
        </button>
      </div>
    </div>
    <p v-else-if="unsupported" class="gx-desc" data-test="git-conflict-unsupported">{{ t('gui.git.conflict.unsupported') }}</p>
    <template v-else>
      <!-- (task 7): the numbers are the server's record (`resolved_paths`), not the files that
           merely show no marker (a file the resolver was refused on lost its markers but is not resolved). -->
      <p v-if="files && files.length" class="gx-desc" data-test="git-conflict-count">
        {{ t('gui.git.conflict.count', { open: openCount, done: files.length - openCount }) }}
      </p>
      <!-- (card B): files the resolution was submitted with besides the session's own (a file main moved). -->
      <div v-if="companions.length" class="gx-companions" data-test="git-conflict-companions">
        <div class="gx-kicker">{{ t('gui.git.conflict.companions') }}</div>
        <p v-for="item in companions" :key="item.path" class="gx-desc" data-test="git-conflict-companion">
          {{ item.sourcePath ? t('gui.git.conflict.companion_item', { path: item.path, source: item.sourcePath }) : item.path }}
        </p>
      </div>
      <div v-if="files && files.length" class="gx-files">
        <GitFileRow
          v-for="file in preview"
          :key="file.path"
          :path="file.path"
          :letter="file.resolved ? 'M' : 'C'"
          @select="openResolver(file.path)"
        />
        <button
          v-if="files.length > preview.length"
          type="button"
          class="gx-files-more"
          data-test="git-conflict-more"
          @click="openResolver(null)"
        >
          {{ t('gui.git.files_more', { n: files.length - preview.length }) }}
        </button>
      </div>
      <p v-else-if="session.state.conflictLoad === 'loading'" class="gx-desc">{{ t('gui.git.conflict.loading') }}</p>
      <p v-if="outcome" class="gx-reason" data-test="git-conflict-ai-outcome">{{ outcomeText }}</p>
      <div class="gx-actions">
        <div class="gx-ai-pick">
          <button
            ref="pickButton"
            type="button"
            class="gx-btn ai"
            :disabled="!actionable"
            :aria-expanded="pickerOpen ? 'true' : 'false'"
            aria-haspopup="dialog"
            :title="outcome ? t('gui.git.conflict.ai_other') : t('gui.git.conflict.ai')"
            :aria-label="outcome ? t('gui.git.conflict.ai_other') : t('gui.git.conflict.ai')"
            data-test="git-conflict-ai"
            @click="togglePicker"
          >
            ✦ ▾
          </button>
          <div v-if="pickerOpen" ref="picker" class="gx-popover" :style="pickerShift" role="dialog" :aria-label="t('gui.git.conflict.ai_pick')" data-test="git-conflict-ai-picker">
            <div class="gx-kicker">{{ t('gui.git.conflict.ai_pick') }}</div>
            <input v-if="providers.length > 8" v-model="providerSearch" class="gx-input" type="search" :aria-label="t('gui.git.conflict.ai_pick')" :placeholder="t('gui.git.conflict.ai_pick')" />
            <div v-if="providers.length" class="gx-pick-list" style="max-height:240px;overflow:auto">
              <label v-for="provider in filteredProviders" :key="provider.id" class="gx-pick-item" :data-selected="picked === provider.id">
                <input v-model="picked" type="radio" name="gx-conflict-ai" :value="provider.id" />
                <span>{{ provider.name }}</span>
                <small v-if="provider.id === session.defaultProviderId.value">{{ t('gui.git.conflict.ai_default') }}</small>
              </label>
            </div>
            <p v-else class="gx-reason">{{ session.state.providers ? t('gui.git.conflict.ai_none') : t('gui.git.conflict.ai_loading') }}</p>
            <div class="gx-actions">
              <button type="button" class="gx-btn primary" :disabled="!actionable || (providers.length > 0 && !picked)" data-test="git-conflict-ai-start" @click="start">
                {{ t('gui.git.conflict.ai_start') }}
              </button>
              <button type="button" class="gx-btn ghost" @click="pickerOpen = false">{{ t('gui.git.conflict.ai_close') }}</button>
            </div>
          </div>
        </div>
        <button type="button" class="gx-btn" :disabled="!actionable" :title="t('gui.git.conflict.manual')" :aria-label="t('gui.git.conflict.manual')" data-test="git-conflict-manual" @click="openResolver(null)">
          ◉
        </button>
        <button type="button" class="gx-btn ghost" :disabled="!actionable" :title="t('gui.git.conflict.abort')" :aria-label="t('gui.git.conflict.abort')" data-test="git-conflict-abort" @click="askAbort">
          ↶
        </button>
        <button v-if="canRebuild" type="button" class="gx-btn" :disabled="!actionable || !!worktree?.state.busy" :title="rebuildLabel" :aria-label="rebuildLabel" data-test="git-conflict-reconstruct" @click="rebuild">⟳</button>
        <!-- (task 3): the way out when git refuses the abort — administrators only, and only then. -->
        <button v-if="canForce" type="button" class="gx-btn danger" :disabled="!actionable" data-test="git-conflict-force" @click="askForceCleanup()">
          {{ t('gui.git.conflict.force_cleanup') }}
        </button>
      </div>
      <!-- (task 2): git's own reason and what to do next, not `git_error` alone. Shown from the
           session's record; a refusal just answered is the notice line below (the same text), so it is not said twice. -->
      <div v-if="failureShown" class="gx-reason gx-abort-failure" data-test="git-conflict-abort-failure">
        <span>{{ t('gui.git.conflict.abort_failure') }}</span>
        <code data-test="git-conflict-abort-reason">{{ abortFailure!.reason }}</code>
        <span data-test="git-conflict-abort-next">{{ t(isAdmin ? 'gui.git.conflict.abort_failure_admin' : 'gui.git.conflict.abort_failure_user') }}</span>
      </div>
      <p v-if="blockedText" class="gx-reason" data-test="git-conflict-blocked">{{ blockedText }}</p>
      <GitReapproveBlock />
    </template>
    <p v-if="noticeText" class="gx-notice" data-test="git-notice">{{ noticeText }}</p>
  </section>
</template>

<script setup lang="ts">
// merge conflict card. `unsupported` is the TR-revert (or
// unknown) conflict this screen does not process: title and one line, no buttons (§3.7).
// the one place a conflict is worked on: file states, a file
// opens the direct resolver, [AI 해결 ▾] picks the AI right under the button before it starts,
// an AI run that ended with conflicts left offers [다른 AI로 ▾] / [직접 해결], and
// the §B re-approval path sits below when a held approval is parked on this conflict.
import { computed, inject, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { GIT_WORKTREE_CONTEXT } from './gitWorktreeSession'
import { useI18n } from 'vue-i18n'
import GitFileRow from './GitFileRow.vue'
import GitReapproveBlock from './GitReapproveBlock.vue'
import { useAbortMergeConfirm } from './abortMerge'
import { FILE_PREVIEW, useGitPanel } from './gitSession'

defineProps<{ unsupported?: boolean }>()
const { t } = useI18n()
const ctx = useGitPanel()
const session = ctx.session
const worktree = inject(GIT_WORKTREE_CONTEXT, null)?.worktree
const rebuildLabel = computed(() => t('gui.git.header.reconstruct'))
const canRebuild = computed(() => !!worktree && !session.pendingRow.value?.finalApprovalBound && !graphMerge.value)
function rebuild(): void {
  const held = worktree?.ticket()
  const mergeId = session.pendingRow.value?.mergeId
  ctx.confirm({
    title: rebuildLabel.value,
    message: t('gui.git.header.reconstruct_message'),
    confirmLabel: t('gui.git.header.reconstruct'),
    run: () => {
      if (held && worktree?.isCurrent(held) && session.pendingRow.value?.mergeId === mergeId) void worktree.reconstruct()
    },
  })
}

const files = computed(() => session.state.conflict?.files ?? null)
// the conflicts answer of this row's merge says it is a Graph card result's merge.
const graphMerge = computed(() => {
  const conflict = session.state.conflict
  return !!conflict?.graphMerge && conflict.mergeId === session.pendingRow.value?.mergeId
})
/** Opens the card whose result this merge carries (the Designer finds it by the merge session id). */
function openCard(): void {
  const flowId = session.state.flow?.groupId
  const mergeId = session.state.conflict?.mergeId
  if (!flowId || typeof mergeId !== 'number') return
  window.dispatchEvent(new CustomEvent('fg:open_card_merge', { detail: { flowId, mergeId } }))
}
// Open files first, so the ones left are the ones shown. "Open" = the server has no resolution recorded for it.
const preview = computed(() => [...(files.value ?? [])].sort((a, b) => Number(!b.resolved) - Number(!a.resolved)).slice(0, FILE_PREVIEW))
const openCount = computed(() => (files.value ?? []).filter((f) => !f.resolved).length)
const companions = computed(() => session.state.conflict?.companionFiles ?? [])
const abortFailure = computed(() => session.state.conflict?.abortFailure ?? null)
const isAdmin = computed(() => ctx.isAdmin?.() === true)
const abortNotice = computed(() => session.state.notices.conflict?.code === 'abort_failed')
const failureShown = computed(() => !!abortFailure.value?.reason && !abortNotice.value)
const canForce = computed(() => isAdmin.value && (!!abortFailure.value || abortNotice.value))
const target = computed(() => {
  const conflict = session.state.conflict
  return conflict?.targetBranch ?? conflict?.baseBranch ?? session.state.status?.baseBranch ?? t('gui.git.header.base_unset')
})
// No action before the conflicts answer says this is an ordinary merge, not a TR conflict;
// readOnly / 403 / busy turn all three off, `직접 해결` included (0033-D §5.1).
const actionable = computed(() => session.canAct.value && session.conflictActionable.value && !worktree?.state.busy)
const blockedText = computed(() => {
  if (actionable.value) return null
  if (session.state.readOnly) return t('gui.git.conflict.blocked.read_only')
  if (session.state.forbidden) return t('gui.git.conflict.blocked.forbidden')
  if (session.state.busy) return t('gui.git.conflict.blocked.busy')
  return t('gui.git.conflict.loading')
})

// A source that only sends ids still offers them (the id is the name then).
const providers = computed(() => {
  const list = session.state.providers
  if (!list) return []
  return list.providers?.length ? list.providers : list.providerIds.map((id) => ({ id, name: id }))
})
const providerName = (id: string | null) => (id ? providers.value.find((p) => p.id === id)?.name ?? id : t('gui.git.conflict.ai_server_default'))
const outcome = computed(() => session.state.aiOutcome)
const outcomeText = computed(() => {
  const value = outcome.value
  if (!value) return ''
  return t('gui.git.conflict.ai_ended', { status: value.status ?? '—', name: providerName(value.providerId), n: value.remaining })
})

const noticeText = computed(() => {
  const notice = session.state.notices.conflict
  return notice ? t(`gui.git.notice.${notice.code}`, { ...(notice.params ?? {}) }) : null
})

const providerSearch = ref('')
const filteredProviders = computed(() => [...providers.value]
  .sort((a, b) => Number(b.id === session.preferredProviderId.value) - Number(a.id === session.preferredProviderId.value))
  .filter(p => p.name.toLowerCase().includes(providerSearch.value.toLowerCase())))
const pickerOpen = ref(false)
// Pulled left (arrow kept on the button) only when the panel edge would cut the popover.
const pickerShift = ref<Record<string, string>>({})
function fitPicker(): void {
  const node = picker.value
  if (!node) return
  const box = node.getBoundingClientRect()
  const edge = (node.closest('.designer-right-pane') ?? document.documentElement).getBoundingClientRect().right - 8
  const shift = Math.max(0, Math.round(box.right - edge))
  pickerShift.value = shift ? { transform: `translateX(-${shift}px)`, '--gx-arrow-x': `${16 + shift}px` } : {}
}
const picked = ref<string | null>(null)
const picker = ref<HTMLElement | null>(null)
const pickButton = ref<HTMLElement | null>(null)

function togglePicker(): void {
  if (pickerOpen.value) {
    pickerOpen.value = false
    return
  }
  session.ensureProviders()
  picked.value = session.preferredProviderId.value
  pickerOpen.value = true
}

// The list may answer after the popover opened: start on the preferred AI then.
watch(
  () => session.preferredProviderId.value,
  (id) => {
    if (pickerOpen.value && !picked.value) picked.value = id
  },
)

function start(): void {
  const providerId = providers.value.length ? picked.value : null
  pickerOpen.value = false
  void session.startAi('conflict', providerId)
}

// Outside click / Escape close the popover; it is anchored to its button, not a modal.
function onDocumentDown(event: MouseEvent): void {
  const node = event.target as Node | null
  if (!node || picker.value?.contains(node) || pickButton.value?.contains(node)) return
  pickerOpen.value = false
}
function onKey(event: KeyboardEvent): void {
  if (event.key === 'Escape') pickerOpen.value = false
}
watch(pickerOpen, (open) => {
  if (open) {
    pickerShift.value = {}
    void nextTick(() => {
      fitPicker()
      document.addEventListener('mousedown', onDocumentDown)
      document.addEventListener('keydown', onKey)
    })
  } else {
    document.removeEventListener('mousedown', onDocumentDown)
    document.removeEventListener('keydown', onKey)
  }
})
watch(actionable, (value) => {
  if (!value) pickerOpen.value = false
})
onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocumentDown)
  document.removeEventListener('keydown', onKey)
})

function openResolver(path: string | null): void {
  const first = path ?? (files.value ?? []).find((f) => !f.resolved)?.path ?? files.value?.[0]?.path ?? null
  session.openConflictWindow(t('gui.git.window.conflict_title'), first)
}

// [병합 중단] closes the conflict review and cancels the approval parked on it; the confirm text says what stays (§3.2).
const { askAbort, askForceCleanup } = useAbortMergeConfirm()
</script>

<style scoped>
.gx-abort-failure { display: flex; flex-direction: column; gap: 3px; }
.gx-abort-failure code { display: block; overflow-wrap: anywhere; font-size: 11.5px; }
.gx-companions { display: flex; flex-direction: column; gap: 2px; margin-top: 6px; }
/* flowgate.v02test.0042 (mockup v7 item 3): a card result's conflict is a blue note with [→ 카드로 이동], never a dead end. */
.gx-card-handled { display: flex; flex-direction: column; gap: 3px; margin-top: 6px; padding: 10px 12px; border: 1px solid rgba(75, 163, 255, 0.4); border-radius: 8px; background: rgba(75, 163, 255, 0.1); }
.gx-card-handled b { color: #7cc0ff; font-size: 12.5px; }
.gx-card-handled span { color: #c0d0e2; font-size: 12px; line-height: 1.5; }
.gx-card-handled-btns { display: flex; margin-top: 8px; }
.gx-card-handled-go { display: inline-flex; align-items: center; gap: 6px; height: 24px; padding: 0 8px; border: 1px solid #2b4566; border-radius: 7px; background: #13263d; color: #dbe8f6; font: inherit; font-size: 11.5px; font-weight: 700; cursor: pointer; }
.gx-card-handled-go svg { width: 15px; height: 15px; }
.gx-card-handled-go:hover { filter: brightness(1.15); }
</style>
