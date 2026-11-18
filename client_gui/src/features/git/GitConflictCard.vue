<template>
  <section class="gx-card" :class="{ danger: !(unsupported && graphMerge) }" data-test="git-conflict-card">
    <div class="gx-title">
      {{ t('gui.git.conflict.title_target', { target }) }}
    </div>
    <!-- flowgate.v02test.0042 (시안 v7 item 3): a card result's merge conflict is no dead end here — it is handled in the
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
      <p v-if="files && files.length" class="gx-desc" data-test="git-conflict-count">
        {{ t('gui.git.conflict.count', { open: openCount, done: files.length - openCount }) }}
      </p>
      <div v-if="files && files.length" class="gx-files">
        <GitFileRow
          v-for="file in preview"
          :key="file.path"
          :path="file.path"
          :letter="file.conflictCount === 0 ? 'M' : 'C'"
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
        <button
          v-if="outcome"
          type="button"
          class="gx-btn ai"
          :disabled="!actionable"
          data-test="git-conflict-ai-again"
          @click="session.startAi('conflict', outcome.providerId)"
        >
          {{ t('gui.git.conflict.ai_again') }}
        </button>
        <div class="gx-ai-pick">
          <button
            ref="pickButton"
            type="button"
            class="gx-btn ai"
            :disabled="!actionable"
            :aria-expanded="pickerOpen ? 'true' : 'false'"
            aria-haspopup="dialog"
            data-test="git-conflict-ai"
            @click="togglePicker"
          >
            {{ outcome ? t('gui.git.conflict.ai_other') : t('gui.git.conflict.ai') }} ▾
          </button>
          <div v-if="pickerOpen" ref="picker" class="gx-popover" :style="pickerShift" role="dialog" :aria-label="t('gui.git.conflict.ai_pick')" data-test="git-conflict-ai-picker">
            <div class="gx-kicker">{{ t('gui.git.conflict.ai_pick') }}</div>
            <div v-if="providers.length" class="gx-pick-list">
              <label v-for="provider in providers" :key="provider.id" class="gx-pick-item" :data-selected="picked === provider.id">
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
        <button type="button" class="gx-btn" :disabled="!actionable" data-test="git-conflict-manual" @click="openResolver(null)">
          {{ t('gui.git.conflict.manual') }}
        </button>
        <button type="button" class="gx-btn ghost" :disabled="!actionable" data-test="git-conflict-abort" @click="askAbort">
          {{ t('gui.git.conflict.abort') }}
        </button>
      </div>
      <p v-if="blockedText" class="gx-reason" data-test="git-conflict-blocked">{{ blockedText }}</p>
      <GitReapproveBlock />
    </template>
    <p v-if="noticeText" class="gx-notice" data-test="git-notice">{{ noticeText }}</p>
  </section>
</template>

<script setup lang="ts">
// flowgate.v02.0033 — merge conflict card (0033-D §3.2). `unsupported` is the TR-revert (or
// unknown) conflict this screen does not process: title and one line, no buttons (§3.7).
// flowgate.v02gui.0004 (0001-B §A) — the one place a conflict is worked on: file states, a file
// opens the direct resolver, [AI 해결 ▾] picks the AI right under the button before it starts,
// an AI run that ended with conflicts left offers [다시 실행] / [다른 AI로 ▾] / [직접 해결], and
// the §B re-approval path sits below when a held approval is parked on this conflict.
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import GitFileRow from './GitFileRow.vue'
import GitReapproveBlock from './GitReapproveBlock.vue'
import { FILE_PREVIEW, useGitPanel } from './gitSession'

defineProps<{ unsupported?: boolean }>()
const { t } = useI18n()
const ctx = useGitPanel()
const session = ctx.session

const files = computed(() => session.state.conflict?.files ?? null)
// flowgate.v02test.0042: the conflicts answer of this row's merge says it is a Graph card result's merge.
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
// Open files first, so the ones left are the ones shown.
const preview = computed(() => [...(files.value ?? [])].sort((a, b) => Number(b.conflictCount > 0) - Number(a.conflictCount > 0)).slice(0, FILE_PREVIEW))
const openCount = computed(() => (files.value ?? []).filter((f) => f.conflictCount > 0).length)
const target = computed(() => {
  const conflict = session.state.conflict
  return conflict?.targetBranch ?? conflict?.baseBranch ?? session.state.status?.baseBranch ?? t('gui.git.header.base_unset')
})
// No action before the conflicts answer says this is an ordinary merge, not a TR conflict;
// readOnly / 403 / busy turn all three off, `직접 해결` included (0033-D §5.1).
const actionable = computed(() => session.canAct.value && session.conflictActionable.value)
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
  const first = path ?? (files.value ?? []).find((f) => f.conflictCount > 0)?.path ?? files.value?.[0]?.path ?? null
  session.openConflictWindow(t('gui.git.window.conflict_title'), first)
}

// Abort drops the final approval intent with it; the confirm text says so (§3.2).
function askAbort(): void {
  const bound = session.pendingRow.value?.finalApprovalBound === true
  ctx.confirm({
    title: t('gui.git.conflict.abort_title'),
    message: bound ? `${t('gui.git.conflict.abort_message')} ${t('gui.git.conflict.abort_bound')}` : t('gui.git.conflict.abort_message'),
    confirmLabel: t('gui.git.conflict.abort'),
    danger: true,
    run: () => void session.abortMerge(),
  })
}
</script>

<style scoped>
/* flowgate.v02test.0042 (시안 v7 item 3): a card result's conflict is a blue note with [→ 카드로 이동], never a dead end. */
.gx-card-handled { display: flex; flex-direction: column; gap: 3px; margin-top: 6px; padding: 10px 12px; border: 1px solid rgba(75, 163, 255, 0.4); border-radius: 8px; background: rgba(75, 163, 255, 0.1); }
.gx-card-handled b { color: #7cc0ff; font-size: 12.5px; }
.gx-card-handled span { color: #c0d0e2; font-size: 12px; line-height: 1.5; }
.gx-card-handled-btns { display: flex; margin-top: 8px; }
.gx-card-handled-go { display: inline-flex; align-items: center; gap: 6px; height: 24px; padding: 0 8px; border: 1px solid #2b4566; border-radius: 7px; background: #13263d; color: #dbe8f6; font: inherit; font-size: 11.5px; font-weight: 700; cursor: pointer; }
.gx-card-handled-go svg { width: 15px; height: 15px; }
.gx-card-handled-go:hover { filter: brightness(1.15); }
</style>
