<template>
  <ModelessShell
    ref="shell"
    :open="true"
    :initial-x="win.x"
    :initial-y="win.y"
    :initial-width="620"
    :initial-height="480"
    :min-width="360"
    :min-height="240"
    :minimized="win.minimized"
    @update:minimized="(value: boolean) => setSlotWriteWindowMinimized(win.key, value)"
    @close="closeSlotWriteWindow(win.key)"
  >
    <template #header>
      <span class="slot-write-heading" data-test="slot-write-heading">
        <span class="slot-write-kind">{{ t(k('title'), { name: request.name }) }}</span>
        <span class="slot-write-card" :title="request.cardTitle" data-test="slot-write-card">{{ request.cardTitle }}</span>
      </span>
    </template>

    <div class="slot-write" data-test="slot-write-window" :data-key="win.key" :data-slot="request.slot">
      <p v-if="contentless" class="slot-write-note" data-test="slot-write-contentless">{{ t(k('contentless')) }}</p>
      <template v-else>
        <p v-if="request.nodeRunning" class="slot-write-note slot-write-note--info" data-test="slot-write-result-note">{{ t(k('result_note')) }}</p>
        <textarea
          v-model="text"
          class="slot-write-textarea"
          :class="{ 'is-invalid': fieldText !== '' }"
          spellcheck="false"
          :aria-label="t(k('editor'))"
          :placeholder="t(k('placeholder'))"
          :disabled="saving || existing"
          data-test="slot-write-textarea"
          @keydown.ctrl.s.prevent="save"
          @keydown.meta.s.prevent="save"
        />
        <p v-if="fieldText" class="slot-write-field-error" data-test="slot-write-field-error" :data-key="failure?.key ?? problemKey">{{ fieldText }}</p>
      </template>
      <p
        v-if="noticeText"
        class="slot-write-notice"
        :class="{ 'is-existing': existing }"
        data-test="slot-write-notice"
        :data-key="failure?.key"
      >{{ noticeText }}</p>
      <div class="slot-write-bar">
        <Button size="sm" variant="ghost" :disabled="saving" data-test="slot-write-cancel" @click="closeSlotWriteWindow(win.key)">{{ t(k(existing ? 'close' : 'cancel')) }}</Button>
        <Button
          size="sm"
          variant="primary"
          :disabled="existing || problemKey !== null"
          :loading="saving"
          data-test="slot-write-save"
          @click="save"
        >{{ t(k(contentless ? 'create' : 'save')) }}</Button>
      </div>
    </div>
  </ModelessShell>
</template>

<script setup lang="ts">
// flowgate.v02.0056 F2-2 — [작성] of a card line without a document (O3 0009-TR §4-2): an editor window on
// ModelessShell (the document windows' look). Nothing is made until [저장]: then the line's document is written
// (cardLineWrite — the body after the server's front matter; a blank text makes an empty draft, a work plan line
// is made as an empty plan with no text). After the 201 the window closes, the answer's line stands for the
// card's line until the Canvas read shows it, and the opener reads the Canvas again (a result bound to a running
// card turns the card to 승인 대기 there — the F2-1 card state).
// A refusal that means the line has a document now (409 slot_has_document · node_token_unavailable) reads the
// Canvas again and opens that document; the window keeps the text (to copy) and can only be closed. Waiting
// refusals (node_in_instruction_stage · node_ai_working) and the rest are told in the window, the content
// refusals under the text; the server's message (x-locale) follows the code's words. Display only: the call and
// its rules are useSlotWrite's.
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '@/components/ui/Button.vue'
import ModelessShell from '@/components/ui/ModelessShell.vue'
import type { SlotWriteFailure } from './slotWriteModel'
import { closeSlotWriteWindow, setSlotWriteWindowMinimized, type SlotWriteWindowState } from './slotWriteWindows'
import { useSlotWrite } from './useSlotWrite'

const props = defineProps<{
  win: SlotWriteWindowState
}>()

const { t } = useI18n()
const k = (key: string) => `gui.document_window.write.${key}`

const shell = ref<InstanceType<typeof ModelessShell> | null>(null)

const { request, contentless, text, saving, failure, existing, problemKey, save } = useSlotWrite(() => props.win)

/** The code's words, then the server's message when it says more. */
function failureText(item: SlotWriteFailure): string {
  const words = t(k(`failure.${item.key}`))
  return item.message ? `${words} — ${item.message}` : words
}

const fieldText = computed(() => {
  if (failure.value?.kind === 'field') return failureText(failure.value)
  return problemKey.value ? t(k(`failure.${problemKey.value}`)) : ''
})

const noticeText = computed(() => (failure.value && failure.value.kind !== 'field' ? failureText(failure.value) : ''))

// A second [작성] on the same line brings the window back to the front.
watch(
  () => props.win.raise,
  () => shell.value?.bringToFront(),
)
</script>

<style scoped>
.slot-write-heading {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  max-width: 100%;
}

.slot-write-kind {
  flex: 0 0 auto;
}

.slot-write-card {
  overflow: hidden;
  color: var(--fg2-muted);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* The shell body pads and scrolls; the window fills it so the text owns its scroll. */
.slot-write {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  margin: -12px -14px;
}

.slot-write-note {
  margin: 0;
  padding: 10px 18px;
  border-bottom: 1px solid var(--fg2-line);
  color: var(--fg2-muted);
  font-size: 12.5px;
}

.slot-write-note--info {
  color: var(--fg2-status-info-fg);
}

.slot-write-textarea {
  flex: 1;
  min-height: 0;
  margin: 0;
  padding: 14px 18px;
  border: none;
  border-bottom: 1px solid var(--fg2-line);
  outline: none;
  resize: none;
  background: var(--fg2-bg);
  color: var(--fg2-text);
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 12.5px;
  line-height: 1.6;
  tab-size: 2;
}

.slot-write-textarea:focus-visible {
  box-shadow: inset 0 0 0 1px var(--fg2-focus);
}

.slot-write-textarea.is-invalid {
  box-shadow: inset 0 0 0 1px var(--fg2-status-danger-border);
}

.slot-write-field-error,
.slot-write-notice {
  margin: 0;
  padding: 8px 18px 0;
  color: var(--fg2-status-danger-fg);
  font-size: 12px;
}

.slot-write-notice.is-existing {
  color: var(--fg2-status-warning-fg);
}

.slot-write-bar {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  margin-top: auto;
  padding: 8px 12px;
}
</style>
