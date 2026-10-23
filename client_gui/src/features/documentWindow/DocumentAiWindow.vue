<template>
  <ModelessShell
    ref="shell"
    :open="true"
    :initial-x="win.x"
    :initial-y="win.y"
    :initial-width="480"
    :initial-height="440"
    :min-width="340"
    :min-height="260"
    :minimized="win.minimized"
    @update:minimized="(value: boolean) => setDocumentAiWindowMinimized(win.docId, value)"
    @close="closeDocumentAiWindow(win.docId)"
  >
    <template #header>
      <span class="document-ai-heading" data-test="document-ai-heading">
        <span class="document-ai-kind">{{ t(k('title')) }}</span>
        <span class="document-ai-doc" :title="docTitle" data-test="document-ai-doc">{{ docTitle }}</span>
      </span>
    </template>

    <div class="document-ai" data-test="document-ai-window" :data-doc="win.docId" :data-phase="phase">
      <label class="document-ai-field">
        <span class="document-ai-label">{{ t(k('provider')) }}</span>
        <Select
          v-model="providerId"
          :options="providerOptions"
          :placeholder="t(k(choicesState === 'loading' ? 'provider_loading' : 'provider_pick'))"
          :disabled="phase === 'starting' || phase === 'running' || choicesState !== 'ready'"
          data-test="document-ai-provider"
        />
      </label>
      <p v-if="choicesState === 'error'" class="document-ai-line is-danger" data-test="document-ai-provider-error">{{ t(k('provider_error')) }}</p>

      <label class="document-ai-field document-ai-field--grow">
        <span class="document-ai-label">{{ t(k('opinion')) }}</span>
        <Textarea
          v-model="opinion"
          class="document-ai-opinion"
          :rows="5"
          :placeholder="t(k('opinion_placeholder'))"
          :disabled="phase === 'starting' || phase === 'running'"
          data-test="document-ai-opinion"
        />
      </label>
      <p class="document-ai-hint" data-test="document-ai-hint">{{ t(k('hint')) }}</p>

      <p v-if="otherRun" class="document-ai-line is-warning" data-test="document-ai-busy">{{ t(k(otherRun)) }}</p>
      <p v-if="phase === 'running'" class="document-ai-line is-running" data-test="document-ai-status" data-phase="running">{{ t(k('running'), { id: run?.runId ?? '' }) }}</p>
      <p v-else-if="phase === 'finished'" class="document-ai-line" data-test="document-ai-status" data-phase="finished">
        {{ t(k('finished'), { reason: run?.endReason ?? '-' }) }}
      </p>
      <p v-if="failureText" class="document-ai-line is-danger" data-test="document-ai-failure" :data-key="failure?.key">{{ failureText }}</p>

      <div class="document-ai-bar">
        <Button size="sm" variant="ghost" data-test="document-ai-close" @click="closeDocumentAiWindow(win.docId)">{{ t(k('close')) }}</Button>
        <Button
          size="sm"
          variant="primary"
          :disabled="!runnable"
          :loading="phase === 'starting'"
          data-test="document-ai-run"
          @click="start"
        >{{ t(k('run')) }}</Button>
      </div>
    </div>
  </ModelessShell>
</template>

<script setup lang="ts">
// flowgate.v02.0056 F2-2 — the single AI call on one document (O3 0009-TR §4-3) on ModelessShell, opened from
// [AI 호출] of a document line. The AI is picked from the project's AIs (settingsChoicesRead — the settings
// tab's read; the project's default first), the opinion is written, and only [실행] calls the AI:
// POST /ai-invoke/start { action_scope: 'edit', mode: 'single', provider_id, provider_pinned: true, messages:
// [opinion] } with no review loop — no review · rework round and no card approval follow, and the window says
// so. The card's memos marked [AI가 참조] go with it (the server adds them).
// The run is followed until it ends (documentAiWindows: the run read and the group's live run), here and on the
// document's line. 409 run_in_progress shows the server's message as it is (the v0.1 envelope). Display only: the
// calls and their rules are useDocumentAi's.
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '@/components/ui/Button.vue'
import ModelessShell from '@/components/ui/ModelessShell.vue'
import Select, { type SelectOption } from '@/components/ui/Select.vue'
import Textarea from '@/components/ui/Textarea.vue'
import { closeDocumentAiWindow, setDocumentAiWindowMinimized, type DocumentAiWindowState } from './documentAiWindows'
import { lineKindName } from './lineKindName'
import { useDocumentAi } from './useDocumentAi'

const props = defineProps<{
  win: DocumentAiWindowState
}>()

const { t, te } = useI18n()
const k = (key: string) => `gui.document_window.ai.${key}`

const shell = ref<InstanceType<typeof ModelessShell> | null>(null)

const request = computed(() => props.win.request)
// flowgate.v02.0088 F15: only the kind follows the language (lineKindName); the document title is shown as stored.
const docTitle = computed(() => [lineKindName(request.value, t, te), request.value.title || props.win.docId].filter(Boolean).join(' · '))

const { providers, choicesState, providerId, opinion, run, phase, runnable, otherRun, failure, readChoices, start } = useDocumentAi(() => props.win)

const providerOptions = computed<SelectOption[]>(() => providers.value.map((provider) => ({ value: provider.id, label: provider.name })))

/** run_in_progress: the server's message as it is; else the code's words with the server's message after them. */
const failureText = computed(() => {
  const item = failure.value
  if (!item) return ''
  if (item.verbatim) return item.message
  const words = t(k(`failure.${item.key}`))
  return item.message ? `${words} — ${item.message}` : words
})

// A second [AI 호출] on the same document brings the window back to the front.
watch(
  () => props.win.raise,
  () => shell.value?.bringToFront(),
)

onMounted(() => {
  void readChoices()
})
</script>

<style scoped>
.document-ai-heading {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  max-width: 100%;
}

.document-ai-kind {
  flex: 0 0 auto;
}

.document-ai-doc {
  overflow: hidden;
  color: var(--fg2-muted);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.document-ai {
  display: flex;
  flex-direction: column;
  gap: 10px;
  height: 100%;
  min-height: 0;
}

.document-ai-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.document-ai-field--grow {
  flex: 1;
  min-height: 0;
}

.document-ai-opinion {
  flex: 1;
  min-height: 72px;
}

.document-ai-label {
  color: var(--fg2-muted);
  font-size: 12px;
  font-weight: 650;
}

.document-ai-hint,
.document-ai-line {
  margin: 0;
  color: var(--fg2-muted);
  font-size: 12px;
}

.document-ai-line.is-running {
  color: var(--fg2-status-info-fg);
}

.document-ai-line.is-warning {
  color: var(--fg2-status-warning-fg);
}

.document-ai-line.is-danger {
  color: var(--fg2-status-danger-fg);
}

.document-ai-bar {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  margin-top: auto;
}
</style>
